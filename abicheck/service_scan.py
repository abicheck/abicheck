# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Per-project cost estimate for one comparison operand, plus header expansion.

ADR-068 Phase 4 (the typed-API slice) retired this module's own request and
result types. :class:`ScanRequest`/:class:`ScanResult` (and their
``--artifact-set`` siblings) are gone: a comparison's typed input is
:class:`~abicheck.api_types.CompareRequest`, its typed output
:class:`~abicheck.api_types.CompareResult`, and there is exactly one of each.
What remains here is the ADR-035 D10 dry-run *cost model* --
:func:`estimate_scan` projects the per-layer cost of resolving one
:class:`~abicheck.api_types.InputSpec` at a given evidence depth, without
running a compiler or parsing a binary -- and the header-input expansion
helpers the extractors share.

A leaf module (must not import :mod:`abicheck.service`); its header-expansion
helper (:func:`expand_header_inputs`) is re-exported by ``service`` for
backward compatibility.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .buildsource.build_query import PRUNED_HEADER_DIR_SEGMENTS
from .compile_context import CompileContext as CompileContext  # re-exported, ADR-055 D1

# pair_wide_cxx20_std_override lives in the leaf `cxx20_pair_dialect` module
# (moved for line-budget room, see that module's own docstring), re-exported
# here so every existing `from .service_scan import pair_wide_cxx20_std_override`
# call site is unaffected.
from .cxx20_pair_dialect import (
    pair_wide_cxx20_std_override as pair_wide_cxx20_std_override,
)
from .errors import ValidationError
from .header_utils import HEADER_SUFFIXES, iter_directory_headers

if TYPE_CHECKING:
    from .model.evidence_depth_levels import EvidenceDepth, SourceMethod
    from .workflows.request_inputs import InputSpec

_logger = logging.getLogger(__name__)

# Header file extensions recognised during directory expansion. Shared with the
# AST-cache include walk (dumper._cache_key) via the leaf header_utils module so
# expansion and cache-invalidation can never drift (Codex review).
_HEADER_EXTS = HEADER_SUFFIXES

# Directory segments never scanned for headers — VCS metadata plus abicheck's own
# in-tree cmake build dir; see build_query.PRUNED_HEADER_DIR_SEGMENTS (the shared
# single source of truth, also used by cli_resolve._expand_header_inputs).
_PRUNED_DIR_SEGMENTS = PRUNED_HEADER_DIR_SEGMENTS


def expand_header_inputs(inputs: list[Path]) -> list[Path]:
    """Expand header inputs where each item can be a file or a directory.

    Directories are scanned recursively for known header extensions.

    Raises:
        ValidationError: If a path does not exist or a header directory is empty.
    """
    out: list[Path] = []
    for p in inputs:
        if not p.exists():
            raise ValidationError(f"Header file not found or not a file: {p}")
        if p.is_file():
            out.append(p)
            continue
        if p.is_dir():
            found = iter_directory_headers(p, _PRUNED_DIR_SEGMENTS)
            if not found:
                raise ValidationError(
                    f"Header directory contains no supported header files: {p}"
                )
            out.extend(found)
            continue
        raise ValidationError(f"Header path is neither file nor directory: {p}")

    # Deduplicate while preserving deterministic order
    seen: set[str] = set()
    deduped: list[Path] = []
    for h in out:
        k = str(h.resolve())
        if k in seen:
            continue
        seen.add(k)
        deduped.append(h)
    return deduped


def expand_public_header_inputs(headers: Iterable[Path]) -> list[str]:
    """Best-effort :func:`expand_header_inputs`, as ``str`` paths.

    The public-header-root variant: a ``-H``/``--public-header-dir`` entry may
    name a directory, and two consumers need the individual header *files* out
    of it rather than the directory as one entry -- the S2 leak pass (so clang
    preprocesses each header instead of a directory as one bogus TU) and L4
    replay's install-tree-vs-build-tree mirror detection
    (``clang_public_roots._equivalent_public_roots_for_unit``, whose promotion
    rule needs two sampled matches for a directory root but only one for a file
    root, so an un-expanded directory silently loses a real mirror).

    Unlike :func:`expand_header_inputs` this never raises: an empty or missing
    directory degrades to the raw paths, because both consumers are advisory
    enrichment on top of a snapshot that has already been built.

    Lives here, in the engine layer, rather than in ``cli_scan_baseline`` where
    it started, so ``service_input_resolution.embed_side_build_source`` can
    reach it without an engine-imports-CLI edge (CLI cleanup phase two, PR 3A --
    the migration that routed ``scan``'s candidate resolution through that
    shared primitive). ``cli_scan_baseline._expand_public_headers`` is now a
    thin delegate, so there is one implementation rather than two.
    """
    hdrs = list(headers)
    try:
        return [str(p) for p in expand_header_inputs(hdrs)]
    except Exception:  # noqa: BLE001 - expansion is best-effort for these tiers
        return [str(h) for h in hdrs]


