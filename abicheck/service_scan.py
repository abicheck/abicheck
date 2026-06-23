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
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .errors import ValidationError
from .header_utils import HEADER_SUFFIXES

if TYPE_CHECKING:
    from .buildsource.scan_levels import EvidenceDepth, SourceMethod

_logger = logging.getLogger(__name__)

# Header file extensions recognised during directory expansion. Shared with the
# AST-cache include walk (dumper._cache_key) via the leaf header_utils module so
# expansion and cache-invalidation can never drift (Codex review).
_HEADER_EXTS = HEADER_SUFFIXES


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
            found = [
                f
                for f in p.rglob("*")
                if f.is_file() and f.suffix.lower() in _HEADER_EXTS
            ]
            if not found:
                raise ValidationError(
                    f"Header directory contains no supported header files: {p}"
                )
            out.extend(sorted(found))
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
    )


@dataclass(frozen=True)
class Budget:
    """Optional scan budget — a failure guard, never a scope-shrinker (ADR-035 D3)."""

    total_timeout: float | None = None  # seconds; overflow FAILS (never shrinks)
    max_tus: int | None = None  # targeted-AST TU cap
    partial_ok: bool = True  # a partial scan (missing tool/layer) is success


@dataclass(frozen=True)
class CompileContext:
    """L2 header-AST compile context — shared by ``dump`` and ``scan``.

    The cross-toolchain + frontend knobs the header frontend needs to parse the
    public headers: the cross-compiler (``--gcc-path``/``--gcc-prefix``), extra
    compiler flags (``--gcc-options``/``--gcc-option``), an alternate
    ``--sysroot``, ``--nostdinc``, and which ``--ast-frontend`` to drive. ADR-037
    D3 (parity: ``dump`` and ``scan`` carry the *same* family via one decorator)
    and the ADR-035 amendment (``scan`` must be able to reach a real L2 — the
    cross-source checks depend on header provenance). All fields defaulted, so a
    bare ``CompileContext()`` is additive over every request and dump path.
    """

    gcc_path: str | None = None
    gcc_prefix: str | None = None
    gcc_options: str | None = None
    gcc_option_tokens: tuple[str, ...] = ()
    sysroot: Path | None = None
    nostdinc: bool = False
    frontend: str = "auto"  # --ast-frontend (auto/castxml/clang)

    @property
    def is_default(self) -> bool:
        """True when nothing was customised (lets call sites skip threading)."""
        return self == CompileContext()


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
_COST_PER_HEADER_PARSE = 0.08  # L2 castxml per public header
_COST_PER_TU_BUILD = 0.002  # L3 compile-DB entry parse
_COST_PER_TU_REPLAY = 0.45  # L4 per-TU semantic AST replay
_COST_PER_TU_GRAPH = 0.02  # L5 per-TU graph fold/edge


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
    """TU count of an ``abicheck collect`` pack dir, or ``None`` if not a pack.

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
    (
        RiskRules,
        score_changed_paths,
        EvidenceDepth,
        ScanMode,
        SourceMethod,
        level_to_collect_mode,
        resolve_level,
        parse_user_depth,
    ) = _scan_imports()

    mode = ScanMode(req.mode)
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
    collect_mode = level_to_collect_mode(resolved, eff_depth)

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
        total_tus = bazel_tus
        tu_note = "Bazel aquery/cquery (build_evidence)"
    elif pack_tus is not None:
        total_tus = pack_tus
        tu_note = "abicheck collect pack (build_evidence)"
    elif compile_db is not None:
        total_tus = _count_compile_db_tus(compile_db)
        tu_note = f"compile DB: {compile_db.name}"
    elif req.sources is not None:
        total_tus = _count_source_tus(req.sources)
        tu_note = "counted source files (no compile DB)"
    else:
        total_tus = 0
        tu_note = "no source tree / compile DB"

    # --depth binary is symbols-only: the real scan suppresses the L2 header AST, so
    # the estimate must not price an L2_header layer for headers that won't be parsed
    # — else a programmatic caller's `ScanResult.estimate` plans a different cost than
    # what executes (Codex review). Keyed on the resolved effective depth.
    eff_req_headers = [] if eff_depth is EvidenceDepth.BINARY else list(req.headers)
    n_headers = len(expand_header_inputs(eff_req_headers)) if eff_req_headers else 0
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

    estimates: list[CostEstimate] = [
        CostEstimate(
            None,
            "L0_binary",
            len(req.binaries),
            0.1 * max(1, len(req.binaries)),
            0.0,
            "binary export table parse",
        ),
        CostEstimate(None, "L1_debug", 0, 0.05, 0.0, "debug info (if present)"),
        CostEstimate(
            None,
            "L2_header",
            n_headers,
            _COST_PER_HEADER_PARSE * n_headers,
            0.0,
            "public-header AST (needs castxml or clang)"
            if n_headers
            else "no headers supplied",
        ),
    ]

    if collect_mode in ("build", "graph-build", "source-changed", "graph-full"):
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
    if collect_mode in ("source-changed", "graph-full"):
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
    if collect_mode in ("graph-build", "graph-full", "source-changed"):
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
    if collect_mode in ("source-changed", "graph-full"):
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
            "verdict": self.verdict,
            "exit_code": self.exit_code,
            "findings": len(self.findings),
            "layers": [layer.to_dict() for layer in self.layers],
            "confidence": {k: list(v) for k, v in self.confidence.items()},
            "estimate": [e.to_dict() for e in self.estimate],
            "report": dict(self.report),
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
    (``cli_scan.run_scan_core`` — classify → always-on tier → pinned level →
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
    ) = _scan_imports()
    from .buildsource.crosscheck import ALL_CHECKS
    from .cli_scan import (
        _BudgetOverflow,
        _EvidenceContractError,
        _public_provenance_set,
        run_scan_core,
    )

    if len(req.binaries) != 1:
        raise ValueError("run_scan accepts exactly one binary")
    binary = req.binaries[0]
    sm = SourceMethod(req.source_method) if req.source_method else None
    dp = parse_user_depth(req.depth)  # honors the symbols→binary alias (Codex)

    changed = [p for p in req.changed_paths if p]
    seeded = req.seeded or bool(changed)
    risk_rules = RiskRules.default()
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
    is_auto = sm is SourceMethod.AUTO
    auto_method = risk.recommended_method if (is_auto and seeded) else None
    resolved, eff_depth = resolve_level(
        mode=scan_mode, source_method=sm, depth=dp, auto_method=auto_method
    )
    collect_mode = level_to_collect_mode(resolved, eff_depth)
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
            build_config=None,
            baseline=Path(req.baseline) if req.baseline is not None else None,
            lang=req.lang,
            allow_build_query=False,
            scan_mode=scan_mode,
            resolved=resolved,
            eff_depth_enum=eff_depth,
            collect_mode=collect_mode,
            changed=changed,
            changed_src="run_scan",
            seeded=seeded,
            risk=risk,
            is_auto=is_auto,
            enabled_checks=frozenset(ALL_CHECKS),
            severities={},
            budget=budget_str,
            budget_s=budget_s,
            pinned_explicit=pinned_explicit,
            compile_context=None if req.compile.is_default else req.compile,
        )
    except _BudgetOverflow:
        # The failure-guard contract: overflow is exit 5, never a shrunk scope.
        return ScanResult(verdict="BUDGET_OVERFLOW", exit_code=5)
    except _EvidenceContractError:
        # A pinned depth that can't collect its evidence (auto-strict, ADR-037 D5):
        # the programmatic API honors the same contract as the CLI (pinned_explicit
        # above), so map the signal to a failed result rather than degrade silently.
        return ScanResult(verdict="EVIDENCE_CONTRACT_ERROR", exit_code=1)

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

    try:
        os.setsid()  # new process group; killpg(parent) reaches clang subprocs
    except (OSError, AttributeError):
        pass  # non-POSIX or already a leader — parent falls back to terminate()
    try:
        q.put(("ok", run_scan(req).to_dict()))
    except BaseException as exc:  # noqa: BLE001 — convey, don't crash the worker
        q.put(("err", f"{type(exc).__name__}: {exc}"))


def _kill_process_tree(proc: Any) -> None:
    """Terminate *proc* and its process group (best-effort, never raises)."""
    import os
    import signal

    if not proc.is_alive():
        return
    try:
        pgid = os.getpgid(proc.pid)
        # Only kill the *group* when the child actually detached into its own
        # (``os.setsid`` ran). If it timed out before that, its pgid still equals
        # the parent's group — killpg would then terminate the MCP server itself,
        # so fall back to killing just the worker process (Codex review).
        own_pgid = os.getpgrp()
        if pgid != own_pgid:
            os.killpg(pgid, signal.SIGTERM)
            proc.join(3)
            if proc.is_alive():
                os.killpg(pgid, signal.SIGKILL)
        else:
            proc.terminate()
    except (ProcessLookupError, PermissionError, AttributeError, OSError):
        try:
            proc.terminate()
        except (OSError, AttributeError):
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
