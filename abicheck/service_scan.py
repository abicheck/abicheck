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

"""Scan service — typed request/result contract and per-project cost estimate.

ADR-035 D10 / G19.7 (Phase 3b). One typed contract — :class:`ScanRequest` ->
:class:`ScanResult` / ``[CostEstimate]`` -- that the CLI and CI wrappers all
drive, so there is one engine and many renderings. A leaf module (must not
import :mod:`abicheck.service`); its header-expansion helper
(:func:`expand_header_inputs`) is re-exported by ``service`` for backward
compatibility.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .buildsource.build_query import (
    PRUNED_HEADER_DIR_SEGMENTS,
    drain_build_dir_cleanups,
)
from .compile_context import CompileContext as CompileContext  # re-exported, ADR-055 D1

# pair_wide_cxx20_std_override lives in the leaf `cxx20_pair_dialect` module
# (no dependency on anything service_scan-specific, so it moved cleanly to
# make line-budget room for the L4/L5 `_TU_UNKNOWN_NOTE_SUFFIX` propagation
# fix, Codex review, fresh evidence -- see that module's own docstring),
# re-exported here so every existing `from .service_scan import
# pair_wide_cxx20_std_override` call site is unaffected.
from .cxx20_pair_dialect import (
    pair_wide_cxx20_std_override as pair_wide_cxx20_std_override,
)
from .errors import ValidationError
from .header_utils import HEADER_SUFFIXES, iter_directory_headers
from .policy.exit_decision_precedence import scan_abort_result_fields
from .schemas import SCAN_SCHEMA_VERSION

if TYPE_CHECKING:
    from .buildsource.scan_levels import EvidenceDepth, SourceMethod
    from .environment_matrix import EnvironmentMatrix
    from .policy_file import PolicyFile
    from .suppression import SuppressionList

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
    """Lazily import the buildsource level/risk vocabulary (keeps import cheap)."""
    from .buildsource.risk import RiskRules, score_changed_paths
    from .buildsource.scan_levels import (
        EvidenceDepth,
        ScanMode,
        SourceMethod,
        SourceScope,
        level_to_collect_mode,
        parse_user_depth,
        resolve_level,
    )

    return (
        RiskRules,
        score_changed_paths,
        EvidenceDepth,
        ScanMode,
        SourceMethod,
        level_to_collect_mode,
        resolve_level,
        parse_user_depth,
        SourceScope,
    )


@dataclass(frozen=True)
class Budget:
    """Optional scan budget — a failure guard, never a scope-shrinker (ADR-035 D3)."""

    total_timeout: float | None = None  # seconds; overflow FAILS (never shrinks)
    max_tus: int | None = None  # targeted-AST TU cap
    partial_ok: bool = True  # a partial scan (missing tool/layer) is success


# CompileContext lives in the leaf `compile_context` module (ADR-055 D1,
# imported above, re-exported here) so api_types.py can depend on the type
# without joining this file's import-cycle-allowlisted cluster.


@dataclass(frozen=True)
class ScanRequest:
    """Typed input to the scan engine (ADR-035 D10). All additive over dump/compare."""

    binaries: list[Path] = field(default_factory=list)
    headers: list[Path] = field(default_factory=list)
    includes: list[Path] = field(default_factory=list)
    public_header_dirs: list[Path] = field(default_factory=list)
    sources: Path | None = None
    compile_db: Path | None = None
    build_info: Path | None = None
    baseline: str | Path | None = None
    mode: str = "pr"  # ScanMode value (fixed preset)
    source_method: str | None = None  # SourceMethod value; None = mode preset
    depth: str | None = None  # EvidenceDepth value (coarse L-axis)
    changed_paths: list[str] = field(default_factory=list)
    seeded: bool = False  # a real diff seed was produced (even if changed_paths is [])
    budget: Budget = field(default_factory=Budget)
    lang: str = "c++"
    # L2 header compile context (dump↔scan flag parity, ADR-037 D3).
    compile: CompileContext = field(default_factory=CompileContext)
    # --against config-surface parity with `compare` (ADR-049 Phase 5 §6.4): a `baseline` comparison can be scoped/suppressed/policy-classified the
    # same way a direct `compare` run is, instead of always using the fixed policy="strict_abi"/suppression=None/scope_to_public_surface=True the engine previously hardcoded.
    suppression: SuppressionList | None = None
    policy: str = "strict_abi"
    policy_file: PolicyFile | None = None
    scope_to_public_surface: bool = True
    force_public_symbols: set[str] | None = None
    pattern_verdicts: bool = False
    env_matrix: EnvironmentMatrix | None = None
    collapse_versioned_symbols: bool = False
    # ADR-049 Phase 5 §6.4 also names contract relevance/reason/evidence side among the fields the two commands must
    # agree on -- which a `scan --against` result could not carry at all while only `compare` could ask for the
    # shadow evaluator. Same advisory contract as `compare`: stamping a decision never changes verdict or exit code.
    contract_evaluation: bool = False
    contract_mode: str | None = None
    # ADR-056: options the single-binary CLI path (cli_scan.py) already forwards straight to run_scan_core,
    # bypassing ScanRequest entirely. Both run_scan and run_scan_set (--artifact-set) honor these when set directly
    # on a ScanRequest (P2 regression, Codex review: run_scan used to silently ignore them, so a direct Python API
    # caller passing abi3_floor/enabled_checks/severities/ build_config/allow_build_query/risk_rules_path got a
    # differently-gated result than requested).
    abi3_floor: tuple[int, int] | None = None
    enabled_checks: frozenset[str] | None = None  # None = ALL_CHECKS default
    severities: dict[str, str] = field(default_factory=dict)
    build_config: Path | None = None
    allow_build_query: bool = False
    risk_rules_path: Path | None = None
    # ADR-056 D2: caller-declared external DSO allow-list for the --artifact-set audit-mode bundle detector (closed-world escape hatch); unused by run_scan.
    bundle_system_providers: tuple[str, ...] = ()
    # ADR-056: real changed-path provenance (e.g. "--since origin/main" or "--changed-path"), as computed by
    # cli_scan._resolve_changed_seed. run_scan_set forwards this into each member's report instead of a hardcoded
    # placeholder; unused by run_scan (which threads its own changed_src through _run_scan_one_member's caller
    # directly).
    changed_src: str = "run_scan_set"
    # `--against` summary's findings/suppressed cap; see `scan --max-findings`.
    max_findings: int | None = None
    # P0.2 equivalent of `dump --build-target`; kw_only + appended last
    # (checker_types.py's public-dataclass convention) so a positional insert
    # can't rebind a later field (Codex review).
    build_targets: tuple[str, ...] = field(default=(), kw_only=True)


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


@dataclass(frozen=True)
class LayerResult:
    """Per-layer coverage of an *executed* scan (ADR-035 D10; reuses LayerCoverage)."""

    method: str | None
    layer: str
    status: str  # "present" | "partial" | "skipped" | "not_collected"
    facts: int = 0
    elapsed_s: float = 0.0
    skipped_reason: str | None = None
    detail: str = ""
    #: Source-surface boundary integrity counters (ADR-035 D4), carried from the
    #: engine's coverage row so the rendered report can show a degraded link
    #: (e.g. ``matched_symbols == 0``) rather than only an internal object.
    counters: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "layer": self.layer,
            "status": self.status,
            "facts": self.facts,
            "elapsed_s": round(self.elapsed_s, 3),
            "skipped_reason": self.skipped_reason,
            "detail": self.detail,
            "counters": dict(self.counters),
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
    req: ScanRequest,
    resolved_level: tuple[SourceMethod, EvidenceDepth] | None,
) -> tuple[SourceMethod, EvidenceDepth, str]:
    """Resolve the (method, depth) level and its collect mode for the estimate."""
    (
        RiskRules,
        score_changed_paths,
        _EvidenceDepth,
        ScanMode,
        SourceMethod,
        level_to_collect_mode,
        resolve_level,
        parse_user_depth,
        SourceScope,
    ) = _scan_imports()

    mode = ScanMode(req.mode)
    seeded = req.seeded or bool(req.changed_paths)
    if resolved_level is not None:
        # The caller (the CLI scan path) already resolved the concrete (method, depth) level — including the auto/risk
        # choice. Honor it verbatim so the estimate matches the real scan: re-resolving from req.source_method/depth
        # here would re-apply the source-method > depth precedence and collapse a mode preset that pins a *deeper* depth
        # than its method implies (``pr-deep`` = (s5, graph) → graph-full), under-pricing it (Codex review).
        resolved, eff_depth = resolved_level
    else:
        sm = SourceMethod(req.source_method) if req.source_method else None
        dp = parse_user_depth(req.depth)  # honors the symbols→binary alias (Codex)
        auto_method = None
        # AUTO resolves from the risk score whenever a real diff seed was produced — including a *seeded but empty* diff
        # (a no-op PR), which scores 0 → s0/off, mirroring what the real scan does. Treating a seeded empty diff as
        # unseeded would fall back to the mode preset and over-estimate a no-op PR (Codex review). A non-empty changed
        # set is itself proof of a seed.
        if sm is SourceMethod.AUTO and (req.seeded or req.changed_paths):
            auto_method = score_changed_paths(
                list(req.changed_paths), RiskRules.default()
            ).recommended_method
        resolved, eff_depth = resolve_level(
            mode=mode, source_method=sm, depth=dp, auto_method=auto_method
        )
    # ADR-043 D2/D3 zero-TU fix: pin the S5 replay scope to CHANGED only with a
    # valid seed, else TARGET — an unseeded explicit-source estimate must not
    # under-project to the "source-changed" default (no seed → no TUs).
    collect_mode = level_to_collect_mode(
        resolved,
        eff_depth,
        source_scope=SourceScope.CHANGED if seeded else SourceScope.TARGET,
    )
    return resolved, eff_depth, collect_mode


def _estimate_total_tus(req: ScanRequest) -> tuple[int, str]:
    """Project-wide TU count and its provenance note for the estimate."""
    # Count TUs from the *same* effective build-info the real scan uses (`req.compile_db or req.build_info`) so an
    # explicit --compile-db wins over a Bazel --build-info here too — else the estimate could price a different
    # action graph than the scan executes (Codex review). A pack dir supplies its own L3 compile units; a Bazel
    # aquery/cquery jsonproto is routed through the Bazel adapter; a raw compile DB / source tree is counted
    # otherwise.
    eff_build_info = req.compile_db or req.build_info
    bazel_tus = (
        _count_bazel_build_info_tus(eff_build_info)
        if eff_build_info is not None
        else None
    )
    pack_tus = _count_pack_tus(eff_build_info) if eff_build_info is not None else None
    compile_db = _discover_compile_db(req.sources, eff_build_info)
    if bazel_tus is not None:
        total, note = bazel_tus, "Bazel aquery/cquery (build_evidence)"
    elif pack_tus is not None:
        total, note = pack_tus, "build-source pack (build_evidence)"
    elif compile_db is not None:
        total, note = (
            _count_compile_db_tus(compile_db),
            f"compile DB: {compile_db.name}",
        )
    elif req.sources is not None:
        total, note = (
            _count_source_tus(req.sources),
            "counted source files (no compile DB)",
        )
    else:
        total, note = (
            0,
            (
                f"build.query: {req.build_config.name} [UNKNOWN: query-only build.query, real run's trusted query determines the actual count]"
                if req.build_config is not None
                and _build_config_declares_query(req.build_config)
                else "no source tree / compile DB"
            ),
        )
    if req.build_targets:
        note += _UNSCOPED_TU_NOTE_SUFFIX
    return total, note


def _estimate_replay_tus(req: ScanRequest, collect_mode: str, total_tus: int) -> int:
    """TUs the L4 replay (and its clang call-graph pass) would touch."""
    # The L4 replay scope: a changed-only collection touches at most the changed *source* TUs (POI-focused, D7); a
    # full/target scope touches every TU. The budget's max_tus is a documented cap (never shrinks scope silently —
    # it FAILS — but the estimate honestly reflects the cap as the upper bound). A changed *header* fans out:
    # without an include graph (the common compile-DB-only path), ``source_replay.select_compile_units(scope=
    # 'changed')`` fails open to **all** TUs so header ABI changes are never silently missed, so the estimate must
    # charge ``total_tus`` for a header change rather than the single header path — else it understates L4 cost and
    # a user picks too small a budget (Codex review). An empty/seedless diff is likewise broad.
    changed = [p for p in req.changed_paths if p]
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
    if req.budget.max_tus:
        replay_tus = min(replay_tus, req.budget.max_tus)
    return replay_tus


def _intrinsic_layer_estimates(
    req: ScanRequest, eff_depth: EvidenceDepth
) -> list[CostEstimate]:
    """The always-present L0/L1/L2 rows (intrinsic layers, no S-method)."""
    from .buildsource.scan_levels import EvidenceDepth

    # --depth binary is symbols-only: the real scan suppresses the L2 header AST, so
    # the estimate must not price an L2_header layer for headers that won't be parsed
    # — else a programmatic caller's `ScanResult.estimate` plans a different cost than
    # what executes (Codex review). Keyed on the resolved effective depth.
    eff_req_headers = [] if eff_depth is EvidenceDepth.BINARY else list(req.headers)
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
    return [
        CostEstimate(
            None,
            "L0_binary",
            len(req.binaries),
            0.1 * max(1, len(req.binaries)),
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
    req: ScanRequest,
    *,
    resolved_level: tuple[SourceMethod, EvidenceDepth] | None = None,
) -> list[CostEstimate]:
    """Dry-run: projected per-layer cost of *req* for this project (ADR-035
    D10). Probes the project (TU count, header fan-out, collect mode) and
    returns one :class:`CostEstimate` per L-layer the level would touch --
    **without running any compiler or parsing any binary**. Coarse anchors
    (see ``_COST_PER_*``): ranks layers for a depth/budget pick, not a
    precise wall-clock prediction."""
    resolved, eff_depth, collect_mode = _resolve_estimate_level(req, resolved_level)
    total_tus, tu_note = _estimate_total_tus(req)
    replay_tus = _estimate_replay_tus(req, collect_mode, total_tus)
    estimates = _intrinsic_layer_estimates(req, eff_depth)
    estimates.extend(
        _source_layer_estimates(
            resolved, collect_mode, total_tus, tu_note, replay_tus, req.build_targets
        )
    )
    return estimates


@dataclass(frozen=True)
class ScanResult:
    """Typed result of an executed scan (ADR-035 D10) — the one object the CLI
    and library callers consume. ``findings`` are the raw cross-source
    :class:`Change` objects; ``layers`` is the per-layer coverage;
    ``confidence`` is the §6.8 provider-agreement matrix; ``estimate`` is the
    projected per-layer cost for comparison against the actual run.
    """

    verdict: str
    exit_code: int
    findings: list[Any] = field(default_factory=list)
    layers: list[LayerResult] = field(default_factory=list)
    confidence: dict[str, list[str]] = field(default_factory=dict)
    estimate: list[CostEstimate] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_schema_version": SCAN_SCHEMA_VERSION,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "findings": len(self.findings),
            "layers": [layer.to_dict() for layer in self.layers],
            "confidence": {k: list(v) for k, v in self.confidence.items()},
            "estimate": [e.to_dict() for e in self.estimate],
            "report": dict(self.report),
        }


@dataclass(frozen=True)
class ScanArtifactResult:
    """One member's :class:`ScanResult`, with the identity that result alone
    doesn't carry (ADR-056 — neither `ScanResult` nor its nested report
    carries a binary path or library name anywhere)."""

    artifact: Path
    result: ScanResult

    def to_dict(self) -> dict[str, Any]:
        return {"artifact": str(self.artifact), **self.result.to_dict()}


# ADR-056 D3: ScanSetResult's own verdict/exit-code precedence — not a reuse
# of compare-release's `_RELEASE_VERDICT_ORDER`, which has no entries for the
# two scan-specific failure verdicts a ScanResult can carry
# (`BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR`).
_SCAN_SET_COMPAT_ORDER: dict[str, int] = {
    # NO_CHANGE ranks strictly below COMPATIBLE (not tied at 0): the bundle audit's own verdict is always appended
    # to `candidates` below and often reads NO_CHANGE when it simply found nothing to flag -- with a tied rank, the
    # >= tie-break (last-candidate-wins) let that placeholder silently override a real, positive COMPATIBLE result
    # from every member scan (Codex review). A genuine "I actually compared this and it's fine" always outranks
    # "there was nothing to compare here."
    "NO_CHANGE": -1,
    "COMPATIBLE": 0,
    "COMPATIBLE_WITH_RISK": 1,
    "API_BREAK": 2,
    "BREAKING": 3,
}
_SCAN_SET_COMPAT_EXIT: dict[str, int] = {
    "NO_CHANGE": 0,
    "COMPATIBLE": 0,
    "COMPATIBLE_WITH_RISK": 0,
    "API_BREAK": 2,
    "BREAKING": 4,
}


def _aggregate_scan_set_verdict(
    per_artifact: list[ScanArtifactResult], bundle_verdict: str | None
) -> tuple[str, int]:
    """Combine per-member + bundle verdicts into one set-level verdict/exit.

    1. Any member ``BUDGET_OVERFLOW`` -> the whole set is ``BUDGET_OVERFLOW``,
       exit 5 (dominates: an unfinished analysis is worse than a finished one
       that found a break, ADR-050 D2's ``not_comparable`` precedent).
    2. Else, the worst compatibility verdict across `per_artifact` + the
       bundle layer's own verdict (`_SCAN_SET_COMPAT_ORDER`), with the
       matching exit code.
    3. Any member ``EVIDENCE_CONTRACT_ERROR`` floors the exit code at 1
       without lowering a worse one from step 2, and becomes the reported
       verdict only when step 2's worst was NO_CHANGE/COMPATIBLE/
       COMPATIBLE_WITH_RISK; a stronger API_BREAK/BREAKING keeps that string.
    """
    if any(a.result.verdict == "BUDGET_OVERFLOW" for a in per_artifact):
        return "BUDGET_OVERFLOW", 5

    worst_verdict = "NO_CHANGE"
    worst_rank = -1
    candidates = [a.result.verdict for a in per_artifact]
    if bundle_verdict is not None:
        candidates.append(bundle_verdict)
    for v in candidates:
        rank = _SCAN_SET_COMPAT_ORDER.get(v)
        if rank is not None and rank >= worst_rank:
            worst_rank = rank
            worst_verdict = v
    exit_code = _SCAN_SET_COMPAT_EXIT.get(worst_verdict, 0)

    has_evidence_error = any(
        a.result.verdict == "EVIDENCE_CONTRACT_ERROR" for a in per_artifact
    )
    if has_evidence_error:
        exit_code = max(exit_code, 1)
        if worst_rank <= _SCAN_SET_COMPAT_ORDER["COMPATIBLE_WITH_RISK"]:
            worst_verdict = "EVIDENCE_CONTRACT_ERROR"

    return worst_verdict, exit_code


@dataclass(frozen=True)
class ScanSetResult:
    """Result of :func:`run_scan_set` — the ``--artifact-set`` sibling of
    :class:`ScanResult` (ADR-056). Not a change to what `run_scan`/
    `ScanResult` return for the single-binary path.
    """

    verdict: str
    exit_code: int
    per_artifact: list[ScanArtifactResult] = field(default_factory=list)
    bundle_findings: list[Any] = field(default_factory=list)
    bundle_verdict: str | None = None
    bundle_incomplete: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "scan_schema_version": SCAN_SCHEMA_VERSION,
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "per_artifact": [a.to_dict() for a in self.per_artifact],
            # Full finding records (kind/symbol/consumer/provider/description), not just a count -- mirrors
            # cli_compare_release_helpers.py's summary["bundle_findings"] JSON shape for the two-sided release path, so a
            # machine consumer of either can identify and act on the condition that produced bundle_verdict, not just its
            # count (Codex review).
            "bundle_findings": [
                {
                    "kind": f.kind.value,
                    "symbol": f.symbol,
                    "consumer_library": f.consumer_library,
                    "provider_library": f.provider_library,
                    "description": f.description,
                    "old_value": f.old_value,
                    "new_value": f.new_value,
                    "affected_libraries": list(f.affected_libraries),
                }
                for f in self.bundle_findings
            ],
            "bundle_finding_count": len(self.bundle_findings),
            "bundle_verdict": self.bundle_verdict,
            "bundle_incomplete": self.bundle_incomplete,
        }


def _layers_from_coverage(coverage: list[dict[str, Any]]) -> list[LayerResult]:
    """Map the engine's coverage rows onto typed :class:`LayerResult`s."""
    out: list[LayerResult] = []
    for row in coverage:
        # Defensive int coercion: a hand-edited / forward-compat row could carry a
        # non-numeric counter or facts value; skip it rather than abort the render.
        counters: dict[str, int] = {}
        for k, v in (row.get("counters") or {}).items():
            try:
                counters[str(k)] = int(v)
            except (TypeError, ValueError):
                continue
        try:
            facts = int(row.get("facts", 0) or 0)
        except (TypeError, ValueError):
            facts = 0
        out.append(
            LayerResult(
                method=row.get("method"),
                layer=str(row.get("layer", "")),
                status=str(row.get("status", "")),
                facts=facts,
                detail=str(row.get("detail", "")),
                skipped_reason=row.get("skipped_reason"),
                counters=counters,
            )
        )
    return out


def _load_risk_rules_for_service(risk_rules_path: Path) -> Any:
    """Load `--risk-rules` YAML for a service-layer caller, as a plain
    `ValueError`: `risk_rules_path` is a plain `ScanRequest` field a direct
    Python API caller can set without importing click, so this boundary
    must not leak a click-flavored failure (CodeRabbit review). A malformed
    profile is a `SnapshotError` from the engine, also not a `ValueError`;
    `ClickException` is still caught because the CLI-side adapter raises it.
    """
    import click

    from .errors import SnapshotError
    from .workflows.scan_config import load_risk_rules as _load

    try:
        return _load(risk_rules_path)
    except (click.ClickException, SnapshotError) as exc:
        msg = (
            exc.format_message() if isinstance(exc, click.ClickException) else str(exc)
        )
        raise ValueError(str(msg)) from exc


def _resolve_member_scan_level(
    req: ScanRequest,
) -> tuple[Any, Any, list[str], bool, Any, Any, Any, str]:
    """Resolve ``(sm, dp, changed, seeded, risk, resolved, eff_depth,
    collect_mode)`` for one member -- shared by :func:`_run_scan_one_member`
    and :func:`estimate_artifact_set`, per ``workflows/AGENTS.md``'s
    "dry-run and execution must consume the same resolved plan" rule.
    """
    (
        RiskRules,
        score_changed_paths,
        _EvidenceDepth,
        ScanMode,
        SourceMethod,
        level_to_collect_mode,
        resolve_level,
        parse_user_depth,
        SourceScope,
    ) = _scan_imports()
    sm = SourceMethod(req.source_method) if req.source_method else None
    dp = parse_user_depth(req.depth)
    changed = [p for p in req.changed_paths if p]
    seeded = req.seeded or bool(changed)
    risk_rules = (
        _load_risk_rules_for_service(req.risk_rules_path)
        if req.risk_rules_path is not None
        else RiskRules.default()
    )
    risk = score_changed_paths(changed, risk_rules)
    auto_method = (
        risk.recommended_method if (sm is SourceMethod.AUTO and seeded) else None
    )
    resolved, eff_depth = resolve_level(
        mode=ScanMode(req.mode), source_method=sm, depth=dp, auto_method=auto_method
    )
    scope = SourceScope.CHANGED if seeded else SourceScope.TARGET
    collect_mode = level_to_collect_mode(resolved, eff_depth, source_scope=scope)
    return sm, dp, changed, seeded, risk, resolved, eff_depth, collect_mode


_COST_PER_MEMBER_BUNDLE_AUDIT = 0.1  # per-member bundle-audit anchor, ~= L0_binary's


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


def estimate_artifact_set(
    req: ScanRequest, member_paths: list[Path]
) -> tuple[dict[str, tuple[int, float]], list[str], str | None, frozenset[str]]:
    """Per-layer cost total for ``scan --artifact-set --dry-run``: one
    single-binary estimate per member at the level :func:`_resolve_member_scan_level`
    resolves once (shared with :func:`_run_scan_one_member`), plus a
    ``bundle_audit`` entry. Third return value: a blocker message, or
    ``None`` -- set only when ``collect_mode != "off"`` (mirrors
    ``scan_engine._check_scan_evidence_contract``'s short-circuit) and no
    source/build evidence was given, matching ``EVIDENCE_CONTRACT_ERROR``
    (exit 1). Kept out of *notes* so the caller routes it through
    :meth:`DryRunResult.block` instead. Fourth value *unknown_layers* names
    per-layer unknown-marked members (totals alone can't tell zero from unknown).
    """
    sm, dp, _c, _s, _r, resolved, eff_depth, collect_mode = _resolve_member_scan_level(
        req
    )
    totals: dict[str, tuple[int, float]] = {}
    notes: list[str] = []
    seen: set[str] = set()
    unknown_layers: set[str] = set()
    blocker: str | None = None
    pinned = dp is not None or (sm is not None and sm != "auto")
    cfg_query = req.build_config and _build_config_declares_query(req.build_config)
    no_evidence = not (req.sources or req.build_info or cfg_query)
    if pinned and collect_mode != "off" and no_evidence:
        blocker = (
            f"pinned depth '{eff_depth.value}' has no --sources/--build-info, "
            "and --build-config declares no build.query -- the real run "
            "would fail with EVIDENCE_CONTRACT_ERROR (exit 1), not run as "
            "priced below."
        )
    for member_path in member_paths:
        member_req = replace(req, binaries=[member_path], mode="audit")
        for e in estimate_scan(member_req, resolved_level=(resolved, eff_depth)):
            tus, seconds = totals.get(e.layer, (0, 0.0))
            totals[e.layer] = (tus + e.tus, seconds + e.est_seconds)
            unknown_layers.update({e.layer} if "[UNKNOWN" in e.note else ())
            if e.note and e.note not in seen:
                seen.add(e.note)
                notes.append(e.note)
    n = len(member_paths)
    totals["bundle_audit"] = (n, _COST_PER_MEMBER_BUNDLE_AUDIT * n)
    return totals, notes, blocker, frozenset(unknown_layers)


#: Every ``ScanRequest`` field meaningful only for a baseline comparison,
#: keyed to a predicate that's ``True`` when *req* carries a caller-set,
#: non-default value (see `test_every_baseline_only_field_is_guarded`).
_COMPARISON_ONLY_FIELD_PREDICATES: dict[str, Callable[[ScanRequest], bool]] = {
    "suppression": lambda r: r.suppression is not None,
    "policy": lambda r: r.policy != "strict_abi",
    "policy_file": lambda r: r.policy_file is not None,
    "scope_to_public_surface": lambda r: r.scope_to_public_surface is not True,
    "force_public_symbols": lambda r: bool(r.force_public_symbols),
    "pattern_verdicts": lambda r: r.pattern_verdicts,
    "env_matrix": lambda r: r.env_matrix is not None,
    "collapse_versioned_symbols": lambda r: r.collapse_versioned_symbols,
    "contract_evaluation": lambda r: r.contract_evaluation,
    "contract_mode": lambda r: r.contract_mode is not None,
    "max_findings": lambda r: r.max_findings is not None,
}


def _reject_comparison_only_fields(req: ScanRequest) -> None:
    """Raise :class:`ValidationError` for a set ``_COMPARISON_ONLY_FIELD_PREDICATES``
    field with no baseline for it to apply to. Shared by :func:`run_scan`
    (baseline ``None``/audit mode) and :func:`run_scan_set` (always
    audit-only, ADR-056 D2) so both enforce the identical contract (P2
    regression: ``run_scan_set()`` used to only validate ``req.baseline``).
    """
    _non_default = [
        name
        for name, is_set in _COMPARISON_ONLY_FIELD_PREDICATES.items()
        if is_set(req)
    ]
    if _non_default:
        raise ValidationError(
            f"ScanRequest field(s) {', '.join(_non_default)} only take "
            "effect with a baseline comparison (req.baseline set, and "
            "req.mode not 'audit'); they configure compare_snapshots and "
            "have no effect otherwise."
        )


def _scan_request_config(req: ScanRequest) -> Any:
    """This request's resolved configuration, or ``None`` when unused.

    The API counterpart of ``cli_scan``'s own resolution. Without it a
    ``run_scan(ScanRequest(..., contract_evaluation=True))`` persisted the
    context ``checker.compare`` reconstructs from its arguments -- which
    keeps :class:`GateConfig`'s defaults, so the receipt claimed the
    ``severity`` scheme while ``run_scan`` computed its 0/2/4 exit straight
    from the compatibility verdict (Codex review). The CLI was wired first
    and this path was left behind.

    Every field is read as *stated*, matching
    :func:`~abicheck.compatibility_evaluation_frontend.compare_request_inputs`:
    a typed request has no "unset" representation, so a caller constructing
    one chose those values whether deliberately or by accepting the
    dataclass default. The gate fields are blanked for the same reason the
    CLI blanks the project config's -- a scan's exit follows its verdict and
    never consults them.

    Resolved at :attr:`FrontEnd.API`, so the receipt names this request's own
    fields rather than the ``--policy``/``--scope-public-headers`` flags
    nobody typed -- and through
    :data:`~abicheck.cli_scan_receipt.SCAN_REQUEST_SPELLINGS`, so they are
    *this* request's names: the default API spelling is ``CompareRequest``'s,
    which calls three of the same inputs ``scope_public``/
    ``policy_file_path``/``suppress``, none of which a ``ScanRequest`` has
    (Codex review, twice on the same receipt).
    :func:`~abicheck.compatibility_evaluation_frontend.unstatable_selectors`
    checks both halves now rather than leaving them to review.

    A D7 same-tier conflict or a D8 pack conflict is a *usage* error about
    the request, but the resolver raises its own :class:`ValueError`
    subclasses for those. Unmapped, they escaped ``run_scan`` raw -- past a
    ``try`` that catches only the budget and evidence-contract signals -- so
    a caller guarding it with ``except ValidationError``, which every other
    request-validation failure here raises, would not catch them.
    ``cli_scan.py`` already maps the same failures to ``click.UsageError``;
    this is the API's equivalent, so a bad request is reported the same way
    on both front ends (CodeRabbit review). Mapped here rather than at the
    one call site so a second caller cannot reintroduce the gap.

    One ``except ValueError`` covers every case: ``FieldResolutionError``,
    ``PackConflictError``, and ``PackManifestError`` -- the three
    ``cli_scan.scan_cmd`` names individually -- are all ``ValueError``
    subclasses (``PackManifestError`` through ``AbicheckError``), so naming
    them adds no coverage. It is also strictly wider, which this front end
    needs: an unknown ``ScanRequest.policy`` reaches
    ``builtin_policy_identity`` and raises a *plain* ``ValueError`` that
    none of the three would have caught. The CLI cannot reach that case --
    ``--policy`` is a ``click.Choice``, so Click rejects an unknown base
    before the resolver sees it -- which is why the two front ends' nets
    differ: they admit different inputs, not different ideas of what a bad
    request is. Keep them in sync if either gains a new failure mode.

    The breadth has a real cost worth stating: an internal ``ValueError``
    from a bug inside the resolver would also be reported as a bad request
    rather than crashing. That is the accepted trade -- every ``ValueError``
    this resolver raises today *is* a statement about the request's own
    values, and misreporting a hypothetical internal one is better than
    letting a genuine bad request escape as an unhandled exception through
    a Tier-2 API whose other validation failures all raise
    ``ValidationError``.
    """
    if not req.contract_evaluation or req.baseline is None:
        return None
    from .compatibility_evaluation_frontend import FrontEnd, stated_policy_base
    from .errors import ValidationError
    from .workflows.scan_config import resolve_scan_config

    try:
        return resolve_scan_config(
            {
                # Dropped when a `policy_file` overrode it, exactly as the comparison itself treats it -- otherwise an accepted
                # request (unknown name, valid file) died in its own receipt before the comparison ran (Codex review, the same
                # defect already fixed on the MCP path; the helper is shared now).
                "policy": stated_policy_base(req.policy, req.policy_file),
                "policy_file_path": None,
                "suppress": None,
                "scope_public_headers": req.scope_to_public_surface,
                "public_symbols": tuple(sorted(req.force_public_symbols or ())),
                "public_symbols_list": None,
                "contract_mode": req.contract_mode,
                # A `ScanRequest` has no pack field: ADR-049 D8 packs are a
                # CLI selector today, so the API resolves none rather than
                # letting the key resolve as "not stated" by omission (which
                # is what the declared-params guard exists to prevent).
                "pack_paths": (),
                # A `ScanRequest` has no severity-preset/exit-code-scheme
                # field either (CLI cleanup phase two, "PR B" -- these two
                # keys joined `SCAN_CONFIG_PARAMS` so a selected gate pack
                # cannot override an *explicit* `--severity-preset`/
                # `--exit-code-scheme` on the CLI path); the API path has no
                # such CLI flag to be explicit about, so both resolve as
                # "not stated" here, same as every other field this request
                # shape does not carry.
                "severity_preset": None,
                "exit_code_scheme": None,
            },
            typed={"policy", "scope_public_headers"},
            policy_file=req.policy_file,
            suppression=req.suppression,
            front_end=FrontEnd.API,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc


def run_scan(req: ScanRequest) -> ScanResult:
    """Execute a scan and return a typed :class:`ScanResult` (ADR-035 D10).
    The single engine entry point behind the ``scan`` CLI and the MCP scan
    tool: resolves the deterministic level from *req* (as :func:`estimate_scan`
    does), drives the shared orchestration core (``scan_engine.run_scan_core``),
    and folds the projected ``estimate_scan`` cost in for projected-vs-actual
    comparison. ``--budget`` overflow surfaces as ``exit_code`` 5 (never
    shrinks scope).
    """
    (
        RiskRules,
        score_changed_paths,
        EvidenceDepth,
        ScanMode,
        SourceMethod,
        level_to_collect_mode,
        resolve_level,
        parse_user_depth,
        SourceScope,
    ) = _scan_imports()
    from .buildsource.crosscheck import ALL_CHECKS
    from .scan_engine import (
        _BudgetOverflow,
        _EvidenceContractError,
        run_scan_core,
    )
    from .workflows.scan_config import public_provenance_set as _public_provenance_set

    if len(req.binaries) != 1:
        raise ValueError("run_scan accepts exactly one binary")
    binary = req.binaries[0]

    if req.max_findings is not None and req.max_findings < 1:
        # Up front, not after a wasted full scan (Codex review).
        raise ValidationError(
            f"ScanRequest.max_findings must be positive, got {req.max_findings}"
        )
    # ADR-049 Phase 5 review (Codex, PR #657): these fields only mean anything for a baseline comparison (`run_scan_core` only calls
    # `_run_baseline_compare` when `baseline is not None` AND `scan_mode is not ScanMode.AUDIT`) -- without one they'd be silently accepted and discarded, which could hide a `policy_file` requiring evidence the caller actually needed. Mirrors the CLI's identical `scan_cmd` guard.
    if req.baseline is None or ScanMode(req.mode) is ScanMode.AUDIT:
        _reject_comparison_only_fields(req)
    else:
        # ADR-049 Phase 6's own rule, applied where the CLI applies it: up front, not after the scan.
        # `compare_snapshots` already rejects the combination at the Tier-2 boundary (`service._validate_contract_mode`,
        # which this reuses rather than restating), so it was never silently accepted -- but reaching that check means a
        # full scan runs first and then fails, where `scan_cmd` rejects the same request before any work (CodeRabbit
        # review).
        from .service import _validate_contract_mode

        _validate_contract_mode(req.contract_mode, req.contract_evaluation)
    sm = SourceMethod(req.source_method) if req.source_method else None
    dp = parse_user_depth(req.depth)  # honors the symbols→binary alias (Codex)

    changed = [p for p in req.changed_paths if p]
    seeded = req.seeded or bool(changed)
    risk_rules = (
        _load_risk_rules_for_service(req.risk_rules_path)
        if req.risk_rules_path is not None
        else RiskRules.default()
    )
    risk = score_changed_paths(changed, risk_rules)

    scan_mode = ScanMode(req.mode)
    # The pinned-depth contract (ADR-037 D5 auto-strict) applies to the programmatic API too: an explicit depth
    # *always* pins (even with source_method=auto, which only picks the method), or a non-auto source_method does.
    # So run_scan_core fails loud if it can't collect the evidence — same as the CLI. AUTO / preset- only requests
    # stay best-effort (CodeRabbit review).
    pinned_explicit = (dp is not None) or (
        sm is not None and sm is not SourceMethod.AUTO
    )
    sm_pin = sm is not None and sm is not SourceMethod.AUTO
    # Mirrors cli_scan._scan_explicit_flags'/_run_scan_one_member's level_explicit: consent to auto-running a
    # trusted --config's build.query (a non-auto source_method, or an explicit depth with no source_method pinned).
    # Without this the singular Python API path left level_explicit at run_scan_core's False default, so an explicit
    # build_config + depth="build"/"source" could return EVIDENCE_CONTRACT_ERROR instead of auto-running the query
    # the CLI and run_scan_set both already consent to (Codex review).
    level_explicit = sm_pin or (sm is None and dp is not None)
    is_auto = sm is SourceMethod.AUTO
    auto_method = risk.recommended_method if (is_auto and seeded) else None
    resolved, eff_depth = resolve_level(
        mode=scan_mode, source_method=sm, depth=dp, auto_method=auto_method
    )
    # ADR-043 D2/D3 zero-TU fix: the S5 replay scope is command-aware — a valid
    # change seed scopes to CHANGED, otherwise TARGET (the current library
    # target), so an explicit/pinned source depth with no diff seed never
    # silently collects zero translation units (parity with the CLI's scan).
    collect_mode = level_to_collect_mode(
        resolved,
        eff_depth,
        source_scope=SourceScope.CHANGED if seeded else SourceScope.TARGET,
    )
    # --depth binary is symbols-only (L0/L1): suppress the L2 header AST (and its provenance) even when the caller
    # passes headers, so the collected evidence matches the reported depth — parity with the CLI's `scan --depth
    # binary`. Keyed on the *resolved* effective depth, not the raw depth: --source-method wins over --depth, so a
    # source-method scan that also passes `depth="binary"` still needs the header AST (Codex review).
    eff_headers = [] if eff_depth is EvidenceDepth.BINARY else list(req.headers)
    prov_headers, prov_dirs = _public_provenance_set(
        eff_headers, list(req.public_header_dirs)
    )
    effective_build_info = req.compile_db or req.build_info
    budget_s = req.budget.total_timeout
    budget_str = f"{budget_s:g}s" if budget_s is not None else None

    import time as _time

    # Own the inferred cmake build-dir cleanup so it outlives run_scan_core's S2
    # preprocessor phase (which runs `clang -E` with a compile unit's `directory`
    # as cwd); run it in the finally below on every exit path. See cli_scan.run_scan.
    build_dir_cleanups: list[Callable[[], None]] = []
    try:
        core = run_scan_core(
            start=_time.monotonic(),
            binary=binary,
            headers=eff_headers,
            includes=list(req.includes),
            public_headers=prov_headers,
            public_header_dirs=prov_dirs,
            sources=req.sources,
            effective_build_info=effective_build_info,
            build_config=req.build_config,
            baseline=Path(req.baseline) if req.baseline is not None else None,
            lang=req.lang,
            allow_build_query=req.allow_build_query,
            scan_mode=scan_mode,
            resolved=resolved,
            eff_depth_enum=eff_depth,
            collect_mode=collect_mode,
            changed=changed,
            changed_src="run_scan",
            seeded=seeded,
            risk=risk,
            is_auto=is_auto,
            enabled_checks=(
                req.enabled_checks
                if req.enabled_checks is not None
                else frozenset(ALL_CHECKS)
            ),
            severities=dict(req.severities),
            budget=budget_str,
            budget_s=budget_s,
            pinned_explicit=pinned_explicit,
            level_explicit=level_explicit,
            compile_context=None if req.compile.is_default else req.compile,
            defer_cleanup=build_dir_cleanups,
            suppression=req.suppression,
            policy=req.policy,
            policy_file=req.policy_file,
            scope_to_public_surface=req.scope_to_public_surface,
            force_public_symbols=req.force_public_symbols,
            pattern_verdicts=req.pattern_verdicts,
            env_matrix=req.env_matrix,
            collapse_versioned_symbols=req.collapse_versioned_symbols,
            contract_evaluation=req.contract_evaluation,
            contract_mode=req.contract_mode,
            resolved_config=_scan_request_config(req),
            abi3_floor=req.abi3_floor,
            max_findings=req.max_findings,
            build_targets=req.build_targets,
        )
    except _BudgetOverflow:
        # The failure-guard contract: overflow is exit 5, never a shrunk scope.
        verdict, exit_code, report = scan_abort_result_fields("budget_overflow")
        return ScanResult(verdict=verdict, exit_code=exit_code, report=report)
    except _EvidenceContractError:
        # A pinned depth that can't collect its evidence (auto-strict, ADR-037 D5):
        # the programmatic API honors the same contract as the CLI (pinned_explicit
        # above), so map the signal to a failed result rather than degrade silently.
        verdict, exit_code, report = scan_abort_result_fields("evidence_contract_error")
        return ScanResult(verdict=verdict, exit_code=exit_code, report=report)
    finally:
        # Remove the inferred cmake build dir(s) once all build-dir-dependent phases
        # have run (or the scan aborted). Best-effort (each thunk is suppressed) so a
        # removal/unlock error never aborts the rest nor masks the real outcome.
        drain_build_dir_cleanups(build_dir_cleanups)

    outcome = core.outcome
    return ScanResult(
        verdict=outcome.verdict,
        exit_code=outcome.exit_code,
        findings=core.findings,
        layers=_layers_from_coverage(outcome.coverage),
        confidence={
            k: list(v) for k, v in outcome.crosscheck.get("providers", {}).items()
        },
        estimate=estimate_scan(req),
        report=outcome.to_dict(),
    )


def run_audit(req: ScanRequest) -> ScanResult:
    """Single-release hygiene audit — :func:`run_scan` with the AUDIT mode (no
    baseline, ADR-035 D8). A thin convenience wrapper so callers can name intent."""
    from dataclasses import replace

    return run_scan(replace(req, mode="audit", baseline=None))


def _scan_subprocess_worker(req: ScanRequest, q: Any) -> None:
    """Child-process entry: run the scan and ship back the JSON-able result dict.

    Detaches into its own process group (POSIX) so the parent can kill the whole
    subtree — including any clang/castxml grandchildren — on timeout. Conveys a
    sanitized ``(status, payload)`` pair; never lets an exception escape silently.
    """
    import os

    from . import deadline

    # _kill_process_tree's _descendant_pgids() walk is a point-in-time snapshot taken before proc.terminate() fires;
    # a clang/castxml child this worker spawns via deadline.run_bounded() in the gap between that snapshot and the
    # terminate() call is invisible to it. Without this, proc.terminate() (default SIGTERM disposition, since this
    # is a fresh `spawn`-context process with no inherited handler) would kill the worker immediately, leaving that
    # just-spawned detached process group permanently orphaned — the worker's own _active_pgroups registry never
    # gets a handler chance to sweep it. install_sigterm_cleanup() gives this worker the same in-process cleanup
    # handler the plain CLI path and the L4 ProcessPoolExecutor workers already install, so its own SIGTERM handler
    # kills every group *it* has registered before the process actually exits — independent of what the outer
    # descendant-pgid snapshot did or didn't see (Codex review, PR #591, round 9).
    deadline.install_sigterm_cleanup()
    try:
        os.setsid()  # new process group; killpg(parent) reaches clang subprocs
    except (OSError, AttributeError):
        pass  # non-POSIX or already a leader — parent falls back to terminate()
    try:
        q.put(("ok", run_scan(req).to_dict()))
    except BaseException as exc:  # noqa: BLE001 — convey, don't crash the worker
        q.put(("err", f"{type(exc).__name__}: {exc}"))


def _descendant_pgids(root_pid: int) -> set[int]:
    """Every process-group id in *root_pid*'s live process tree (POSIX,
    best-effort). A clang/castxml child spawned via ``deadline.run_bounded``
    detaches into its own session/group, so a plain ``killpg(root_pgid)``
    no longer reaches it even though the PPID chain still leads back to
    *root_pid*. Walking a live ``ps -eo pid,ppid`` snapshot finds every such
    descendant's own pgid too, so it can be killed alongside the worker's
    group instead of surviving as an orphan (Codex review, PR #591). Empty
    set on any failure (missing ``ps``, non-POSIX, ...) -- never raises.
    """
    import os
    import subprocess

    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,ppid="],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    children: dict[int, list[int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        try:
            pid, ppid = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        children.setdefault(ppid, []).append(pid)
    pgids: set[int] = set()
    stack, seen = [root_pid], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        try:
            pgids.add(os.getpgid(pid))
        except (ProcessLookupError, PermissionError, AttributeError, OSError):
            pass
        stack.extend(children.get(pid, []))
    return pgids


def _kill_process_tree(proc: Any) -> None:
    """Terminate *proc* and every group in its process tree (best-effort,
    never raises). Killing only ``proc``'s own group used to miss a clang/
    castxml child detached into its own session via ``deadline.run_bounded``
    (needed so *its* inner-timeout ``killpg`` can't self-kill the worker,
    but also made it invisible to this outer ``killpg``).
    :func:`_descendant_pgids` finds it by PPID while ``proc`` is alive, so it
    gets killed here too instead of surviving as an orphan (Codex review,
    PR #591).
    """
    import os
    import signal

    if not proc.is_alive():
        return
    try:
        own_pgid = os.getpgrp()
    except (AttributeError, OSError):
        try:
            proc.terminate()
        except (OSError, AttributeError):
            pass
        proc.join(5)
        return
    pgids: set[int] = set()
    try:
        pgids.add(os.getpgid(proc.pid))
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        pass
    pgids |= _descendant_pgids(proc.pid)
    # Only kill *proc*'s own group when it actually detached into its own (``os. setsid`` ran). If it timed out
    # before that, its pgid still equals the parent's group — killpg would then terminate the MCP server itself, so
    # that group is excluded and only its (already-detached) descendants, if any, are targeted (Codex review).
    pgids.discard(own_pgid)
    # Unconditional: proc itself never detached (own_pgid was excluded above precisely because it's still in the
    # parent's group), so it is never reached by the killpg sweep below over *descendant* groups. Skipping this when
    # pgids is non-empty left the direct worker process running forever whenever it had spawned a detached
    # clang/castxml child but had not itself detached (CodeRabbit review, PR #591).
    try:
        proc.terminate()
    except (OSError, AttributeError):
        pass
    if not pgids:
        proc.join(5)
        return
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    proc.join(3)
    # Unconditional SIGKILL sweep, not gated on proc's own exit: a detached
    # clang/castxml session or a SIGTERM-ignoring grandchild can outlive the
    # grace period even once the direct worker has already exited (mirrors
    # deadline._kill_process_tree's same escalation fix).
    for pgid in pgids:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            pass
    proc.join(5)


def run_scan_subprocess(req: ScanRequest, timeout: float) -> dict[str, Any]:
    """Run :func:`run_scan` in a killable child process; return ``ScanResult.to_dict()``.

    The MCP server uses this so a deep/hung scan that exceeds the tool timeout is
    *terminated* (process + clang subtree) rather than orphaned to keep burning
    CPU after the timeout response is sent (ADR-035 / Codex review). Raises
    :class:`TimeoutError` on overflow and :class:`RuntimeError` on a worker-side
    failure (already sanitized to ``Type: message``).
    """
    import multiprocessing as mp
    import queue as _queue

    ctx = mp.get_context("spawn")  # no inherited locks/fds; portable
    q: Any = ctx.Queue()
    proc = ctx.Process(target=_scan_subprocess_worker, args=(req, q), daemon=True)
    proc.start()
    try:
        try:
            status, payload = q.get(timeout=timeout)
        except _queue.Empty:
            raise TimeoutError(f"scan exceeded {timeout:.0f}s") from None
    finally:
        if proc.is_alive():
            _kill_process_tree(proc)
        else:
            proc.join(1)
    if status == "err":
        raise RuntimeError(payload)
    return payload  # type: ignore[no-any-return]


def _run_scan_one_member(
    req: ScanRequest,
    binary: Path,
    *,
    start: float,
    budget_s: float | None,
    changed_src: str,
    sibling_exported_symbols: frozenset[str] = frozenset(),
) -> ScanResult:
    """One member's scan, for :func:`run_scan_set` (ADR-056). Mirrors
    :func:`run_scan`'s body (a separate function so `run_scan` stays
    byte-for-byte unchanged), with three differences: accepts an
    externally-supplied ``start``/``budget_s`` (the *same*, unreduced total
    passed to every member -- `run_scan_core`'s `_remaining_budget_s`
    computes the shrinking remainder itself, so a pre-reduced value here
    would double-subtract); forwards `abi3_floor`/`enabled_checks`/
    `severities`/`build_config`/`allow_build_query`/`risk_rules_path`/
    `build_targets`; accepts ``sibling_exported_symbols`` (G35: a sibling's
    export also satisfies `public_not_exported`).
    """
    from .buildsource.crosscheck import ALL_CHECKS
    from .buildsource.scan_levels import EvidenceDepth, ScanMode, SourceMethod
    from .scan_engine import _BudgetOverflow, _EvidenceContractError, run_scan_core
    from .workflows.scan_config import public_provenance_set as _public_provenance_set

    # Shared with the --artifact-set --dry-run preview (workflows.scan_estimate) -- workflows/AGENTS.md's
    # "dry-run and execution must consume the same resolved plan" rule.
    sm, dp, changed, seeded, risk, resolved, eff_depth, collect_mode = (
        _resolve_member_scan_level(req)
    )

    scan_mode = ScanMode(req.mode)
    sm_pin = sm is not None and sm is not SourceMethod.AUTO
    pinned_explicit = (dp is not None) or sm_pin
    # Mirrors cli_scan._scan_explicit_flags' level_explicit: consent to auto-running build.query (a non-auto
    # source_method, or an explicit depth with no source_method pinned) -- without this, a trusted --config's
    # build.query never auto-runs for a member even when the caller explicitly requested a deep depth (Codex
    # review).
    level_explicit = sm_pin or (sm is None and dp is not None)
    is_auto = sm is SourceMethod.AUTO
    eff_headers = [] if eff_depth is EvidenceDepth.BINARY else list(req.headers)
    prov_headers, prov_dirs = _public_provenance_set(
        eff_headers, list(req.public_header_dirs)
    )
    effective_build_info = req.compile_db or req.build_info
    budget_str = f"{budget_s:g}s" if budget_s is not None else None

    build_dir_cleanups: list[Callable[[], None]] = []
    try:
        core = run_scan_core(
            start=start,
            binary=binary,
            headers=eff_headers,
            includes=list(req.includes),
            public_headers=prov_headers,
            public_header_dirs=prov_dirs,
            sources=req.sources,
            effective_build_info=effective_build_info,
            build_config=req.build_config,
            baseline=None,
            lang=req.lang,
            allow_build_query=req.allow_build_query,
            scan_mode=scan_mode,
            resolved=resolved,
            eff_depth_enum=eff_depth,
            collect_mode=collect_mode,
            changed=changed,
            changed_src=changed_src,
            seeded=seeded,
            risk=risk,
            is_auto=is_auto,
            enabled_checks=(
                req.enabled_checks
                if req.enabled_checks is not None
                else frozenset(ALL_CHECKS)
            ),
            severities=dict(req.severities),
            budget=budget_str,
            budget_s=budget_s,
            level_explicit=level_explicit,
            pinned_explicit=pinned_explicit,
            compile_context=None if req.compile.is_default else req.compile,
            defer_cleanup=build_dir_cleanups,
            abi3_floor=req.abi3_floor,
            sibling_exported_symbols=sibling_exported_symbols,
            build_targets=req.build_targets,
        )
    except _BudgetOverflow:
        verdict, exit_code, report = scan_abort_result_fields("budget_overflow")
        return ScanResult(verdict=verdict, exit_code=exit_code, report=report)
    except _EvidenceContractError:
        verdict, exit_code, report = scan_abort_result_fields("evidence_contract_error")
        return ScanResult(verdict=verdict, exit_code=exit_code, report=report)
    finally:
        drain_build_dir_cleanups(build_dir_cleanups)

    outcome = core.outcome
    return ScanResult(
        verdict=outcome.verdict,
        exit_code=outcome.exit_code,
        findings=core.findings,
        layers=_layers_from_coverage(outcome.coverage),
        confidence={
            k: list(v) for k, v in outcome.crosscheck.get("providers", {}).items()
        },
        estimate=[],
        report=outcome.to_dict(),
    )


def run_scan_set(req: ScanRequest) -> ScanSetResult:
    """Execute an audit-mode, no-old-side scan over a *set* of artifacts
    (ADR-056, ``scan --artifact-set``). The plural sibling of :func:`run_scan`,
    sharing `ScanRequest` but never touching `run_scan`'s own code path --
    `req.binaries` must have 2+ entries. `req.baseline` must be ``None``: a
    service-layer guard so a directly-constructed `ScanRequest(binaries=
    [...], baseline=old)` can't silently compare every member against the
    same baseline (ADR-056 D2 scopes `--artifact-set` to audit-only).
    """
    import time as _time
    from dataclasses import replace

    from . import deadline as _deadline
    from .bundle import (
        artifact_set_member_exports,
        audit_bundle,
        check_artifact_set_soname_collisions,
        discover_artifact_set,
    )

    # run_scan_set is audit-only by definition (ADR-056 D2) -- normalize mode here rather than trust every caller to
    # set it. Without this, the documented minimal form ``run_scan_set(ScanRequest(binaries=[...]))`` left req.mode
    # at ScanRequest's own default ("pr"), so each member's report claimed mode: "pr" despite this entry point never
    # accepting a baseline (Codex review).
    req = replace(req, mode="audit")

    # Canonicalize/deduplicate before the cardinality check: two literal duplicates or symlink aliases of one DSO
    # must not silently pass as a valid 2-member set (discover_artifact_set below dedupes via Path.resolve() too,
    # which would otherwise report a "complete" audit of what's really a single library) (Codex review).
    # Path.resolve() only follows symlinks, not hard links -- key on filesystem identity (st_dev, st_ino) when
    # available so two hard-linked aliases of one DSO are also caught, not just symlink aliases (Codex review, same
    # gap fixed in bundle.discover_artifact_set's own dedup).
    seen_resolved: set[Path | tuple[int, int]] = set()
    binaries: list[Path] = []
    for binary in req.binaries:
        try:
            resolved = binary.resolve()
        except OSError:
            resolved = binary
        try:
            st = resolved.stat()
            identity: Path | tuple[int, int] = (st.st_dev, st.st_ino)
        except OSError:
            identity = resolved
        if identity in seen_resolved:
            continue
        seen_resolved.add(identity)
        binaries.append(binary)

    if len(binaries) < 2:
        raise ValueError(
            "run_scan_set requires 2 or more distinct binaries "
            "(duplicate/symlink-aliased paths do not count separately)"
        )
    if req.baseline is not None:
        raise ValueError("run_scan_set does not accept req.baseline (audit-only)")
    # P2 regression (Codex review): run_scan_set is a public, re-exported service entry point (ADR-056) that a
    # direct Python API caller can reach without going through cli_scan._run_artifact_set's own click-level "these
    # flags only mean anything with --against" guard -- req.mode is already forced to "audit" above, so this always
    # applies here (unlike run_scan, where it's conditional on baseline/mode).
    _reject_comparison_only_fields(req)

    # Start the shared budget clock *before* set discovery/validation, not after (Codex review):
    # discover_artifact_set() stats every candidate path and parses each one's ELF program/dynamic table to classify
    # it, real work on a large set -- if the clock only started once that finished, a slow discovery phase would be
    # invisible to `--budget` entirely, so even `--budget 0s` could spend substantial time discovering before ever
    # reporting overflow.
    start = _time.monotonic()
    budget_s = req.budget.total_timeout

    # Validate/canonicalize the whole set *before* scanning any member (Codex review): run_scan_set is a public, re-
    # exported service entry point (ADR-056) -- a direct Python API caller can reach it without ever going through
    # cli_scan._run_artifact_set's own discover_artifact_set prevalidation, so an unsupported member or a canonical-
    # identity collision here is not necessarily anomalous the way it would be for a CLI-originated request.
    # Discovering first avoids two real problems with discovering after the per-member loop: (1) needlessly running
    # potentially expensive member scans (compiler/build queries) for a request that was never valid to begin with,
    # and (2) a member scan exhausting the budget and returning BUDGET_OVERFLOW *before* discovery ever runs, which
    # would report budget exhaustion instead of the real, underlying ArtifactSetError.
    libraries = discover_artifact_set(list(binaries), explicit=True)

    # P2 regression (Codex review): validate cross-member DT_SONAME uniqueness before scanning any member, not only
    # inside audit_bundle() after every member has already been individually scanned -- an earlier member scan
    # exhausting --budget would otherwise mask this genuine usage error as an ordinary BUDGET_OVERFLOW, and every
    # member's own (potentially expensive) scan would already have run for a request that was always going to be
    # rejected.
    remaining_for_soname_check = (
        None if budget_s is None else budget_s - (_time.monotonic() - start)
    )
    if remaining_for_soname_check is not None and remaining_for_soname_check <= 0:
        return ScanSetResult(verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=[])
    try:
        with _deadline.deadline_scope(remaining_for_soname_check):
            check_artifact_set_soname_collisions(libraries)
    except _deadline.DeadlineExceeded:
        return ScanSetResult(verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=[])

    # G35: cheap export-table pass so a member's own check learns siblings' exports.
    remaining_for_exports = (
        None if budget_s is None else budget_s - (_time.monotonic() - start)
    )
    if remaining_for_exports is not None and remaining_for_exports <= 0:
        return ScanSetResult(verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=[])
    try:
        with _deadline.deadline_scope(remaining_for_exports):
            member_exports = artifact_set_member_exports(libraries)
    except _deadline.DeadlineExceeded:
        return ScanSetResult(verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=[])
    # No checkpoint after the *last* member's parse -- recheck before the loop.
    if budget_s is not None and (_time.monotonic() - start) >= budget_s:
        return ScanSetResult(verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=[])

    all_exports = frozenset[str]().union(*member_exports.values())
    per_artifact: list[ScanArtifactResult] = []
    for name, binary in libraries.items():
        siblings = all_exports - member_exports.get(name, frozenset())
        result = _run_scan_one_member(
            req,
            binary,
            start=start,
            budget_s=budget_s,
            changed_src=req.changed_src,
            sibling_exported_symbols=siblings,
        )
        per_artifact.append(ScanArtifactResult(artifact=binary, result=result))
        if result.verdict == "BUDGET_OVERFLOW":
            # The failure-guard contract: never keep scanning past overflow.
            return ScanSetResult(
                verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=per_artifact
            )

    remaining = None if budget_s is None else budget_s - (_time.monotonic() - start)
    if remaining is not None and remaining <= 0:
        return ScanSetResult(
            verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=per_artifact
        )

    try:
        # P2 regression (Codex review): the post-audit elapsed-time check below only detects an overflow *after*
        # audit_bundle() already ran to completion -- it doesn't bound the call itself, so a pathologically large or
        # malformed ELF set could still burn far more wall-clock than --budget allows before that check is ever reached.
        # build_bundle_snapshot()'s per-library parsing loop now calls deadline.check() between members (the same
        # cooperative, per-unit-of-work checkpoint pattern scan_engine already uses for per-header/per-TU work), so
        # running audit_bundle() under this scope lets a real overflow raise mid-parse instead of only being caught
        # afterward.
        #
        # audit_bundle()'s ambiguous duplicate-SONAME rejection (ArtifactSetError) is only detectable *here*, after
        # actually parsing every member's ELF metadata -- propagates the same way discover_artifact_set()'s own
        # ArtifactSetError does above (P2, Codex review x2): degrading either to bundle_incomplete=True let a genuinely
        # invalid artifact set exit 0 with the cross-library audit silently skipped, misreading as full success for a
        # direct Python API caller that never went through cli_scan._run_artifact_set's own prevalidation. The CLI's own
        # try/except around run_scan_set() converts this into a click.UsageError (exit 64, "bad flags/inputs" per
        # AGENTS.md's exit code table).
        with _deadline.deadline_scope(remaining):
            audit = audit_bundle(
                libraries, bundle_system_providers=req.bundle_system_providers
            )
    except _deadline.DeadlineExceeded:
        return ScanSetResult(
            verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=per_artifact
        )
    # The deadline_scope above only bounds the checkpoints build_bundle_snapshot() itself calls deadline.check()
    # between (i.e. per library, not mid-parse of one) -- re-check elapsed time after the whole call so a real
    # overflow within a single library's parse (or in _compute_resolution_graph/
    # _detect_unresolved_intra_dependency, which have no checkpoints of their own) is still reported as
    # BUDGET_OVERFLOW (the failure-guard contract) rather than a normal verdict that quietly ran over time (Codex
    # review).
    if budget_s is not None and (_time.monotonic() - start) > budget_s:
        return ScanSetResult(
            verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=per_artifact
        )
    # discover_artifact_set already validated every member looks like ELF, but build_bundle_snapshot() can still
    # silently drop one whose actual parse failed or came back empty (bundle.py's own per-member try/except + empty-
    # metadata skip). A reduced resolution graph missing a real provider can then invent an unresolved-import
    # finding for a symbol that dropped member would have supplied -- mark the audit incomplete (no findings
    # published) rather than risk a false positive (Codex review), the same degrade-not-raise contract the
    # discovery-failure path above already uses.
    if set(audit.snapshot.libraries) != set(libraries):
        verdict, exit_code = _aggregate_scan_set_verdict(per_artifact, None)
        # P2 regression (Codex review): bundle_incomplete previously left the exit code purely a function of the per-
        # member verdicts -- the cross-library audit itself never ran, but a set where every member scanned clean
        # (COMPATIBLE/NO_CHANGE/COMPATIBLE_WITH_RISK) still exited 0, so a CI gate that only checks the exit code
        # silently accepted a skipped audit as a full pass. Floor the exit code at 1 (mirroring the per-member
        # EVIDENCE_CONTRACT_ERROR contract in ``_aggregate_scan_set_verdict`` above) and surface a dedicated
        # BUNDLE_INCOMPLETE verdict when no worse, already-dominant member problem
        # (API_BREAK/BREAKING/EVIDENCE_CONTRACT_ERROR/BUDGET_OVERFLOW) is already being reported.
        exit_code = max(exit_code, 1)
        if verdict in ("NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK"):
            verdict = "BUNDLE_INCOMPLETE"
        return ScanSetResult(
            verdict=verdict,
            exit_code=exit_code,
            per_artifact=per_artifact,
            bundle_incomplete=True,
        )
    bundle_verdict = audit.verdict.value
    verdict, exit_code = _aggregate_scan_set_verdict(per_artifact, bundle_verdict)
    return ScanSetResult(
        verdict=verdict,
        exit_code=exit_code,
        per_artifact=per_artifact,
        bundle_findings=list(audit.findings),
        bundle_verdict=bundle_verdict,
    )


def _scan_set_subprocess_worker(req: ScanRequest, q: Any) -> None:
    """Child-process entry for :func:`run_scan_set_subprocess` (ADR-056).
    Mirrors :func:`_scan_subprocess_worker`'s process-group detachment and
    SIGTERM cleanup verbatim -- same race, same fix: without these, a
    clang/castxml child a member scan spawns can detach into its own group
    in the gap between the parent's descendant-pgid snapshot and its
    terminate() call, and outlive the worker as an orphan (Codex review).
    """
    import os

    from . import deadline

    deadline.install_sigterm_cleanup()
    try:
        os.setsid()
    except (OSError, AttributeError):
        pass
    try:
        q.put(("ok", run_scan_set(req).to_dict()))
    except ValueError as exc:
        q.put(("value_err", str(exc)))
    except BaseException as exc:  # noqa: BLE001 — convey, don't crash the worker
        q.put(("err", f"{type(exc).__name__}: {exc}"))


def run_scan_set_subprocess(req: ScanRequest, timeout: float) -> dict[str, Any]:
    """Run :func:`run_scan_set` in a killable child process (ADR-056).

    The plural sibling of :func:`run_scan_subprocess` — MCP `abi_scan` must
    route an `artifact_set` call through this, not the singular wrapper, so
    the MCP tool timeout still terminates the process tree for a hung
    multi-artifact scan instead of either being rejected outright or running
    unbounded in the MCP server's own process.
    """
    import multiprocessing as mp
    import queue as _queue

    ctx = mp.get_context("spawn")
    q: Any = ctx.Queue()
    proc = ctx.Process(target=_scan_set_subprocess_worker, args=(req, q), daemon=True)
    proc.start()
    try:
        try:
            status, payload = q.get(timeout=timeout)
        except _queue.Empty:
            raise TimeoutError(f"scan exceeded {timeout:.0f}s") from None
    finally:
        if proc.is_alive():
            _kill_process_tree(proc)
        else:
            proc.join(1)
    if status in ("value_err", "err"):
        raise (ValueError if status == "value_err" else RuntimeError)(payload)
    return payload  # type: ignore[no-any-return]