# ── Scan service: typed request/result + per-project cost estimate ───────────
# See the module docstring above for the contract. ``estimate_scan`` is a
# first-class **dry-run** (ADR-035 D10): it probes the project (TU count,
# header fan-out, cache state) and returns the projected cost of each L-layer
# so a maintainer can pick a depth on measured cost, not guesswork — it scans
# nothing and runs no compiler.


def _scan_imports() -> tuple[Any, ...]:
    """Lazily import the evidence-depth vocabulary (keeps import cheap)."""
    from .model.evidence_depth_levels import (
        EvidenceDepth,
        ScanMode,
        SourceMethod,
        SourceScope,
        level_to_collect_mode,
        parse_user_depth,
        resolve_level,
    )

    return (
        EvidenceDepth,
        ScanMode,
        SourceMethod,
        level_to_collect_mode,
        resolve_level,
        parse_user_depth,
        SourceScope,
    )


@dataclass(frozen=True)
class CostEstimate:
    """Projected cost of one L-layer for *this* project (ADR-035 D10 dry-run)."""

    method: str | None  # S-axis (s0..s6) producing it; None for intrinsic L0-L2
    layer: str  # L-axis it populates (L0_binary..L5_source_graph)
    tus: int  # translation units this layer would touch
    est_seconds: float  # projected wall-clock for *this* project
    cache_hit_rate: float  # 0..1 fraction expected to hit the per-TU cache
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "layer": self.layer,
            "tus": self.tus,
            "est_seconds": round(self.est_seconds, 3),
            "cache_hit_rate": round(self.cache_hit_rate, 3),
            "note": self.note,
        }


# Codex review: TU counts here are workspace-wide (a pre-captured Bazel aquery/cquery jsonproto is never filtered
# by `targets` -- BazelAdapter only scopes a *live* query), so a `--build-target` run's real count is typically
# lower. Baked into each row's `note` so a Python-API caller sees it too.
_UNSCOPED_TU_NOTE_SUFFIX = (
    " [UNSCOPED: --build-target given, but this TU count is workspace-wide -- "
    "the real run's Bazel collection scopes to the requested root target(s) "
    "and typically touches fewer TUs]"
)
# L4/L5 derive their counts from L3's -- inherit its "[UNKNOWN" state too.
_TU_UNKNOWN_NOTE_SUFFIX = (
    " [UNKNOWN: derived from an unknown L3 TU count, see L3_build note]"
)

#: Per-TU / per-file cost anchors (seconds) for the dry-run estimate. These are deliberately coarse starting
#: defaults (§11 of the ADR-035 proposal: a full ``-fsyntax-only`` pass dominates; pattern/compile-DB scans are
#: <1-5%). The real per-project number comes from the actual run; the estimate only ranks layers so a maintainer
#: can pick a depth.
_COST_PER_HEADER_PARSE = 0.08  # L2 base: castxml/clang startup + preprocess per header
#: L2 marginal cost per KB of header text. A flat per-header anchor priced a one-line shim and a 200 KB templated
#: umbrella (ICU/hdf5) identically, under-ranking a large public surface. Weighting by on-disk size ranks heavy
#: headers above trivial ones (field-eval P1: ICU/HDF5 scans took 80-180 s while the estimate read flat).
_COST_PER_HEADER_KB = 0.004
_COST_PER_TU_BUILD = 0.002  # L3 compile-DB entry parse
# Cold L4 is a full clang JSON-AST replay + Python JSON parse + macro pass per TU. Real-world pvxs/oneDAL
# validation showed ~7.5s/TU cold; the old 0.45s/TU anchor under-promised source/full scans by an order of
# magnitude, making 100+ TU runs look like one-minute jobs. Warm cache is reported by live coverage; dry-run
# stays conservative absent a future cache probe.
_COST_PER_TU_REPLAY = 7.5  # L4 per-TU semantic AST replay, cold-cache default
_COST_PER_TU_GRAPH = 0.02  # L5 per-TU graph fold/edge


