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

ADR-035 D10 / G19.7 (Phase 3b). One typed contract — :class:`ScanRequest` →
:class:`ScanResult` / ``[CostEstimate]`` — that the CLI (`cli_scan.py`), the MCP
server, and CI wrappers all drive, so there is one engine and many renderings.

This is a leaf module (it must not import from :mod:`abicheck.service`); the
header-expansion helper it shares with the input-resolution path
(:func:`expand_header_inputs`) lives here and is re-exported by ``service`` for
backward compatibility.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .buildsource.build_query import (
    PRUNED_HEADER_DIR_SEGMENTS,
    drain_build_dir_cleanups,
)
from .compile_context import CompileContext as CompileContext  # re-exported, ADR-055 D1
from .errors import ValidationError
from .header_utils import HEADER_SUFFIXES, iter_directory_headers
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


# ── Scan service: typed request/result + per-project cost estimate ───────────
#
# ADR-035 D10 / G19.7 (Phase 3b). One typed contract — :class:`ScanRequest` →
# :class:`ScanResult` / ``[CostEstimate]`` — that the CLI (`cli_scan.py`), the MCP
# server, and CI wrappers all drive, so there is one engine and many renderings.
# ``estimate_scan`` is a first-class **dry-run** (ADR-035 D10): it probes the
# project (TU count, header fan-out, cache state) and returns the projected cost
# of each L-layer for *this* project so a maintainer can pick a depth on measured
# cost instead of guesswork — it scans nothing and runs no compiler.


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


# CompileContext itself now lives in the leaf `compile_context` module
# (ADR-055 D1, imported at the top of this file and re-exported here) so a
# module outside this file's import-cycle-allowlisted cluster (api_types.py,
# in particular) can depend on the type without joining the cluster itself.


def pair_wide_cxx20_std_override(
    lang: str,
    old_headers: Iterable[Path],
    new_headers: Iterable[Path],
    gcc_options: str | None,
    gcc_option_tokens: tuple[str, ...],
) -> tuple[str, ...] | None:
    """Pair-wide C++20 dialect decision, shared by every compare front-end
    (P0 fix — CLI ``compare`` via ``cli_helpers_compare._pair_wide_dialect_override``,
    the Python-API/MCP ``run_compare_request`` path).

    ``dumper.py``'s C++20 ``requires``/``concept`` heuristic only ever sees ONE
    side's headers at a time (each side is dumped independently), so an
    old/new pair could silently disagree on the language standard whenever
    neither side pins an explicit one — e.g. only the *new* side picks up a
    ``concept`` and gets auto-upgraded to C++20 while *old* stays on the
    toolchain default. That lets a real dialect-floor change masquerade as an
    ordinary ABI diff (or, the inverse historical bug: a header containing
    ``#error Foo requires Base`` tripped the heuristic on whichever side had
    that text).

    Decides the heuristic once, over the union of both sides' headers, so a
    caller can pin the identical result onto both sides' compile context. An
    explicit ``-std=``/``--std=``/``/std:`` from the user always wins
    (``has_explicit_std`` short-circuits first); returns ``None`` in that case
    and whenever no override is needed — never overriding an explicit choice.
    Lives here (not in a CLI-layer module) so every front-end — CLI, Python
    API, MCP — can share one implementation without a CLI-layer dependency
    reaching into the Tier-2 service layer.
    """
    from ._compiler_options import has_explicit_std
    from .dumper_ast_config_cpp20 import _detect_cpp20_headers

    old_h = list(old_headers)
    new_h = list(new_headers)
    if lang.lower() != "c++" or not (old_h or new_h):
        return None
    if has_explicit_std(gcc_options, gcc_option_tokens):
        return None
    if not _detect_cpp20_headers(old_h + new_h):
        return None
    return ("-std=gnu++20",)


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
    # --against config-surface parity with `compare` (ADR-049 Phase 5 §6.4):
    # a `baseline` comparison can be scoped/suppressed/policy-classified the
    # same way a direct `compare` run is, instead of always using the fixed
    # policy="strict_abi"/suppression=None/scope_to_public_surface=True the
    # engine previously hardcoded.
    suppression: SuppressionList | None = None
    policy: str = "strict_abi"
    policy_file: PolicyFile | None = None
    scope_to_public_surface: bool = True
    force_public_symbols: set[str] | None = None
    pattern_verdicts: bool = False
    env_matrix: EnvironmentMatrix | None = None
    collapse_versioned_symbols: bool = False
    # ADR-056: options the single-binary CLI path (cli_scan.py) already
    # forwards straight to run_scan_core, bypassing ScanRequest entirely.
    # Both run_scan and run_scan_set (--artifact-set) honor these when set
    # directly on a ScanRequest (P2 regression, Codex review: run_scan used
    # to silently ignore them, so a direct Python API caller passing
    # abi3_floor/enabled_checks/severities/build_config/allow_build_query/
    # risk_rules_path got a differently-gated result than requested).
    abi3_floor: tuple[int, int] | None = None
    enabled_checks: frozenset[str] | None = None  # None = ALL_CHECKS default
    severities: dict[str, str] = field(default_factory=dict)
    build_config: Path | None = None
    allow_build_query: bool = False
    risk_rules_path: Path | None = None
    # ADR-056 D2: caller-declared external DSO allow-list for the
    # --artifact-set audit-mode bundle detector (closed-world escape hatch).
    # Unused by run_scan.
    bundle_system_providers: tuple[str, ...] = ()
    # ADR-056: real changed-path provenance (e.g. "--since origin/main" or
    # "--changed-path"), as computed by cli_scan._resolve_changed_seed.
    # run_scan_set forwards this into each member's report instead of a
    # hardcoded placeholder; unused by run_scan (which threads its own
    # changed_src through _run_scan_one_member's caller directly).
    changed_src: str = "run_scan_set"


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


