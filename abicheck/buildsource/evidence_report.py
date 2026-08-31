# Copyright 2026 Nikolay Petrov
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

"""Compare-side build/source evidence: side resolution, diffing, reporting.

ADR-061 Phase 3. This is the engine owner of the ``compare`` build-source
integration that used to live in ``cli_buildsource_helpers`` -- resolving each
side's pack, diffing the L3/L4/L5 payloads, deriving the ADR-028 D7 coverage
and capability report, and finalizing the ADR-033 D9 metrics. It was the last
thing keeping ``service_compare_pipeline`` importing a ``cli_*`` module.

**The module owns no output stream.** Every function that used to call
``click.echo`` now *renders* its report as ``list[str]`` and hands the lines to
an optional ``on_output`` sink. This replaced a ``quiet: bool`` flag, for the
same reason the ``embed_build_source`` move replaced one: a ``quiet`` flag is
only meaningful to a caller that has a stream in the first place, and
``service.run_compare_request`` never did -- it had to pass ``quiet=True``
forever to suppress writes to a stream it does not own. A sink makes the
default (no sink, no output) the correct one for an engine caller, and lets the
CLI adapter decide that its sink writes to *stderr* so ``--format json``
stdout stays parseable.

**The error contract is preserved, not tidied.** ``resolve_side_pack`` raises
``SnapshotError`` for an unloadable pack -- *operational*, not a usage error:
the command line was well-formed and the pack's bytes were not, so the CLI
adapter renders it as ``click.ClickException`` (**exit 1**) rather than
``click.UsageError`` (exit 64). Collapsing the two would tell a CI consumer
the invocation was wrong when the data was. Pinned by
``tests/test_evidence_report_contract.py``, written before this move.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import TYPE_CHECKING

from .evidence_policy import (
    apply_evidence_policy,
    evidence_coverage_metrics,
    evidence_metrics_lines,
    finding_bucket_counts,
    require_evidence_findings,
    tag_evidence_category,
)
from .merge_support import _combine_packs
from .model import CoverageStatus, DataLayer, LayerConfidence, LayerCoverage
from .pack import BuildSourcePack

if TYPE_CHECKING:
    from ..checker_types import Change, DiffResult
    from ..model import AbiSnapshot
    from ..policy_file import PolicyFile

#: Sink for this module's human-readable report lines. ``None`` means "produce
#: nothing" -- the correct default for an engine caller that owns no stream.
EvidenceEmit = Callable[[str], None]

__all__ = [
    "CHECK_CAPABILITIES",
    "LAYER_NAMES",
    "EvidenceEmit",
    "attach_evidence_metrics",
    "capability_lines",
    "compare_side_coverage_lines",
    "coverage_lines",
    "detect_coverage_asymmetry",
    "diff_embedded_build_source",
    "intrinsic_coverage",
    "layer_presence",
    "optional_coverage",
    "prepare_embedded_build_source",
    "resolve_side_pack",
]


def _emit(on_output: EvidenceEmit | None, lines: Iterable[str]) -> None:
    """Hand ``lines`` to the caller's sink, or drop them when it owns none.

    The engine never writes to a stream of its own: a typed-API caller passes
    no sink and stays silent, while the CLI passes one that routes to stderr.
    """
    if on_output is None:
        return
    for line in lines:
        on_output(line)


def _load_side_pack_input(
    path: Path | None,
    *,
    exported_symbols: Iterable[str] = (),
    on_warning: EvidenceEmit | None = None,
) -> BuildSourcePack | None:
    """Load a compare-side out-of-band pack, auto-detecting its pack kind.

    Raises ``SnapshotError`` for an unloadable pack (see the module docstring
    on why that is deliberately not a usage error).

    A Flow-2 ``abicheck_inputs/`` pack validates on ingest, and its *non-fatal*
    findings -- an incomplete fact family, an empty source surface -- leave
    through ``on_warning``. Passing none discards them, which is right for a
    typed caller that owns no stream and wrong for the CLI: dropping them lets
    a successful comparison conceal degraded evidence (Codex review, P2, on
    exactly this call site after the loader moved to the engine).
    """
    if path is None:
        return None
    from .inputs_pack import is_inputs_pack_dir
    from .pack_load import load_inputs_pack_or_raise, load_pack_or_raise

    if is_inputs_pack_dir(path):
        return load_inputs_pack_or_raise(
            path, exported_symbols=exported_symbols, on_warning=on_warning
        )
    return load_pack_or_raise(path)


def resolve_side_pack(
    build_info: Path | None,
    sources: Path | None,
    snap: AbiSnapshot | None,
    *,
    on_warning: EvidenceEmit | None = None,
) -> BuildSourcePack | None:
    """Resolve one compare side's pack from flags first, then embedded facts.

    Explicit ``--build-info old=`` / ``--sources new=`` pack directories
    override the snapshot's embedded payload per layer; when neither flag is
    given the embedded ``snap.build_source`` is used as-is (single-artifact UX).
    """
    # AC-003 (compare side): seed the ingest of a Flow-2 `abicheck_inputs/` pack
    # given via `--build-info old=`/`--sources new=` with this side's L0
    # exports, so its source surface relinks onto the DSO (matched_symbols>0)
    # instead of reporting 0 -- the same fix the dump/embed path already applies
    # (Codex/CodeRabbit review).
    from .snapshot_exports import exported_symbols_from_snapshot

    exported = exported_symbols_from_snapshot(snap) if snap is not None else ()
    bi_pack = _load_side_pack_input(
        build_info, exported_symbols=exported, on_warning=on_warning
    )
    src_pack = _load_side_pack_input(
        sources, exported_symbols=exported, on_warning=on_warning
    )
    embedded = snap.build_source if snap is not None else None
    if bi_pack is None and src_pack is None:
        return embedded

    # Each flag's pack exposes *every* layer it carries (a pack directory may
    # hold build + source + graph together). --build-info wins for
    # L3, --sources wins for L4/L5, the embedded payload backfills, and the
    # coverage manifest is rebuilt per-layer from the supplying pack.
    # `prefer_nonempty=False`: an explicit `--build-info`/`--sources` pack
    # overrides the snapshot's embedded payload even when its layer is
    # intentionally empty (a failed/absent replay) -- the documented "explicit
    # flags override embedded" contract, which the dump-path non-empty
    # preference would otherwise break by falling through to stale embedded
    # facts (Codex review).
    return _combine_packs(bi_pack, src_pack, embedded, prefer_nonempty=False)


def intrinsic_coverage(snap: AbiSnapshot) -> list[LayerCoverage]:
    """Derive L0/L1/L2 coverage rows from a snapshot (ADR-028 D7)."""

    def row(layer: str, present: bool, detail: str) -> LayerCoverage:
        """One coverage row: presence alone fixes both status and confidence."""
        return LayerCoverage(
            layer=layer,
            status=CoverageStatus.PRESENT if present else CoverageStatus.NOT_COLLECTED,
            confidence=LayerConfidence.HIGH if present else LayerConfidence.UNKNOWN,
            detail=detail,
        )

    has_debug = bool(snap.dwarf or snap.dwarf_advanced)
    has_headers = bool(snap.from_headers and not snap.from_headers_inferred)
    return [
        row("L0", bool(snap.elf or snap.pe or snap.macho), snap.platform or ""),
        row("L1", has_debug, "DWARF" if has_debug else ""),
        row("L2", has_headers, "header-scoped" if has_headers else ""),
    ]


def optional_coverage(pack: BuildSourcePack | None) -> list[LayerCoverage]:
    """L3/L4/L5 coverage rows from a pack manifest, or all-absent rows."""
    if pack is not None:
        return list(pack.manifest.coverage)
    return [
        LayerCoverage(layer=layer.value, status=CoverageStatus.NOT_COLLECTED)
        for layer in (
            DataLayer.L3_BUILD,
            DataLayer.L4_SOURCE_ABI,
            DataLayer.L5_SOURCE_GRAPH,
        )
    ]


# Human-readable layer names, ordered shallow->deep, shared by the coverage
# table and the asymmetry finding so both speak the same vocabulary.
LAYER_NAMES: dict[str, str] = {
    "L0": "L0 binary metadata",
    "L1": "L1 debug info",
    "L2": "L2 public header AST",
    "L3_build": "L3 build context",
    "L4_source_abi": "L4 source ABI replay",
    "L5_source_graph": "L5 source graph summary",
}


def coverage_lines(
    intrinsic: list[LayerCoverage], optional: list[LayerCoverage]
) -> list[str]:
    """Render the D7 evidence-coverage table (one string per line)."""
    lines = ["Evidence coverage:"]
    for cov in [*intrinsic, *optional]:
        extra = ""
        if cov.status != CoverageStatus.NOT_COLLECTED:
            extra = f", {cov.confidence.value} confidence"
            if cov.detail:
                extra += f": {cov.detail}"
        lines.append(
            f"  {LAYER_NAMES.get(cov.layer, cov.layer):<26} {cov.status.value}{extra}"
        )
    return lines


def compare_side_coverage_lines(
    old_intrinsic: list[LayerCoverage],
    old_optional: list[LayerCoverage],
    new_intrinsic: list[LayerCoverage],
    new_optional: list[LayerCoverage],
) -> list[str]:
    """Render old/new layer coverage so mixed-evidence compares are explicit."""
    old_by_layer = {c.layer: c for c in (*old_intrinsic, *old_optional)}
    new_by_layer = {c.layer: c for c in (*new_intrinsic, *new_optional)}
    lines = ["Evidence coverage by side:"]
    for layer, name in LAYER_NAMES.items():
        old = old_by_layer.get(layer)
        new = new_by_layer.get(layer)
        old_status = old.status.value if old is not None else "not_collected"
        new_status = new.status.value if new is not None else "not_collected"
        marker = " (asymmetric)" if old_status != new_status else ""
        lines.append(f"  {name:<26} old={old_status:<13} new={new_status}{marker}")
    return lines


def layer_presence(snap: AbiSnapshot, pack: BuildSourcePack | None) -> dict[str, bool]:
    """Map every evidence layer id -> present? for one side of the compare.

    L0/L1/L2 are intrinsic to the snapshot; L3/L4/L5 come from the pack manifest
    coverage (with the loaded ``build_evidence`` object treated as authoritative
    proof that L3 is present even if the manifest row is stale).
    """
    present = {
        row.layer: row.status != CoverageStatus.NOT_COLLECTED
        for row in intrinsic_coverage(snap)
    }
    by_layer = {c.layer: c.present for c in (pack.manifest.coverage if pack else [])}
    for layer in (
        DataLayer.L3_BUILD,
        DataLayer.L4_SOURCE_ABI,
        DataLayer.L5_SOURCE_GRAPH,
    ):
        present[layer.value] = by_layer.get(layer.value, False)
    if pack is not None and pack.build_evidence is not None:
        present[DataLayer.L3_BUILD.value] = True
    return present


def detect_coverage_asymmetry(
    old_snap: AbiSnapshot,
    old_pack: BuildSourcePack | None,
    new_snap: AbiSnapshot,
    new_pack: BuildSourcePack | None,
) -> list[Change]:
    """Flag layers the base was analyzed with but the target lacks (ADR-028 D7).

    A full base scan (binary + debug + headers + build + sources) compared
    against a binary+headers-only target is a legitimate, supported comparison --
    but it is *degraded*: the layers the target is missing cannot prove or
    disprove changes, so the verdict is scoped to what both sides share. Rather
    than let that happen silently, emit one ``EVIDENCE_COVERAGE_ASYMMETRIC``
    RISK finding naming exactly which pieces the target is missing.

    Only the base->target degradation direction is reported (target missing what
    the base had). A target that is *richer* than the base does not undermine
    the comparison, so it is not flagged here.
    """
    from ..checker_policy import ChangeKind
    from ..checker_types import Change

    old_present = layer_presence(old_snap, old_pack)
    new_present = layer_presence(new_snap, new_pack)
    missing = [
        layer
        for layer in LAYER_NAMES
        if old_present.get(layer) and not new_present.get(layer)
    ]
    if not missing:
        return []

    human = ", ".join(LAYER_NAMES[m] for m in missing)
    return [
        Change(
            kind=ChangeKind.EVIDENCE_COVERAGE_ASYMMETRIC,
            symbol="evidence:coverage",
            description=(
                f"Base was analyzed with evidence the target lacks ({human}). "
                "The comparison is scoped to the layers both sides share, so "
                "changes only those missing layers could prove are NOT reported "
                "and this verdict must not be read as a full-coverage result. "
                "Re-scan the target with the same inputs (e.g. -g for debug "
                "info, collect for build/source context) to restore "
                "full coverage."
            ),
            old_value=human,
            new_value="not collected on target",
        )
    ]


#: One row per check category: (label, evidence layer that enables it, the
#: question it answers, and why it is off when that layer is absent). This is the
#: "what is and is not being checked, and why" report (ADR-028 D7): the tiers run
#: from a bare binary up through debug symbols, headers, build data, and sources.
CHECK_CAPABILITIES: tuple[tuple[str, str, str, str], ...] = (
    (
        "Symbol presence & linkage (added/removed/SONAME)",
        "L0",
        "from the binary's dynamic symbol table",
        "needs the built binary",
    ),
    (
        "Type layout, members, vtables, signatures",
        "L1",
        "from DWARF/PDB debug info",
        "no debug info: checks limited to symbol-level, not struct/member/layout",
    ),
    (
        "API decls absent from the symbol table; public-surface scoping",
        "L2",
        "from the public header AST",
        "no headers: header-only/inline-API declarations are invisible",
    ),
    (
        "Build-flag & toolchain drift (visibility, std, ABI flags)",
        "L3_build",
        "from build-system data (compile DB / CMake / Ninja / Bazel)",
        "no build data: flag/toolchain regressions are not detected",
    ),
    (
        "Macros, default args, inline/template/constexpr bodies",
        "L4_source_abi",
        "from source ABI replay (requires a source extractor: clang, castxml, or android)",
        "no source replay evidence: source-only API changes are not detected",
    ),
    (
        "Impact / call / reachability graph",
        "L5_source_graph",
        "from the source graph summary",
        "no graph evidence: cross-symbol impact is not analyzed",
    ),
)


def capability_lines(
    intrinsic: list[LayerCoverage], optional: list[LayerCoverage]
) -> list[str]:
    """Render exactly which check categories are enabled -- and why others are not.

    Driven by the evidence coverage (ADR-028 D7): each check category is gated on
    one evidence layer, so the user sees, for the inputs they actually provided
    (binary only -> +debug -> +headers -> +build data -> +sources), which checks
    ran and the concrete reason each disabled one is off.
    """
    # Only a PRESENT layer enables its checks: a PARTIAL layer (e.g. L4 when clang
    # was missing or every TU failed, so no entities were extracted) ran but
    # produced nothing, and must read as [off], not [on] (CodeRabbit review).
    present = {
        c.layer for c in (*intrinsic, *optional) if c.status == CoverageStatus.PRESENT
    }
    lines = ["Checks enabled for this scan (and why others are not):"]
    for label, layer, how, why_off in CHECK_CAPABILITIES:
        if layer in present:
            lines.append(f"  [on]  {label} — {how}")
        else:
            lines.append(f"  [off] {label} — {why_off}")
    return lines


def diff_embedded_build_source(
    old_build_info: Path | None,
    new_build_info: Path | None,
    old_sources: Path | None,
    new_sources: Path | None,
    collect_mode: str,
    new_snapshot: AbiSnapshot,
    old_snapshot: AbiSnapshot | None = None,
    policy_file: PolicyFile | None = None,
    *,
    on_output: EvidenceEmit | None = None,
) -> tuple[list[Change], list[dict[str, object]], dict[str, object]]:
    """Diff each side's build-info + source facts, report coverage, return findings.

    Each side's facts come from the snapshot's *embedded* ``build_source``
    payload (single-artifact UX) unless an out-of-band ``--build-info old=`` /
    ``--sources new=`` pack directory overrides it. Per ADR-028 D3 the findings
    are folded into the ordinary verdict pipeline as ``extra_changes`` and never
    override artifact-backed verdicts. The D7 coverage table is handed to
    ``on_output`` (the CLI adapter writes it to stderr, so it covers every
    output format without polluting a ``--format json`` stdout) and also
    returned as serialized rows so the JSON report can carry a structured
    ``layer_coverage`` block. With no ``on_output`` sink nothing is rendered;
    the returned/embedded data is unaffected either way.

    When ``old_snapshot`` is supplied, the base and target coverage are compared
    layer-by-layer: if the base was analyzed with evidence the target lacks
    (e.g. a full base scan vs a binary+headers-only target), a single
    ``EVIDENCE_COVERAGE_ASYMMETRIC`` finding spells out exactly which pieces the
    target is missing so the degraded comparison is never silent.

    The third tuple element is a partial ADR-033 D9 metrics dict (coverage flags
    plus the build-context-drift / source-only finding split this function can
    count first-hand); :func:`attach_evidence_metrics` fills in timing and
    run-wide totals. Returns ``(changes, coverage_rows, metrics)``.
    """
    from .build_diff import check_header_parse_drift, diff_build_evidence

    old_pack = resolve_side_pack(
        old_build_info, old_sources, old_snapshot, on_warning=on_output
    )
    new_pack = resolve_side_pack(
        new_build_info, new_sources, new_snapshot, on_warning=on_output
    )

    if old_pack is None and new_pack is None:
        if collect_mode != "off":
            _emit(
                on_output,
                [
                    f"Note: --depth collected evidence mode '{collect_mode}' was "
                    "requested but no build-info/source facts were embedded or "
                    "supplied; inline collection for this mode is not yet "
                    "available. Embed with `dump --build-info`/`--sources` (or "
                    "pass --build-info old=/new= pack dirs to compare)."
                ],
            )
        # require_evidence still fires with no packs at all: every required layer
        # is missing, so the run must fail rather than pass on zero evidence. Emit
        # a coverage-only metrics dict so attach_evidence_metrics still counts the
        # evidence_required_missing finding (Codex review) instead of dropping it.
        req = require_evidence_findings(policy_file, None, None)
        metrics = evidence_coverage_metrics([]) if req else {}
        return req, [], metrics

    changes: list[Change] = []
    # Tag each finding with its D9 bucket as it is produced: each diff helper
    # below owns one bucket, so we never re-classify by ChangeKind (which would
    # drift as kinds move between modules). The metrics then count *retained*
    # (post-suppression) findings per bucket in attach_evidence_metrics, so the
    # D9 split partitions the reported findings (Codex review).
    old_build = old_pack.build_evidence if old_pack else None
    new_build = new_pack.build_evidence if new_pack else None
    if old_build is not None and new_build is not None:
        _build_changes = diff_build_evidence(old_build, new_build)
        tag_evidence_category(_build_changes, "build_context")
        apply_evidence_policy(_build_changes, "build_context", policy_file)
        changes.extend(_build_changes)
    # Header-parse-context drift only applies when the new snapshot actually
    # carries a public-header AST (L2). A binary-only compare has no header
    # parse context that could have drifted, so the finding would be misleading.
    new_has_headers = bool(
        new_snapshot.from_headers and not new_snapshot.from_headers_inferred
    )
    if new_build is not None and new_has_headers:
        _drift = check_header_parse_drift(
            new_build,
            headers_parsed_with_context=new_snapshot.parsed_with_build_context,
        )
        tag_evidence_category(_drift, "build_context")
        apply_evidence_policy(_drift, "build_context", policy_file)
        changes.extend(_drift)

    if old_snapshot is not None:
        _asym = detect_coverage_asymmetry(
            old_snapshot, old_pack, new_snapshot, new_pack
        )
        tag_evidence_category(_asym, "build_context")
        apply_evidence_policy(_asym, "build_context", policy_file)
        changes.extend(_asym)

    # L4 source ABI replay diff (ADR-030 D6): both packs must carry a source
    # surface. Per ADR-028 D3 these are ordinary API_BREAK/RISK findings folded
    # into the verdict pipeline — never sole authority for a BREAKING verdict.
    old_surface = old_pack.source_abi if old_pack else None
    new_surface = new_pack.source_abi if new_pack else None
    _src: list[Change] = []
    if old_surface is not None and new_surface is not None:
        from .source_diff import diff_source_abi

        _src = diff_source_abi(old_surface, new_surface)
        tag_evidence_category(_src, "source_only")
        apply_evidence_policy(_src, "source_only", policy_file)
        changes.extend(_src)

    # L5 source graph diff (ADR-031 D6): both packs must carry a graph summary.
    # Per ADR-028 D3 / ADR-031 D6 these are ordinary RISK findings folded into
    # the verdict pipeline — they explain and prioritize, never sole authority.
    old_graph = old_pack.source_graph if old_pack else None
    new_graph = new_pack.source_graph if new_pack else None
    if old_graph is not None and new_graph is not None:
        from .source_graph_findings import diff_source_graph_findings

        # ``_src`` (the L4 surface diff, if both sides had one) lets the graph
        # diff correlate a public entry's own body/type_hash change with it
        # newly reaching an internal dependency (ADR-041 P0 roadmap item 2).
        _gr = diff_source_graph_findings(old_graph, new_graph, source_diff_changes=_src)
        tag_evidence_category(_gr, "source_only")
        apply_evidence_policy(_gr, "graph_risk", policy_file)
        changes.extend(_gr)

    # ADR-033 D7 require_evidence: fail if a declared-mandatory layer is not
    # comparable on both sides. These are API_BREAK findings (not modulated by
    # the knobs).
    changes.extend(require_evidence_findings(policy_file, old_pack, new_pack))

    # Coverage/capability reflect the *target* (new) side only: the L3/L4/L5
    # diffs run only when both sides supply a layer, so reporting the old pack's
    # coverage when the new side has none would over-claim that source/build
    # checks ran for this scan (Codex review). The side-by-side table below
    # still exposes old/new asymmetry to humans.
    coverage = optional_coverage(new_pack)
    intrinsic = intrinsic_coverage(new_snapshot)
    if on_output is not None:
        _emit(on_output, coverage_lines(intrinsic, coverage))
        if old_snapshot is not None:
            _emit(
                on_output,
                compare_side_coverage_lines(
                    intrinsic_coverage(old_snapshot),
                    optional_coverage(old_pack),
                    intrinsic,
                    coverage,
                ),
            )
        _emit(on_output, capability_lines(intrinsic, coverage))
    coverage_rows: list[dict[str, object]] = [
        c.to_dict() for c in (*intrinsic, *coverage)
    ]
    metrics = evidence_coverage_metrics(coverage)
    return changes, coverage_rows, metrics


def prepare_embedded_build_source(
    old_snapshot: AbiSnapshot,
    new_snapshot: AbiSnapshot,
    collect_mode: str,
    extra_changes: list[Change] | None,
    old_build_info: Path | None,
    new_build_info: Path | None,
    old_sources: Path | None,
    new_sources: Path | None,
    policy_file: PolicyFile | None = None,
    *,
    on_output: EvidenceEmit | None = None,
) -> tuple[
    list[Change] | None, list[dict[str, object]], dict[str, object], list[Change]
]:
    """Run inline build-info/source diffing for ``compare`` and time it.

    Gates on whether any pack flag, embedded payload, or non-``off`` collect mode
    is in play; folds the evidence findings into ``extra_changes``; and wall-clocks
    the inline diffing for the ADR-033 D6/D9 ``extractor.duration_seconds`` metric.
    ``policy_file`` carries the ADR-033 D7 evidence-policy knobs that modulate the
    findings' verdict category. ``on_output`` forwards to
    :func:`diff_embedded_build_source`. Returns
    ``(extra_changes, layer_coverage_rows, evidence_metrics, ev_changes)``; the
    metrics still need :func:`attach_evidence_metrics` for run-wide totals.
    """
    import time

    any_pack_flag = any(
        x is not None
        for x in (old_build_info, new_build_info, old_sources, new_sources)
    )
    has_embedded = (
        old_snapshot.build_source is not None or new_snapshot.build_source is not None
    )
    # require_evidence must be able to fail a run that supplied no evidence at
    # all, so engage the pipeline when the policy declares any requirement.
    requires_evidence = bool(policy_file is not None and policy_file.require_evidence)
    if not (
        any_pack_flag or collect_mode != "off" or has_embedded or requires_evidence
    ):
        return extra_changes, [], {}, []

    start = time.perf_counter()
    ev_changes, coverage_rows, metrics = diff_embedded_build_source(
        old_build_info,
        new_build_info,
        old_sources,
        new_sources,
        collect_mode,
        new_snapshot,
        old_snapshot,
        policy_file,
        on_output=on_output,
    )
    if metrics:
        metrics["extractor.duration_seconds"] = round(time.perf_counter() - start, 4)
    if ev_changes:
        extra_changes = (extra_changes or []) + ev_changes
    return extra_changes, coverage_rows, metrics, ev_changes


def attach_evidence_metrics(
    result: DiffResult,
    metrics: dict[str, object],
    injected_changes: list[Change],
    *,
    on_output: EvidenceEmit | None = None,
) -> None:
    """Finalize and attach the ADR-033 D9 evidence metrics onto ``result``.

    Counts the finding buckets from the *retained* (post-suppression)
    ``result.changes`` so they partition the reported findings consistently
    (Codex review): build-context-drift and source-only come from each finding's
    ``evidence_category`` tag, and artifact-backed is everything not externally
    injected via ``extra_changes`` (build/source evidence *and* probe-matrix
    findings — none from L0–L2 diffing). Adds the suppression/surface-demotion
    totals, then renders the D6 timing summary to ``on_output``. No-op when no
    evidence was involved.
    """
    if not metrics:
        return
    counts = finding_bucket_counts(result.changes, injected_changes)
    for bucket, n in counts.items():
        metrics[f"findings.{bucket}.count"] = n
    metrics["findings.demoted_by_surface.count"] = result.out_of_surface_count
    metrics["findings.suppressed_with_reason.count"] = result.suppressed_count
    result.evidence_metrics = metrics
    _emit(on_output, evidence_metrics_lines(metrics))