#: A flat size-based estimate prices a one-line ``#include`` umbrella and a heavily-templated header identically
#: per KB — real-world field evidence (the SVS ``datatype.h``/``float16.h``/``meta.h`` trio) showed a dry-run
#: estimate of 0.51s for headers whose actual parse ran over 15,000s before an external SIGKILL: that cost comes
#: from template/include fan-out, not on-disk bytes. These are a cheap, local (no compiler invocation) peek for
#: that signal so the estimate can flag it instead of a falsely precise number.
_COMPLEXITY_PEEK_BYTES = 512 * 1024
_COMPLEXITY_INCLUDE_THRESHOLD = 8  # local #include lines
_COMPLEXITY_TEMPLATE_THRESHOLD = 5  # template/concept/requires/SFINAE hits
_COMPLEXITY_MULTIPLIER = 25.0  # conservative floor-raising factor, not a promise
_INCLUDE_RE = re.compile(rb"^[ \t]*#[ \t]*include\b", re.MULTILINE)
_TEMPLATE_RE = re.compile(
    rb"\btemplate[ \t]*<|\bconcept\b|\brequires\b|\benable_if\b|\bconstexpr\b"
)


def _header_complexity_signal(path: Path) -> bool:
    """Best-effort local peek: many ``#include``s or heavy template/concept/
    constexpr usage in *path* signals a header a flat size-based estimate
    under-prices (template instantiation isn't linear in bytes -- the SVS
    case had small headers and near-unbounded real parse time). Never
    raises; unreadable is treated as unknown risk, matching the size
    estimate's own fallback.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(_COMPLEXITY_PEEK_BYTES)
    except OSError:
        return False
    return (
        len(_INCLUDE_RE.findall(data)) >= _COMPLEXITY_INCLUDE_THRESHOLD
        or len(_TEMPLATE_RE.findall(data)) >= _COMPLEXITY_TEMPLATE_THRESHOLD
    )


def _estimate_header_seconds(headers: list[Path]) -> tuple[float, bool]:
    """Size-aware L2 cost, plus a complexity-risk flag. Returns ``(seconds,
    high_risk)``: *seconds* is a fixed per-header base plus a per-KB term,
    inflated by :data:`_COMPLEXITY_MULTIPLIER` for any header tripping
    :func:`_header_complexity_signal` (so a small-but-pathological header
    doesn't price as a fraction of a second); *high_risk* flags that case so
    callers surface an explicit caveat instead of a falsely precise number
    (P0 SVS field report). Falls back to the base alone when a path can't
    be stat'd, so the estimate never raises mid-dry-run.
    """
    total = 0.0
    high_risk = False
    for h in headers:
        cost = _COST_PER_HEADER_PARSE
        try:
            cost += _COST_PER_HEADER_KB * (h.stat().st_size / 1024.0)
        except OSError:
            pass
        if _header_complexity_signal(h):
            high_risk = True
            cost *= _COMPLEXITY_MULTIPLIER
        total += cost
    return total, high_risk


def _count_compile_db_tus(compile_db: Path) -> int:
    """Count unique translation units in a ``compile_commands.json`` (0 on error)."""
    import json as _json

    try:
        raw = _json.loads(compile_db.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(raw, list):
        return 0
    # Deduplicate on the *resolved* path (file joined with its entry directory): a compile DB commonly stores
    # relative `file` names under different `directory` entries, so two distinct TUs (/proj/a/main.cpp,
    # /proj/b/main.cpp) both read as a bare `main.cpp` and would collapse to one — undercounting the TUs the real
    # scan (which normalizes via load_compile_db) replays (Codex).
    import os.path as _osp

    files: set[str] = set()
    for e in raw:
        if not (isinstance(e, dict) and e.get("file")):
            continue
        f = str(e["file"])
        directory = str(e.get("directory") or "")
        resolved = (
            _osp.normpath(_osp.join(directory, f)) if directory else _osp.normpath(f)
        )
        files.add(resolved)
    return len(files)


#: Source-file extensions counted as translation units when no compile DB exists.
_SOURCE_TU_EXTS = frozenset({".c", ".cc", ".cpp", ".cxx", ".c++", ".m", ".mm"})


def _is_source_tu_path(path: str) -> bool:
    """Whether a changed path is a compilable translation unit (a ``.cpp`` etc.)."""
    return Path(path).suffix.lower() in _SOURCE_TU_EXTS


def _is_header_path(path: str) -> bool:
    """Whether a changed path is a header (a change that fans out to many TUs).

    Delegates to the L4 replay selector's own header predicate so the estimate
    agrees with what the real scan does — notably inline/template headers
    (``.inl``/``.tcc``/``.ipp``) which the selector treats as headers (fan out to
    all TUs without an include graph) but ``service._HEADER_EXTS`` omits (Codex
    review).
    """
    from .buildsource.source_replay import _looks_like_header

    return _looks_like_header(path)


def _count_source_tus(sources: Path) -> int:
    """Count source translation units under a tree (compile-DB-free fallback)."""
    if sources.is_file():
        return 1 if sources.suffix.lower() in _SOURCE_TU_EXTS else 0
    n = 0
    for p in sources.rglob("*"):
        if p.is_file() and p.suffix.lower() in _SOURCE_TU_EXTS:
            n += 1
    return n


def _compile_db_in(root: Path) -> Path | None:
    """The ``compile_commands.json`` inside a build/source *directory*, if any.

    Reuses the *execution* path's discovery (``inline._find_compile_db_in_dir``:
    the conventional build-dir hints **plus** the depth-1 ``*/compile_commands.json``
    glob fallback) so ``scan --estimate`` mirrors what the real scan collects — a
    DB in a non-hint immediate subdirectory such as ``cmake-build-debug-gcc/`` is
    priced, not reported as absent / 0 TUs (Codex review).
    """
    from .buildsource.inline import _find_compile_db_in_dir

    return _find_compile_db_in_dir(root)


def _discover_compile_db(sources: Path | None, explicit: Path | None) -> Path | None:
    """The compile DB to estimate against: explicit wins, else discover in
    *sources*. An explicit ``--compile-db``/``--build-info`` pointing at a
    *directory* (e.g. ``build/`` holding a ``compile_commands.json``) is
    resolved to the contained DB -- else it flows into
    :func:`_count_compile_db_tus`, which fails the read and reports 0 TUs,
    making L3/L4/L5 near-free even though the real scan replays it (Codex
    review).
    """
    if explicit is not None and explicit.exists():
        if explicit.is_dir():
            found = _compile_db_in(explicit)
            if found is not None:
                return found
            # A build dir with no DB at the well-known spots: fall through to the
            # source-tree discovery rather than returning the unreadable dir.
        else:
            return explicit
    if sources is not None and sources.is_dir():
        return _compile_db_in(sources)
    return None


def _count_pack_tus(path: Path) -> int | None:
    """TU count of a build-source pack dir, or ``None`` if not a pack.

    The real scan loads a pack dir (``is_pack_dir``) and uses its embedded
    ``build_evidence``; the estimate mirrors that so a pack-only ``--build-info``
    does not report 0 TUs (Codex review). Best-effort: any load failure → ``None``
    so the caller falls back to compile-DB / source-tree counting.
    """
    if not path.is_dir():
        return None
    try:
        from .buildsource import pack_io
        from .buildsource.inline import is_pack_dir

        if not is_pack_dir(path):
            return None
        pack = pack_io.load(path)
    except Exception:  # noqa: BLE001 - estimate is advisory; never raise on a bad pack
        return None
    be = pack.build_evidence
    return len(be.compile_units) if be is not None else 0


def _count_bazel_build_info_tus(path: Path) -> int | None:
    """Compile-unit count of a Bazel ``aquery``/``cquery`` ``--build-info``,
    else ``None``. The real scan routes it through ``BazelAdapter``
    (pre-captured, ``allow_query=False``) and replays its compile actions;
    the estimate mirrors that so a Bazel project doesn't undersize the
    budget (Codex review). Non-executing, best-effort -- any failure ->
    ``None`` so the caller falls back to compile-DB/source counting.
    """
    if not path.is_file():
        return None
    try:
        from .buildsource.inline import sniff_build_info_format

        fmt = sniff_build_info_format(path)
        if fmt not in ("bazel_aquery", "bazel_cquery"):
            return None
        from .buildsource.adapters.bazel import BazelAdapter

        if fmt == "bazel_aquery":
            adapter = BazelAdapter(aquery=path, allow_query=False)
        else:
            adapter = BazelAdapter(cquery=path, allow_query=False)
        return len(adapter.collect().compile_units)
    except Exception:  # noqa: BLE001 - estimate is advisory; never raise
        return None


def _resolve_estimate_level(
    *,
    mode: str,
    source_method: str | None,
    depth: str | None,
    changed_paths: Sequence[str],
    seeded: bool,
    resolved_level: tuple[SourceMethod, EvidenceDepth] | None,
) -> tuple[SourceMethod, EvidenceDepth, str]:
    """Resolve the (method, depth) level and its collect mode for the estimate."""
    (
        _EvidenceDepth,
        ScanMode,
        SourceMethod,
        level_to_collect_mode,
        resolve_level,
        parse_user_depth,
        SourceScope,
    ) = _scan_imports()

    seeded = seeded or bool(changed_paths)
    if resolved_level is not None:
        # The caller (the CLI scan path) already resolved the concrete (method, depth) level. Honor it verbatim so the
        # estimate matches the real scan: re-resolving from source_method/depth here would re-apply the source-method >
        # depth precedence and collapse a mode preset that pins a *deeper* depth than its method implies (``pr-deep`` =
        # (s5, graph) -> graph-full), under-pricing it (Codex review).
        resolved, eff_depth = resolved_level
    else:
        sm = SourceMethod(source_method) if source_method else None
        dp = parse_user_depth(depth)  # honors the symbols->binary alias (Codex)
        # ADR-068's second 2026-09-09 amendment rules risk-driven ``auto``
        # depth selection (b) -- dropped, so no risk score is consulted here
        # any more. What an *unpinned command* resolves to instead
        # (``resolve_unpinned_level``'s fixed ``headers`` rung) is deliberately
        # NOT applied here: this function's own *mode* argument is a caller's
        # explicit "price this preset" request, not an omitted ``--depth``.
        # The ``scan`` CLI never relies on this branch for a real run's
        # preview -- it pre-resolves its own level and passes it as
        # *resolved_level* above -- so the two cannot disagree about what the
        # run will execute.
        resolved, eff_depth = resolve_level(
            mode=ScanMode(mode), source_method=sm, depth=dp, auto_method=None
        )
    # ADR-043 D2/D3 zero-TU fix: pin the S5 replay scope to CHANGED only with a
    # valid seed, else TARGET -- an unseeded explicit-source estimate must not
    # under-project to the "source-changed" default (no seed -> no TUs).
    collect_mode = level_to_collect_mode(
        resolved,
        eff_depth,
        source_scope=SourceScope.CHANGED if seeded else SourceScope.TARGET,
    )
    return resolved, eff_depth, collect_mode


def _estimate_total_tus(side: InputSpec, compile_db: Path | None) -> tuple[int, str]:
    """Project-wide TU count and its provenance note for the estimate."""
    # Count TUs from the *same* effective build-info the real scan uses (`req.compile_db or req.build_info`) so an
    # explicit --compile-db wins over a Bazel --build-info here too — else the estimate could price a different
    # action graph than the scan executes (Codex review). A pack dir supplies its own L3 compile units; a Bazel
    # aquery/cquery jsonproto is routed through the Bazel adapter; a raw compile DB / source tree is counted
    # otherwise.
    eff_build_info = compile_db or side.build_info
    bazel_tus = (
        _count_bazel_build_info_tus(eff_build_info)
        if eff_build_info is not None
        else None
    )
    pack_tus = _count_pack_tus(eff_build_info) if eff_build_info is not None else None
    discovered_db = _discover_compile_db(side.sources, eff_build_info)
    if bazel_tus is not None:
        total, note = bazel_tus, "Bazel aquery/cquery (build_evidence)"
    elif pack_tus is not None:
        total, note = pack_tus, "build-source pack (build_evidence)"
    elif discovered_db is not None:
        total, note = (
            _count_compile_db_tus(discovered_db),
            f"compile DB: {discovered_db.name}",
        )
    elif side.sources is not None:
        total, note = (
            _count_source_tus(side.sources),
            "counted source files (no compile DB)",
        )
    else:
        total, note = (
            0,
            (
                f"build.query: {side.build_config.name} [UNKNOWN: query-only build.query, real run's trusted query determines the actual count]"
                if side.build_config is not None
                and _build_config_declares_query(side.build_config)
                else "no source tree / compile DB"
            ),
        )
    if side.build_targets:
        note += _UNSCOPED_TU_NOTE_SUFFIX
    return total, note


def _estimate_replay_tus(
    changed_paths: Sequence[str],
    max_tus: int | None,
    collect_mode: str,
    total_tus: int,
) -> int:
    """TUs the L4 replay (and its clang call-graph pass) would touch."""
    # The L4 replay scope: a changed-only collection touches at most the changed *source* TUs (POI-focused, D7); a
    # full/target scope touches every TU. The budget's max_tus is a documented cap (never shrinks scope silently —
    # it FAILS — but the estimate honestly reflects the cap as the upper bound). A changed *header* fans out:
    # without an include graph (the common compile-DB-only path), ``source_replay.select_compile_units(scope=
    # 'changed')`` fails open to **all** TUs so header ABI changes are never silently missed, so the estimate must
    # charge ``total_tus`` for a header change rather than the single header path — else it understates L4 cost and
    # a user picks too small a budget (Codex review). An empty/seedless diff is likewise broad.
    changed = [p for p in changed_paths if p]
    source_changed = [p for p in changed if _is_source_tu_path(p)]
    header_changed = any(_is_header_path(p) for p in changed)
    if collect_mode == "source-changed":
        if not changed or header_changed:
            replay_tus = total_tus
        else:
            replay_tus = (
                min(len(source_changed), total_tus)
                if total_tus
                else len(source_changed)
            )
    else:
        # graph-full / baseline → full scope; graph-build emits no L4 row.
        replay_tus = total_tus
    if max_tus:
        replay_tus = min(replay_tus, max_tus)
    return replay_tus


def _intrinsic_layer_estimates(
    side: InputSpec, eff_depth: EvidenceDepth
) -> list[CostEstimate]:
    """The always-present L0/L1/L2 rows (intrinsic layers, no S-method)."""
    from .model.evidence_depth_levels import EvidenceDepth

    # --depth binary is symbols-only: the real scan suppresses the L2 header AST, so
    # the estimate must not price an L2_header layer for headers that won't be parsed
    # -- else a caller's dry-run preview plans a different cost than what
    # executes (Codex review). Keyed on the resolved effective depth.
    eff_req_headers = [] if eff_depth is EvidenceDepth.BINARY else list(side.headers)
    expanded_headers = expand_header_inputs(eff_req_headers) if eff_req_headers else []
    n_headers = len(expanded_headers)
    l2_seconds, l2_high_risk = _estimate_header_seconds(expanded_headers)
    if not n_headers:
        l2_note = "no headers supplied"
    elif l2_high_risk:
        l2_note = (
            "public-header AST (needs castxml or clang); deep #include/template "
            "complexity detected — this is a conservative floor, not a precise "
            "ETA, actual parse time can be far higher (unbounded in pathological "
            "cases); pass --budget to cap it"
        )
    else:
        l2_note = "public-header AST (needs castxml or clang)"
    n_binaries = 1 if side.path is not None else 0
    return [
        CostEstimate(
            None,
            "L0_binary",
            n_binaries,
            0.1 * max(1, n_binaries),
            0.0,
            "binary export table parse",
        ),
        CostEstimate(None, "L1_debug", 0, 0.05, 0.0, "debug info (if present)"),
        CostEstimate(None, "L2_header", n_headers, l2_seconds, 0.0, l2_note),
    ]


def _source_layer_estimates(
    resolved: SourceMethod,
    collect_mode: str,
    total_tus: int,
    tu_note: str,
    replay_tus: int,
    build_targets: tuple[str, ...] = (),
) -> list[CostEstimate]:
    """The collect-mode-dependent L3/L4/L5 rows (source-evidence layers)."""
    # "source-target" (ADR-043 D2/D3) is the unseeded sibling of "source-changed" — same L3/L4/L5 layers, just a
    # broader (target-scoped, not changed-only) replay; it must price identically to source-changed everywhere
    # below, else an unseeded explicit-source scan/estimate silently reports zero source layers (the same zero-TU
    # defect the collect-mode fix addresses). L4/L5 inherit the same unscoped/unknown total_tus the L3 row's
    # tu_note already flags -- a short back-reference, not the full sentence again.
    unscoped_ref = " [UNSCOPED, see L3_build note]" if build_targets else ""
    unknown_ref = _TU_UNKNOWN_NOTE_SUFFIX if "[UNKNOWN" in tu_note else ""
    estimates: list[CostEstimate] = []
    if collect_mode in (
        "build",
        "graph-build",
        "source-changed",
        "source-target",
        "graph-full",
    ):
        estimates.append(
            CostEstimate(
                "s1",
                "L3_build",
                total_tus,
                _COST_PER_TU_BUILD * total_tus,
                0.0,
                tu_note,
            )
        )
    if collect_mode in ("source-changed", "source-target", "graph-full"):
        estimates.append(
            CostEstimate(
                resolved.value,
                "L4_source_abi",
                replay_tus,
                _COST_PER_TU_REPLAY * replay_tus,
                0.0,
                f"{collect_mode} replay scope ({replay_tus} of {total_tus} TU(s))"
                f"{unscoped_ref}{unknown_ref}",
            )
        )
    # L5 structural fold runs for every graph-building mode (cheap).
    if collect_mode in ("graph-build", "graph-full", "source-changed", "source-target"):
        estimates.append(
            CostEstimate(
                resolved.value,
                "L5_source_graph",
                total_tus,
                _COST_PER_TU_GRAPH * total_tus,
                0.0,
                f"source graph fold/edges{unscoped_ref}{unknown_ref}",
            )
        )
    # When both L4 and L5 are collected the inline path also runs a Clang
    # call-graph pass (``inline._fold_call_graph``) over the replay scope — price
    # it so `scan --estimate` does not understate a source-changed/graph-full PR
    # scan (Codex review). Scope mirrors the L4 replay (changed-scoped vs full).
    if collect_mode in ("source-changed", "source-target", "graph-full"):
        estimates.append(
            CostEstimate(
                resolved.value,
                "L5_source_graph",
                replay_tus,
                _COST_PER_TU_REPLAY * replay_tus,
                0.0,
                f"call-graph clang pass ({replay_tus} of {total_tus} TU(s))"
                f"{unscoped_ref}{unknown_ref}",
            )
        )
    return estimates


def estimate_scan(
    side: InputSpec,
    *,
    mode: str = "pr",
    source_method: str | None = None,
    depth: str | None = None,
    changed_paths: Sequence[str] = (),
    seeded: bool = False,
    max_tus: int | None = None,
    compile_db: Path | None = None,
    resolved_level: tuple[SourceMethod, EvidenceDepth] | None = None,
) -> list[CostEstimate]:
    """Dry-run: projected per-layer cost of one comparison operand for this
    project (ADR-035 D10). Probes the project (TU count, header fan-out,
    collect mode) and returns one :class:`CostEstimate` per L-layer the level
    would touch -- **without running any compiler or parsing any binary**.
    Coarse anchors (see ``_COST_PER_*``): ranks layers for a depth/budget
    pick, not a precise wall-clock prediction.

    Takes the canonical :class:`~abicheck.api_types.InputSpec` one side of a
    comparison is already described by, plus the run-scoped scalars that are
    not a property of the operand itself. ADR-068's Phase 4 typed-API slice
    retired the ``ScanRequest`` this used to take: a cost preview is a
    projection over an *input*, and the request type it lived on is gone.
    """
    resolved, eff_depth, collect_mode = _resolve_estimate_level(
        mode=mode,
        source_method=source_method,
        depth=depth,
        changed_paths=changed_paths,
        seeded=seeded,
        resolved_level=resolved_level,
    )
    total_tus, tu_note = _estimate_total_tus(side, compile_db)
    replay_tus = _estimate_replay_tus(changed_paths, max_tus, collect_mode, total_tus)
    estimates = _intrinsic_layer_estimates(side, eff_depth)
    estimates.extend(
        _source_layer_estimates(
            resolved, collect_mode, total_tus, tu_note, replay_tus, side.build_targets
        )
    )
    return estimates


def _build_config_declares_query(path: Path) -> bool:
    """Does *path* declare ``build.query`` -- the only way a bare
    ``--build-config`` supplies L3 evidence on its own (Codex review: an
    inert config gives the real run nothing to collect either). ``False`` on
    any read/parse failure -- a dry-run can't know whether an unparseable
    query would have succeeded."""
    from .buildsource.build_config_io import load_build_config_with_digest

    try:
        config, _digest = load_build_config_with_digest(path)
    except ValueError:
        return False
    return bool(config.query)