#: Per-TU / per-file cost anchors (seconds) for the dry-run estimate. These are
#: deliberately coarse starting defaults (§11 of the ADR-035 proposal: a full
#: ``-fsyntax-only`` pass dominates; pattern/compile-DB scans are <1-5%). The real
#: per-project number comes from the actual run; the estimate only ranks layers so
#: a maintainer can pick a depth.
_COST_PER_HEADER_PARSE = 0.08  # L2 base: castxml/clang startup + preprocess per header
#: L2 marginal cost per KB of header text. A flat per-header anchor priced a
#: one-line shim and a 200 KB templated umbrella (ICU's ``unicode/*.h``,
#: hdf5's C++ API) identically, so a large public surface was under-ranked and
#: ``scan --estimate`` understated header-audit cost. Weighting by on-disk size
#: ranks heavy headers above trivial ones (field-eval P1: ICU/HDF5 header scans
#: took 80-180 s while the estimate read flat).
_COST_PER_HEADER_KB = 0.004
_COST_PER_TU_BUILD = 0.002  # L3 compile-DB entry parse
# Cold L4 is a full clang JSON-AST replay + Python JSON parse + macro pass per TU.
# Real-world pvxs/oneDAL validation showed ~7.5s/TU cold; the old 0.45s/TU anchor
# under-promised source/full scans by an order of magnitude and made 100+ TU runs
# look like one-minute jobs. Warm cache is reported by live coverage; dry-run stays
# conservative unless a future cache probe can prove hits up front.
_COST_PER_TU_REPLAY = 7.5  # L4 per-TU semantic AST replay, cold-cache default
_COST_PER_TU_GRAPH = 0.02  # L5 per-TU graph fold/edge


#: A flat size-based estimate prices a one-line ``#include`` umbrella and a
#: heavily-templated header identically per KB — real-world field evidence (the
#: SVS ``datatype.h``/``float16.h``/``meta.h`` trio) showed a dry-run estimate
#: of 0.51s for headers whose actual parse ran over 15,000s before an external
#: SIGKILL: none of that cost is visible in on-disk bytes, it comes from
#: template/include fan-out the compiler has to instantiate. These are a cheap,
#: local (no compiler invocation) peek for that signal so the dry-run estimate
#: can flag it instead of reporting a falsely precise number.
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
    constexpr usage in *path* signal a header whose real parse time a flat
    size-based estimate systematically under-prices (recursive template
    instantiation is not linear in header bytes — the SVS pathological case had
    small headers and a near-unbounded real parse time). Never raises; an
    unreadable header is treated as unknown risk (not flagged), matching the
    size estimate's own fall back to the base cost alone.
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
    """Size-aware L2 cost, plus a complexity-risk flag.

    Returns ``(seconds, high_risk)``: *seconds* is a fixed per-header parse/
    preprocess base plus a marginal per-KB term over each header's on-disk
    size — inflated by :data:`_COMPLEXITY_MULTIPLIER` for any header that trips
    :func:`_header_complexity_signal`, so a small-but-pathological header no
    longer prices as a fraction of a second. *high_risk* is True when any
    header tripped the signal — callers surface this as an explicit caveat
    instead of a falsely precise point estimate (P0 SVS field report).

    Falls back to the base alone when a path can't be stat'd (a symlink/glob
    that no longer resolves) so the estimate never raises mid-dry-run.
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
    # Deduplicate on the *resolved* path (file joined with its entry directory):
    # a compile DB commonly stores relative `file` names under different
    # `directory` entries, so two distinct TUs (/proj/a/main.cpp, /proj/b/main.cpp)
    # both read as a bare `main.cpp` and would collapse to one — undercounting the
    # TUs the real scan (which normalizes via load_compile_db) replays (Codex).
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
    """The compile DB to estimate against: explicit wins, else discover in *sources*.

    An explicit ``--compile-db``/``--build-info`` that points at a *directory*
    (a supported scan input, e.g. ``build/`` holding a ``compile_commands.json``)
    is resolved to the contained DB — otherwise the directory itself flows into
    :func:`_count_compile_db_tus`, which fails the read and reports 0 TUs, making
    L3/L4/L5 near-free even though the real scan replays the directory's DB
    (Codex review).
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
        from .buildsource.inline import is_pack_dir
        from .buildsource.pack import BuildSourcePack

        if not is_pack_dir(path):
            return None
        pack = BuildSourcePack.load(path)
    except Exception:  # noqa: BLE001 - estimate is advisory; never raise on a bad pack
        return None
    be = pack.build_evidence
    return len(be.compile_units) if be is not None else 0


def _count_bazel_build_info_tus(path: Path) -> int | None:
    """Compile-unit count of a Bazel ``aquery``/``cquery`` ``--build-info``, else ``None``.

    The real scan routes a Bazel jsonproto ``--build-info`` through
    ``inline._maybe_collect_bazel_build_info`` → ``BazelAdapter`` (pre-captured,
    ``allow_query=False``) and replays its compile actions; the estimate mirrors
    that so a Bazel project does not report 0 L3/L4/L5 TUs and undersize the budget
    (Codex review). Non-executing (parses the captured JSON only); best-effort —
    any failure → ``None`` so the caller falls back to compile-DB / source counting.
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
        # The caller (the CLI scan path) already resolved the concrete (method,
        # depth) level — including the auto/risk choice. Honor it verbatim so the
        # estimate matches the real scan: re-resolving from req.source_method/depth
        # here would re-apply the source-method > depth precedence and collapse a
        # mode preset that pins a *deeper* depth than its method implies
        # (``pr-deep`` = (s5, graph) → graph-full), under-pricing it (Codex review).
        resolved, eff_depth = resolved_level
    else:
        sm = SourceMethod(req.source_method) if req.source_method else None
        dp = parse_user_depth(req.depth)  # honors the symbols→binary alias (Codex)
        auto_method = None
        # AUTO resolves from the risk score whenever a real diff seed was produced —
        # including a *seeded but empty* diff (a no-op PR), which scores 0 → s0/off,
        # mirroring what the real scan does. Treating a seeded empty diff as unseeded
        # would fall back to the mode preset and over-estimate a no-op PR (Codex
        # review). A non-empty changed set is itself proof of a seed.
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
    # Count TUs from the *same* effective build-info the real scan uses
    # (`req.compile_db or req.build_info`) so an explicit --compile-db wins over a
    # Bazel --build-info here too — else the estimate could price a different action
    # graph than the scan executes (Codex review). A pack dir supplies its own L3
    # compile units; a Bazel aquery/cquery jsonproto is routed through the Bazel
    # adapter; a raw compile DB / source tree is counted otherwise.
    eff_build_info = req.compile_db or req.build_info
    bazel_tus = (
        _count_bazel_build_info_tus(eff_build_info)
        if eff_build_info is not None
        else None
    )
    pack_tus = _count_pack_tus(eff_build_info) if eff_build_info is not None else None
    compile_db = _discover_compile_db(req.sources, eff_build_info)
    if bazel_tus is not None:
        return bazel_tus, "Bazel aquery/cquery (build_evidence)"
    if pack_tus is not None:
        return pack_tus, "build-source pack (build_evidence)"
    if compile_db is not None:
        return _count_compile_db_tus(compile_db), f"compile DB: {compile_db.name}"
    if req.sources is not None:
        return _count_source_tus(req.sources), "counted source files (no compile DB)"
    return 0, "no source tree / compile DB"


def _estimate_replay_tus(req: ScanRequest, collect_mode: str, total_tus: int) -> int:
    """TUs the L4 replay (and its clang call-graph pass) would touch."""
    # The L4 replay scope: a changed-only collection touches at most the changed
    # *source* TUs (POI-focused, D7); a full/target scope touches every TU. The
    # budget's max_tus is a documented cap (never shrinks scope silently — it
    # FAILS — but the estimate honestly reflects the cap as the upper bound).
    #
    # A changed *header* fans out: without an include graph (the common
    # compile-DB-only path) ``source_replay.select_compile_units(scope='changed')``
    # fails open to **all** TUs so header ABI changes are never silently missed,
    # so the estimate must charge ``total_tus`` for a header change rather than
    # the single header path — else it understates L4 cost and a user picks too
    # small a budget (Codex review). An empty/seedless diff is likewise broad.
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
) -> list[CostEstimate]:
    """The collect-mode-dependent L3/L4/L5 rows (source-evidence layers)."""
    # "source-target" (ADR-043 D2/D3) is the unseeded sibling of "source-changed"
    # — same L3/L4/L5 layers, just a broader (target-scoped, not changed-only)
    # replay; it must price identically to source-changed everywhere below, else
    # an unseeded explicit-source scan/estimate silently reports zero source
    # layers (the same zero-TU defect the collect-mode fix addresses).
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
                f"{collect_mode} replay scope ({replay_tus} of {total_tus} TU(s))",
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
                "source graph fold/edges",
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
                f"call-graph clang pass ({replay_tus} of {total_tus} TU(s))",
            )
        )
    return estimates


def estimate_scan(
    req: ScanRequest,
    *,
    resolved_level: tuple[SourceMethod, EvidenceDepth] | None = None,
) -> list[CostEstimate]:
    """Dry-run: projected per-layer cost of *req* for this project (ADR-035 D10).

    Probes the project (TU count from the compile DB or source tree, public-header
    fan-out, the resolved level's collect mode) and returns one
    :class:`CostEstimate` per L-layer the chosen level would touch — **without
    running any compiler or parsing any binary**. The numbers are coarse anchors
    (see ``_COST_PER_*``); the estimate's job is to *rank* layers so a maintainer
    can pick a depth/budget, not to be a precise wall-clock prediction.
    """
    resolved, eff_depth, collect_mode = _resolve_estimate_level(req, resolved_level)
    total_tus, tu_note = _estimate_total_tus(req)
    replay_tus = _estimate_replay_tus(req, collect_mode, total_tus)
    estimates = _intrinsic_layer_estimates(req, eff_depth)
    estimates.extend(
        _source_layer_estimates(resolved, collect_mode, total_tus, tu_note, replay_tus)
    )
    return estimates


@dataclass(frozen=True)
class ScanResult:
    """Typed result of an executed scan (ADR-035 D10) — the one object the CLI,
    the MCP server, and library callers consume.

    ``diff`` is the full report payload (``None``-free); ``findings`` are the raw
    cross-source :class:`Change` objects; ``layers`` is the per-layer coverage;
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
    carries a binary path or library name anywhere).
    """

    artifact: Path
    result: ScanResult

    def to_dict(self) -> dict[str, Any]:
        return {"artifact": str(self.artifact), **self.result.to_dict()}


# ADR-056 D3: ScanSetResult's own explicit verdict/exit-code precedence —
# deliberately NOT a reuse of compare-release's `_RELEASE_VERDICT_ORDER`
# (`cli_compare_release_helpers.py`), which has no entries for the two
# scan-specific failure verdicts (`BUDGET_OVERFLOW`/`EVIDENCE_CONTRACT_ERROR`)
# a ScanResult can carry. See _aggregate_scan_set_verdict's docstring.
_SCAN_SET_COMPAT_ORDER: dict[str, int] = {
    "NO_CHANGE": 0,
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
       exit 5. Dominates every other outcome (ADR-050 D2's ``not_comparable``
       precedent: a member whose analysis didn't finish is worse than one
       that finished and found a break, because its true result is unknown).
    2. Else, the worst compatibility verdict across `per_artifact` + the
       bundle layer's own verdict (`_SCAN_SET_COMPAT_ORDER`), with the
       matching exit code.
    3. Any member ``EVIDENCE_CONTRACT_ERROR`` floors the exit code at 1
       without lowering a worse one from step 2, and — mirroring the exit
       code — becomes the reported verdict *only* when step 2's worst
       compatibility verdict was NO_CHANGE/COMPATIBLE/COMPATIBLE_WITH_RISK
       (i.e. the error is the dominant problem); a stronger API_BREAK/
       BREAKING from step 2 keeps that verdict string.
    """
    if any(a.result.verdict == "BUDGET_OVERFLOW" for a in per_artifact):
        return "BUDGET_OVERFLOW", 5

    worst_verdict = "NO_CHANGE"
    worst_rank = 0
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
            # Full finding records (kind/symbol/consumer/provider/description),
            # not just a count -- mirrors cli_compare_release_helpers.py's
            # summary["bundle_findings"] JSON shape for the two-sided release
            # path, so a machine consumer of either can identify and act on
            # the condition that produced bundle_verdict, not just its count
            # (Codex review).
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


def run_scan(req: ScanRequest) -> ScanResult:
    """Execute a scan and return a typed :class:`ScanResult` (ADR-035 D10).

    The single engine entry point behind the ``scan`` CLI and the MCP scan tool:
    it resolves the deterministic level from *req* (the same way
    :func:`estimate_scan` does), drives the shared orchestration core
    (``scan_engine.run_scan_core`` — classify → always-on tier → pinned level →
    optional baseline compare), and folds the projected ``estimate_scan`` cost in
    so a caller can compare projected vs. actual. ``--budget`` overflow surfaces as
    ``exit_code`` 5 (the failure-guard contract; never shrinks scope).
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
    from .cli_scan_baseline import _load_risk_rules, _public_provenance_set
    from .scan_engine import (
        _BudgetOverflow,
        _EvidenceContractError,
        run_scan_core,
    )

    if len(req.binaries) != 1:
        raise ValueError("run_scan accepts exactly one binary")
    binary = req.binaries[0]

    # ADR-049 Phase 5 review (Codex, PR #657): these fields only mean
    # anything for a baseline comparison (`run_scan_core` only calls
    # `_run_baseline_compare` when `baseline is not None` AND `scan_mode is
    # not ScanMode.AUDIT`) -- without one they'd be silently accepted and
    # discarded, which could hide a `policy_file` requiring evidence the
    # caller actually needed. Mirrors the CLI's identical `scan_cmd` guard.
    if req.baseline is None or ScanMode(req.mode) is ScanMode.AUDIT:
        _non_default = [
            name
            for name, is_set in (
                ("suppression", req.suppression is not None),
                ("policy", req.policy != "strict_abi"),
                ("policy_file", req.policy_file is not None),
                ("scope_to_public_surface", req.scope_to_public_surface is not True),
                ("force_public_symbols", bool(req.force_public_symbols)),
                ("pattern_verdicts", req.pattern_verdicts),
                ("env_matrix", req.env_matrix is not None),
                ("collapse_versioned_symbols", req.collapse_versioned_symbols),
            )
            if is_set
        ]
        if _non_default:
            raise ValidationError(
                f"ScanRequest field(s) {', '.join(_non_default)} only take "
                "effect with a baseline comparison (req.baseline set, and "
                "req.mode not 'audit'); they configure compare_snapshots and "
                "have no effect otherwise."
            )
    sm = SourceMethod(req.source_method) if req.source_method else None
    dp = parse_user_depth(req.depth)  # honors the symbols→binary alias (Codex)

    changed = [p for p in req.changed_paths if p]
    seeded = req.seeded or bool(changed)
    risk_rules = (
        _load_risk_rules(req.risk_rules_path)
        if req.risk_rules_path is not None
        else RiskRules.default()
    )
    risk = score_changed_paths(changed, risk_rules)

    scan_mode = ScanMode(req.mode)
    # The pinned-depth contract (ADR-037 D5 auto-strict) applies to the programmatic
    # API too: an explicit depth *always* pins (even with source_method=auto, which
    # only picks the method), or a non-auto source_method does. So run_scan_core
    # fails loud if it can't collect the evidence — same as the CLI. AUTO / preset-
    # only requests stay best-effort (CodeRabbit review).
    pinned_explicit = (dp is not None) or (
        sm is not None and sm is not SourceMethod.AUTO
    )
    sm_pin = sm is not None and sm is not SourceMethod.AUTO
    # Mirrors cli_scan._scan_explicit_flags'/_run_scan_one_member's
    # level_explicit: consent to auto-running a trusted --config's
    # build.query (a non-auto source_method, or an explicit depth with no
    # source_method pinned). Without this the singular Python API path
    # left level_explicit at run_scan_core's False default, so an explicit
    # build_config + depth="build"/"source" could return
    # EVIDENCE_CONTRACT_ERROR instead of auto-running the query the CLI
    # and run_scan_set both already consent to (Codex review).
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
    # --depth binary is symbols-only (L0/L1): suppress the L2 header AST (and its
    # provenance) even when the caller passes headers, so the collected evidence
    # matches the reported depth — parity with the CLI's `scan --depth binary`.
    # Keyed on the *resolved* effective depth, not the raw depth: --source-method
    # wins over --depth, so a source-method scan that also passes `depth="binary"`
    # still needs the header AST (Codex review).
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
            abi3_floor=req.abi3_floor,
        )
    except _BudgetOverflow:
        # The failure-guard contract: overflow is exit 5, never a shrunk scope.
        return ScanResult(verdict="BUDGET_OVERFLOW", exit_code=5)
    except _EvidenceContractError:
        # A pinned depth that can't collect its evidence (auto-strict, ADR-037 D5):
        # the programmatic API honors the same contract as the CLI (pinned_explicit
        # above), so map the signal to a failed result rather than degrade silently.
        return ScanResult(verdict="EVIDENCE_CONTRACT_ERROR", exit_code=1)
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

    # _kill_process_tree's _descendant_pgids() walk is a point-in-time
    # snapshot taken before proc.terminate() fires; a clang/castxml child
    # this worker spawns via deadline.run_bounded() in the gap between that
    # snapshot and the terminate() call is invisible to it. Without this,
    # proc.terminate() (default SIGTERM disposition, since this is a fresh
    # `spawn`-context process with no inherited handler) would kill the
    # worker immediately, leaving that just-spawned detached process group
    # permanently orphaned — the worker's own _active_pgroups registry never
    # gets a handler chance to sweep it. install_sigterm_cleanup() gives this
    # worker the same in-process cleanup handler the plain CLI path and the
    # L4 ProcessPoolExecutor workers already install, so its own SIGTERM
    # handler kills every group *it* has registered before the process
    # actually exits — independent of what the outer descendant-pgid
    # snapshot did or didn't see (Codex review, PR #591, round 9).
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
    """Every process-group id in *root_pid*'s live process tree (POSIX, best-effort).

    A clang/castxml child spawned via ``deadline.run_bounded`` detaches into its
    *own* new session/group (needed so its own inner-timeout ``killpg`` can't
    self-kill the caller) — a plain ``killpg(root_pgid)`` from an ancestor no
    longer reaches it even though the PPID chain still leads back to *root_pid*,
    until *root_pid* itself dies and severs that link. Walking a live
    ``ps -eo pid,ppid`` snapshot while *root_pid* is still alive finds every such
    descendant's own pgid too, so it can be killed alongside the worker's own
    group instead of surviving as an orphan (Codex review, PR #591). Returns an
    empty set on any failure (missing ``ps``, non-POSIX, ...) — never raises.
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
    """Terminate *proc* and every group in its process tree (best-effort, never raises).

    Killing only ``proc``'s own group used to miss a clang/castxml child that
    had detached into its own session via ``deadline.run_bounded``'s
    ``start_new_session=True`` — that isolation is needed so *its* inner-timeout
    ``killpg`` can't self-kill the worker, but it also made the child invisible
    to this outer, worker-level ``killpg``. :func:`_descendant_pgids` finds it
    (and any other detached descendant) by PPID while ``proc`` is still alive, so
    it gets killed here too instead of surviving as an orphan (Codex review,
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
    # Only kill *proc*'s own group when it actually detached into its own (``os.
    # setsid`` ran). If it timed out before that, its pgid still equals the
    # parent's group — killpg would then terminate the MCP server itself, so
    # that group is excluded and only its (already-detached) descendants, if
    # any, are targeted (Codex review).
    pgids.discard(own_pgid)
    # Unconditional: proc itself never detached (own_pgid was excluded above
    # precisely because it's still in the parent's group), so it is never
    # reached by the killpg sweep below over *descendant* groups. Skipping
    # this when pgids is non-empty left the direct worker process running
    # forever whenever it had spawned a detached clang/castxml child but had
    # not itself detached (CodeRabbit review, PR #591).
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
) -> ScanResult:
    """One member's scan, for :func:`run_scan_set` (ADR-056).

    Mirrors :func:`run_scan`'s body (kept as a separate function rather than
    a refactor of `run_scan` itself, so `run_scan`'s existing behavior/tests
    stay byte-for-byte unchanged) with two differences:

    - Accepts an externally-supplied ``start``/``budget_s`` instead of
      calling ``_time.monotonic()`` itself. `run_scan_set` passes the *same*
      ``start`` and the *original, unreduced* total ``budget_s`` to every
      member — `run_scan_core`'s own ``_remaining_budget_s(start, budget_s)``
      already computes ``budget_s - (now - start)`` internally, so this
      alone produces the correct shrinking remaining budget across members.
      Passing an already-reduced ``budget_s`` here instead would
      double-subtract elapsed time.
    - Forwards `req.abi3_floor`/`enabled_checks`/`severities`/
      `build_config`/`allow_build_query`/`risk_rules_path`, which `run_scan`
      itself still ignores (unchanged behavior) but which the single-binary
      CLI path (`cli_scan.py`) already passes directly to `run_scan_core` —
      an `--artifact-set` scan must not silently drop them relative to an
      equivalent single-binary `scan` invocation with the same flags.
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
    from .cli_scan_baseline import _load_risk_rules, _public_provenance_set
    from .scan_engine import (
        _BudgetOverflow,
        _EvidenceContractError,
        run_scan_core,
    )

    sm = SourceMethod(req.source_method) if req.source_method else None
    dp = parse_user_depth(req.depth)

    changed = [p for p in req.changed_paths if p]
    seeded = req.seeded or bool(changed)
    risk_rules = (
        _load_risk_rules(req.risk_rules_path)
        if req.risk_rules_path is not None
        else RiskRules.default()
    )
    risk = score_changed_paths(changed, risk_rules)

    scan_mode = ScanMode(req.mode)
    sm_pin = sm is not None and sm is not SourceMethod.AUTO
    pinned_explicit = (dp is not None) or sm_pin
    # Mirrors cli_scan._scan_explicit_flags' level_explicit: consent to
    # auto-running build.query (a non-auto source_method, or an explicit
    # depth with no source_method pinned) -- without this, a trusted
    # --config's build.query never auto-runs for a member even when the
    # caller explicitly requested a deep depth (Codex review).
    level_explicit = sm_pin or (sm is None and dp is not None)
    is_auto = sm is SourceMethod.AUTO
    auto_method = risk.recommended_method if (is_auto and seeded) else None
    resolved, eff_depth = resolve_level(
        mode=scan_mode, source_method=sm, depth=dp, auto_method=auto_method
    )
    collect_mode = level_to_collect_mode(
        resolved,
        eff_depth,
        source_scope=SourceScope.CHANGED if seeded else SourceScope.TARGET,
    )
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
        )
    except _BudgetOverflow:
        return ScanResult(verdict="BUDGET_OVERFLOW", exit_code=5)
    except _EvidenceContractError:
        return ScanResult(verdict="EVIDENCE_CONTRACT_ERROR", exit_code=1)
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
    (ADR-056, ``scan --artifact-set``).

    The plural sibling of :func:`run_scan`, sharing `ScanRequest` but never
    touching `run_scan`'s own code path — `req.binaries` must have 2+
    entries (use `run_scan` for exactly one). `req.baseline` must be
    ``None``: this is a service-layer, not just a CLI-layer, guard — a
    directly-constructed `ScanRequest(binaries=[...], baseline=old)` handed
    straight to this public, re-exported entry point would otherwise
    silently compare every member against the same single baseline
    (ADR-056 D2 scopes `--artifact-set` to audit-only).
    """
    import time as _time

    from .bundle import ArtifactSetError, audit_bundle, discover_artifact_set

    # Canonicalize/deduplicate before the cardinality check: two literal
    # duplicates or symlink aliases of one DSO must not silently pass as a
    # valid 2-member set (discover_artifact_set below dedupes via
    # Path.resolve() too, which would otherwise report a "complete" audit
    # of what's really a single library) (Codex review).
    seen_resolved: set[Path] = set()
    binaries: list[Path] = []
    for binary in req.binaries:
        try:
            resolved = binary.resolve()
        except OSError:
            resolved = binary
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        binaries.append(binary)

    if len(binaries) < 2:
        raise ValueError(
            "run_scan_set requires 2 or more distinct binaries "
            "(duplicate/symlink-aliased paths do not count separately)"
        )
    if req.baseline is not None:
        raise ValueError("run_scan_set does not accept req.baseline (audit-only)")

    start = _time.monotonic()
    budget_s = req.budget.total_timeout

    per_artifact: list[ScanArtifactResult] = []
    for binary in binaries:
        result = _run_scan_one_member(
            req, binary, start=start, budget_s=budget_s, changed_src=req.changed_src
        )
        per_artifact.append(ScanArtifactResult(artifact=binary, result=result))
        if result.verdict == "BUDGET_OVERFLOW":
            # The failure-guard contract: never keep scanning past overflow.
            return ScanSetResult(verdict="BUDGET_OVERFLOW", exit_code=5,
                                  per_artifact=per_artifact)

    try:
        libraries = discover_artifact_set(list(binaries), explicit=True)
    except ArtifactSetError:
        # Collision/unsupported-member checks already ran in the CLI/MCP
        # front door before req.binaries was populated (ADR-056); if one
        # somehow reaches here, mark the bundle section incomplete rather
        # than raising out of an already-executed multi-member scan.
        verdict, exit_code = _aggregate_scan_set_verdict(per_artifact, None)
        return ScanSetResult(
            verdict=verdict,
            exit_code=exit_code,
            per_artifact=per_artifact,
            bundle_incomplete=True,
        )

    remaining = (
        None if budget_s is None else budget_s - (_time.monotonic() - start)
    )
    if remaining is not None and remaining <= 0:
        return ScanSetResult(
            verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=per_artifact
        )

    from . import deadline as _deadline

    try:
        # P2 regression (Codex review): the post-audit elapsed-time check
        # below only detects an overflow *after* audit_bundle() already ran
        # to completion -- it doesn't bound the call itself, so a
        # pathologically large or malformed ELF set could still burn far
        # more wall-clock than --budget allows before that check is ever
        # reached. build_bundle_snapshot()'s per-library parsing loop now
        # calls deadline.check() between members (the same cooperative,
        # per-unit-of-work checkpoint pattern scan_engine already uses for
        # per-header/per-TU work), so running audit_bundle() under this
        # scope lets a real overflow raise mid-parse instead of only being
        # caught afterward.
        with _deadline.deadline_scope(remaining):
            audit = audit_bundle(
                libraries, bundle_system_providers=req.bundle_system_providers
            )
    except ArtifactSetError:
        # Ambiguous duplicate-SONAME rejection (Codex review) surfaces here
        # the same way a discovery failure does above -- degrade to an
        # incomplete bundle section rather than raising out of an
        # already-executed multi-member scan.
        verdict, exit_code = _aggregate_scan_set_verdict(per_artifact, None)
        return ScanSetResult(
            verdict=verdict,
            exit_code=exit_code,
            per_artifact=per_artifact,
            bundle_incomplete=True,
        )
    except _deadline.DeadlineExceeded:
        return ScanSetResult(
            verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=per_artifact
        )
    # The deadline_scope above only bounds the checkpoints
    # build_bundle_snapshot() itself calls deadline.check() between (i.e.
    # per library, not mid-parse of one) -- re-check elapsed time after the
    # whole call so a real overflow within a single library's parse (or in
    # _compute_resolution_graph/_detect_unresolved_intra_dependency, which
    # have no checkpoints of their own) is still reported as
    # BUDGET_OVERFLOW (the failure-guard contract) rather than a normal
    # verdict that quietly ran over time (Codex review).
    if budget_s is not None and (_time.monotonic() - start) > budget_s:
        return ScanSetResult(
            verdict="BUDGET_OVERFLOW", exit_code=5, per_artifact=per_artifact
        )
    # discover_artifact_set already validated every member looks like ELF,
    # but build_bundle_snapshot() can still silently drop one whose actual
    # parse failed or came back empty (bundle.py's own per-member try/except
    # + empty-metadata skip). A reduced resolution graph missing a real
    # provider can then invent an unresolved-import finding for a symbol
    # that dropped member would have supplied -- mark the audit incomplete
    # (no findings published) rather than risk a false positive (Codex
    # review), the same degrade-not-raise contract the discovery-failure
    # path above already uses.
    if set(audit.snapshot.libraries) != set(libraries):
        verdict, exit_code = _aggregate_scan_set_verdict(per_artifact, None)
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
    SIGTERM cleanup verbatim — same race, same fix: without these, a
    clang/castxml child one of `run_scan_set`'s member scans spawns can
    detach into its own process group in the gap between the parent's
    descendant-pgid snapshot and its terminate() call, and outlive the
    worker as an orphan (Codex review).
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
    if status == "err":
        raise RuntimeError(payload)
    return payload  # type: ignore[no-any-return]
