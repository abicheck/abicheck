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

"""Plain helper functions extracted from ``cli_buildsource``.

These cover the ``merge`` sub-command as well as the ``compare`` build-source
integration (embedded-evidence diffing, layer-coverage reporting, capability
reporting) and the source-graph load/localize helpers. They were extracted from
``cli_buildsource.py`` to keep that module under the 2000-line hard cap. They
must NOT import from ``abicheck.cli_buildsource`` or ``abicheck.cli`` (that would
create an import cycle rejected by the CI gate) — this is a leaf module.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import click

from .buildsource.evidence_policy import (
    apply_evidence_policy,
    echo_evidence_metrics,
    evidence_coverage_metrics,
    finding_bucket_counts,
    require_evidence_findings,
    tag_evidence_category,
)
from .buildsource.merge_support import _combine_packs
from .buildsource.model import (
    CoverageStatus,
    DataLayer,
    ExtractorRecord,
    LayerConfidence,
    LayerCoverage,
)
from .buildsource.pack import BuildSourcePack
from .buildsource.redaction import DEFAULT_REDACTION
from .cli_buildsource_merge import (
    _exported_symbols_from_snapshot as _exported_symbols_from_snapshot,
    _ingest_inputs_pack_snapshot as _ingest_inputs_pack_snapshot,
    _merge_attach_combined as _merge_attach_combined,
    _merge_fold_packs as _merge_fold_packs,
    _merge_handle_conflicts as _merge_handle_conflicts,
    _merge_load_snapshots as _merge_load_snapshots,
    _merge_pick_base as _merge_pick_base,
    _merge_print_summary as _merge_print_summary,
)

if TYPE_CHECKING:
    from .buildsource.build_evidence import BuildEvidence
    from .buildsource.source_abi import SourceAbiSurface
    from .buildsource.source_graph import SourceGraphSummary
    from .checker_types import Change, DiffResult
    from .model import AbiSnapshot
    from .policy_file import PolicyFile


def _resolve_side_pack(
    build_info: Path | None,
    sources: Path | None,
    snap: AbiSnapshot | None,
) -> BuildSourcePack | None:
    """Resolve one compare side's pack from flags first, then embedded facts.

    Explicit ``--*-build-info`` / ``--*-sources`` pack directories override the
    snapshot's embedded payload per layer; when neither flag is given the
    embedded ``snap.build_source`` is used as-is (single-artifact UX).
    """
    bi_pack = _load_pack_or_raise(build_info) if build_info is not None else None
    src_pack = _load_pack_or_raise(sources) if sources is not None else None
    embedded = snap.build_source if snap is not None else None
    if bi_pack is None and src_pack is None:
        return embedded

    # Each flag's pack exposes *every* layer it carries (a pack collected by
    # `abicheck collect` may hold build + source + graph). --build-info wins for
    # L3, --sources wins for L4/L5, the embedded payload backfills, and the
    # coverage manifest is rebuilt per-layer from the supplying pack.
    return _combine_packs(bi_pack, src_pack, embedded)


def diff_embedded_build_source(
    old_build_info: Path | None,
    new_build_info: Path | None,
    old_sources: Path | None,
    new_sources: Path | None,
    collect_mode: str,
    new_snapshot: AbiSnapshot,
    old_snapshot: AbiSnapshot | None = None,
    policy_file: PolicyFile | None = None,
) -> tuple[list[Change], list[dict[str, object]], dict[str, object]]:
    """Diff each side's build-info + source facts, echo coverage, return findings.

    Each side's facts come from the snapshot's *embedded* ``build_source``
    payload (single-artifact UX) unless an out-of-band ``--*-build-info`` /
    ``--*-sources`` pack directory overrides it. Per ADR-028 D3 the findings are
    folded into the ordinary verdict pipeline as ``extra_changes`` and never
    override artifact-backed verdicts. The D7 coverage table is printed to
    stderr (covers every output format) and also returned as serialized rows so
    the JSON report can carry a structured ``layer_coverage`` block. Returns
    ``(changes, coverage_rows)``.

    When ``old_snapshot`` is supplied, the base and target coverage are compared
    layer-by-layer: if the base was analyzed with evidence the target lacks
    (e.g. a full base scan vs a binary+headers-only target), a single
    ``EVIDENCE_COVERAGE_ASYMMETRIC`` finding spells out exactly which pieces the
    target is missing so the degraded comparison is never silent.

    The third tuple element is a partial ADR-033 D9 metrics dict (coverage flags
    plus the build-context-drift / source-only finding split this function can
    count first-hand); ``cli.py`` fills in timing and run-wide totals via
    :func:`finalize_evidence_metrics`. Returns
    ``(changes, coverage_rows, metrics)``.
    """
    from .buildsource.build_diff import check_header_parse_drift, diff_build_evidence

    old_pack = _resolve_side_pack(old_build_info, old_sources, old_snapshot)
    new_pack = _resolve_side_pack(new_build_info, new_sources, new_snapshot)

    if old_pack is None and new_pack is None:
        if collect_mode != "off":
            click.echo(
                f"Note: --depth collected evidence mode '{collect_mode}' was "
                "requested but no build-info/source facts were embedded or "
                "supplied; inline collection for this mode is not yet available. "
                "Use `abicheck collect` then embed with `dump --build-info/"
                "--sources` (or pass --old/new pack dirs).",
                err=True,
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
        _asym = _detect_coverage_asymmetry(
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
        from .buildsource.source_diff import diff_source_abi

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
        from .buildsource.source_graph import diff_source_graph_findings

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
    coverage = _optional_coverage(new_pack)
    intrinsic = _intrinsic_coverage(new_snapshot)
    _echo_coverage(intrinsic, coverage)
    if old_snapshot is not None:
        _echo_compare_side_coverage(
            _intrinsic_coverage(old_snapshot),
            _optional_coverage(old_pack),
            intrinsic,
            coverage,
        )
    _echo_capabilities(intrinsic, coverage)
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
) -> tuple[
    list[Change] | None, list[dict[str, object]], dict[str, object], list[Change]
]:
    """Run inline build-info/source diffing for ``compare`` and time it.

    Gates on whether any pack flag, embedded payload, or non-``off`` collect mode
    is in play; folds the evidence findings into ``extra_changes``; and wall-clocks
    the inline diffing for the ADR-033 D6/D9 ``extractor.duration_seconds`` metric.
    ``policy_file`` carries the ADR-033 D7 evidence-policy knobs that modulate the
    findings' verdict category. Returns
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
) -> None:
    """Finalize and attach the ADR-033 D9 evidence metrics onto ``result``.

    Counts the finding buckets from the *retained* (post-suppression)
    ``result.changes`` so they partition the reported findings consistently
    (Codex review): build-context-drift and source-only come from each finding's
    ``evidence_category`` tag, and artifact-backed is everything not externally
    injected via ``extra_changes`` (build/source evidence *and* probe-matrix
    findings — none from L0–L2 diffing). Adds the suppression/surface-demotion
    totals, then echoes the D6 timing summary. No-op when no evidence involved.
    """
    if not metrics:
        return
    counts = finding_bucket_counts(result.changes, injected_changes)
    for bucket, n in counts.items():
        metrics[f"findings.{bucket}.count"] = n
    metrics["findings.demoted_by_surface.count"] = result.out_of_surface_count
    metrics["findings.suppressed_with_reason.count"] = result.suppressed_count
    result.evidence_metrics = metrics
    echo_evidence_metrics(metrics)


def _load_pack_or_raise(evidence_dir: Path) -> BuildSourcePack:
    try:
        return BuildSourcePack.load(evidence_dir)
    except (FileNotFoundError, ValueError) as exc:
        raise click.ClickException(
            f"Invalid evidence pack at {evidence_dir}: {exc}"
        ) from exc


def _intrinsic_coverage(snap: AbiSnapshot) -> list[LayerCoverage]:
    """Derive L0/L1/L2 coverage rows from a snapshot (ADR-028 D7)."""

    def row(layer: str, present: bool, detail: str) -> LayerCoverage:
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


def _optional_coverage(pack: BuildSourcePack | None) -> list[LayerCoverage]:
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


# Human-readable layer names, ordered shallow→deep, shared by the coverage
# table and the asymmetry finding so both speak the same vocabulary.
_LAYER_NAMES: dict[str, str] = {
    "L0": "L0 binary metadata",
    "L1": "L1 debug info",
    "L2": "L2 public header AST",
    "L3_build": "L3 build context",
    "L4_source_abi": "L4 source ABI replay",
    "L5_source_graph": "L5 source graph summary",
}


def _echo_coverage(
    intrinsic: list[LayerCoverage], optional: list[LayerCoverage]
) -> None:
    """Print the D7 evidence-coverage table to stderr (all output formats)."""
    click.echo("Evidence coverage:", err=True)
    for cov in [*intrinsic, *optional]:
        extra = ""
        if cov.status != CoverageStatus.NOT_COLLECTED:
            extra = f", {cov.confidence.value} confidence"
            if cov.detail:
                extra += f": {cov.detail}"
        click.echo(
            f"  {_LAYER_NAMES.get(cov.layer, cov.layer):<26} {cov.status.value}{extra}",
            err=True,
        )


def _echo_compare_side_coverage(
    old_intrinsic: list[LayerCoverage],
    old_optional: list[LayerCoverage],
    new_intrinsic: list[LayerCoverage],
    new_optional: list[LayerCoverage],
) -> None:
    """Print old/new layer coverage so mixed-evidence compares are explicit."""
    old_by_layer = {c.layer: c for c in (*old_intrinsic, *old_optional)}
    new_by_layer = {c.layer: c for c in (*new_intrinsic, *new_optional)}
    click.echo("Evidence coverage by side:", err=True)
    for layer, name in _LAYER_NAMES.items():
        old = old_by_layer.get(layer)
        new = new_by_layer.get(layer)
        old_status = old.status.value if old is not None else "not_collected"
        new_status = new.status.value if new is not None else "not_collected"
        marker = " (asymmetric)" if old_status != new_status else ""
        click.echo(
            f"  {name:<26} old={old_status:<13} new={new_status}{marker}",
            err=True,
        )


def _layer_presence(snap: AbiSnapshot, pack: BuildSourcePack | None) -> dict[str, bool]:
    """Map every evidence layer id → present? for one side of the compare.

    L0/L1/L2 are intrinsic to the snapshot; L3/L4/L5 come from the pack manifest
    coverage (with the loaded ``build_evidence`` object treated as authoritative
    proof that L3 is present even if the manifest row is stale).
    """
    present = {
        row.layer: row.status != CoverageStatus.NOT_COLLECTED
        for row in _intrinsic_coverage(snap)
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


def _detect_coverage_asymmetry(
    old_snap: AbiSnapshot,
    old_pack: BuildSourcePack | None,
    new_snap: AbiSnapshot,
    new_pack: BuildSourcePack | None,
) -> list[Change]:
    """Flag layers the base was analyzed with but the target lacks (ADR-028 D7).

    A full base scan (binary + debug + headers + build + sources) compared
    against a binary+headers-only target is a legitimate, supported comparison —
    but it is *degraded*: the layers the target is missing cannot prove or
    disprove changes, so the verdict is scoped to what both sides share. Rather
    than let that happen silently, emit one ``EVIDENCE_COVERAGE_ASYMMETRIC``
    RISK finding naming exactly which pieces the target is missing.

    Only the base→target degradation direction is reported (target missing what
    the base had). A target that is *richer* than the base does not undermine
    the comparison, so it is not flagged here.
    """
    from .checker_policy import ChangeKind
    from .checker_types import Change

    old_present = _layer_presence(old_snap, old_pack)
    new_present = _layer_presence(new_snap, new_pack)
    missing = [
        layer
        for layer in _LAYER_NAMES
        if old_present.get(layer) and not new_present.get(layer)
    ]
    if not missing:
        return []

    human = ", ".join(_LAYER_NAMES[m] for m in missing)
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
_CHECK_CAPABILITIES: tuple[tuple[str, str, str, str], ...] = (
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


def _echo_capabilities(
    intrinsic: list[LayerCoverage], optional: list[LayerCoverage]
) -> None:
    """Print exactly which check categories are enabled — and why others are not.

    Driven by the evidence coverage (ADR-028 D7): each check category is gated on
    one evidence layer, so the user sees, for the inputs they actually provided
    (binary only → +debug → +headers → +build data → +sources), which checks ran
    and the concrete reason each disabled one is off.
    """
    # Only a PRESENT layer enables its checks: a PARTIAL layer (e.g. L4 when clang
    # was missing or every TU failed, so no entities were extracted) ran but
    # produced nothing, and must read as [off], not [on] (CodeRabbit review).
    present = {
        c.layer for c in (*intrinsic, *optional) if c.status == CoverageStatus.PRESENT
    }
    click.echo("Checks enabled for this scan (and why others are not):", err=True)
    for label, layer, how, why_off in _CHECK_CAPABILITIES:
        if layer in present:
            click.echo(f"  [on]  {label} — {how}", err=True)
        else:
            click.echo(f"  [off] {label} — {why_off}", err=True)


def _build_coverage(
    merged: BuildEvidence,
    has_build: bool,
    surface: SourceAbiSurface | None = None,
    source_detail: str = "",
    graph: SourceGraphSummary | None = None,
    graph_detail: str = "",
) -> list[LayerCoverage]:
    """Build the L3/L4/L5 coverage rows for the pack manifest (ADR-028 D7)."""
    if has_build:
        systems = sorted({g.kind for g in merged.generators}) or ["generic"]
        l3 = LayerCoverage(
            layer=DataLayer.L3_BUILD.value,
            status=CoverageStatus.PRESENT,
            confidence=LayerConfidence.HIGH
            if merged.targets
            else LayerConfidence.REDUCED,
            detail=(
                f"{'+'.join(systems)}, {len(merged.compile_units)} compile units, "
                f"{len(merged.targets)} targets"
            ),
        )
    else:
        l3 = LayerCoverage(
            layer=DataLayer.L3_BUILD.value, status=CoverageStatus.NOT_COLLECTED
        )
    # L4 is PRESENT when at least one TU parsed into the surface, PARTIAL when
    # replay ran but every TU failed/was empty (e.g. clang missing), else
    # NOT_COLLECTED. The surface keeps decls/types only when extraction worked.
    if surface is not None:
        # PRESENT when the surface actually carries reachable entities; PARTIAL
        # when replay ran but yielded nothing (tool missing, all TUs failed, or
        # no public surface matched) — never silently NOT_COLLECTED, so the
        # capability report can explain the gap.
        any_entities = bool(
            surface.reachable_declarations
            or surface.reachable_types
            or surface.reachable_macros
            or surface.reachable_templates
            or surface.reachable_inline_bodies
        )
        if any_entities:
            l4 = LayerCoverage(
                layer=DataLayer.L4_SOURCE_ABI.value,
                status=CoverageStatus.PRESENT,
                confidence=LayerConfidence.HIGH,
                detail=source_detail,
            )
        else:
            l4 = LayerCoverage(
                layer=DataLayer.L4_SOURCE_ABI.value,
                status=CoverageStatus.PARTIAL,
                confidence=LayerConfidence.REDUCED,
                detail=source_detail,
            )
    else:
        l4 = LayerCoverage(
            layer=DataLayer.L4_SOURCE_ABI.value, status=CoverageStatus.NOT_COLLECTED
        )
    # L5 is PRESENT when the graph carries edges; PARTIAL when a graph was built
    # but had no build evidence to fold (so it is empty), else NOT_COLLECTED.
    if graph is not None:
        if graph.edges:
            l5 = LayerCoverage(
                layer=DataLayer.L5_SOURCE_GRAPH.value,
                status=CoverageStatus.PRESENT,
                confidence=LayerConfidence.REDUCED,
                detail=graph_detail,
            )
        else:
            l5 = LayerCoverage(
                layer=DataLayer.L5_SOURCE_GRAPH.value,
                status=CoverageStatus.PARTIAL,
                confidence=LayerConfidence.UNKNOWN,
                detail=graph_detail or "no build evidence to fold into a graph",
            )
    else:
        l5 = LayerCoverage(
            layer=DataLayer.L5_SOURCE_GRAPH.value, status=CoverageStatus.NOT_COLLECTED
        )
    return [l3, l4, l5]


def _exported_symbols_from_binary(binary: Path | None) -> list[str]:
    """Best-effort exported (mangled) symbol names from ``binary`` for D5 linking.

    Used so the source-decl → binary-symbol mapping (and
    ``source_decl_binary_symbol_mismatch``) is populated. Failures are swallowed
    (returns ``[]``): the other eight source findings do not need symbols, so a
    binary that cannot be parsed must not block L4 collection.
    """
    if binary is None or not Path(binary).is_file():
        return []
    try:
        from .service import detect_binary_format, run_dump

        fmt = detect_binary_format(Path(binary))
        if not fmt:
            return []
        snap = run_dump(Path(binary), fmt)
    except Exception:  # noqa: BLE001 - best-effort; never fail collection on this
        return []
    syms = {fn.mangled for fn in snap.functions if fn.mangled}
    syms |= {v.mangled for v in snap.variables if getattr(v, "mangled", "")}
    return sorted(syms)


def _collect_source_graph(
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    source_graph: str,
    changed_paths: tuple[str, ...],
    kythe_entries: Path | None,
    codeql_results: Path | None,
    codeql_extends_results: Path | None,
    surface: SourceAbiSurface | None,
    clang_bin: str,
) -> tuple[SourceGraphSummary | None, str]:
    """Build the optional L5 source graph and fold in any requested augmentations.

    Kythe/CodeQL ingestion (pre-captured, non-executing) implies graph
    collection — their JSON is useless without a graph to fold into. Returns
    ``(graph, graph_detail)``; ``graph`` is ``None`` when no graph was
    requested.

    Call/type/include-graph edges are **not** separate opt-in flags here —
    they fold automatically whenever both ``--source-abi`` (L4) and
    ``--source-graph summary`` (L5) are active, mirroring exactly the inline
    ``dump --sources`` path's own automatic gate
    (``inline._build_inline_graph``'s ``with_call_graph``). The two paths
    used to diverge: this one required explicit ``--call-graph``/
    ``--include-graph`` flags with no inline-path equivalent, which read as
    dead CLI surface on the recommended path and a hidden, easy-to-miss
    requirement on this one. Sharing ``inline_graph_fold``'s fold functions
    (rather than this module's own now-removed near-duplicates) keeps the two
    paths from drifting again.
    """
    if (
        kythe_entries or codeql_results or codeql_extends_results
    ) and source_graph == "off":
        source_graph = "summary"
    if source_graph != "summary":
        return None, ""

    from .buildsource.source_graph import build_source_graph

    # Fold the L4 surface in too when it was collected (--source-abi), so the
    # graph carries the public-reachability + source↔binary slices.
    graph = build_source_graph(merged, source_abi=surface)
    if surface is not None:
        from .buildsource.inline_graph_fold import (
            fold_call_graph,
            fold_include_graph,
            fold_type_graph,
        )

        fold_call_graph(graph, merged, clang_bin, extractors, changed_paths)
        fold_type_graph(graph, merged, clang_bin, extractors, changed_paths)
        fold_include_graph(graph, merged, clang_bin, extractors, changed_paths)
    if kythe_entries or codeql_results or codeql_extends_results:
        _ingest_graph_backends(
            graph,
            extractors,
            kythe_entries=kythe_entries,
            codeql_results=codeql_results,
            codeql_extends_results=codeql_extends_results,
        )
    graph.finalize()
    graph_detail = (
        f"{len(graph.nodes)} nodes, {len(graph.edges)} edges "
        f"({graph.coverage.get('targets', 0)} targets, "
        f"{graph.coverage.get('compile_units', 0)} compile units, "
        f"{graph.coverage.get('source_decls', 0)} source decls, "
        f"{graph.coverage.get('call_edges', {}).get('count', 0)} call edges, "
        f"{graph.coverage.get('include_edges', {}).get('count', 0)} include edges)"
    )
    extractors.append(
        ExtractorRecord(
            name="source_graph:summary",
            status="ok" if graph.nodes else "partial",
            detail=graph_detail
            if graph.nodes
            else "no build evidence to fold into a graph",
        )
    )
    return graph, graph_detail


def _enforce_strict_mode(
    extractors: list[ExtractorRecord], merged: BuildEvidence, collection_mode: str
) -> None:
    """Fail the command if strict mode is set and any extractor is incomplete (ADR-032 D9).

    Both a failed row and a skipped one (e.g. an extractor gated out by the action
    ceiling, so its requested evidence is absent) count — strict requires the
    evidence to be present. Called *before* the success output so a strict run
    never prints "Evidence pack written" and then exits non-zero.
    """
    if collection_mode != "strict":
        return
    incomplete = [e for e in extractors if e.status in ("failed", "skipped")]
    if not incomplete:
        return
    names = ", ".join(sorted(f"{e.name}:{e.status}" for e in incomplete))
    for diag in merged.diagnostics:
        click.echo(f"  note: {diag}", err=True)
    raise click.ClickException(
        f"strict collection mode: {len(incomplete)} extractor(s) did not "
        f"produce valid evidence ({names}). Fix the inputs/tools, grant the "
        "needed actions, or use --collection-mode permissive."
    )


def _echo_collection_summary(
    pack: BuildSourcePack,
    merged: BuildEvidence,
    output: Path,
    *,
    has_build: bool,
    source_abi: bool,
    source_detail: str,
    graph: SourceGraphSummary | None,
    graph_detail: str,
) -> None:
    """Print the per-layer summary for a successfully written evidence pack."""
    click.echo(f"Evidence pack written to {output}")
    click.echo(f"  content hash: {pack.content_hash()}")
    if has_build:
        click.echo(
            f"  L3 build context: {len(merged.compile_units)} compile units, "
            f"{len(merged.targets)} targets, {len(merged.toolchains)} toolchains"
        )
    else:
        click.echo("  L3 build context: not collected (no adapters produced facts)")
    if source_abi:
        click.echo(f"  L4 source ABI replay: {source_detail}")
    if graph is not None:
        click.echo(f"  L5 source graph: {graph_detail or 'empty (no build evidence)'}")
    for diag in merged.diagnostics:
        click.echo(f"  note: {diag}", err=True)


#: ``collect --from`` adapter specs (ADR-037 CLI consolidation). The six former
#: per-adapter flags (``--cmake``/``--ninja`` live toggles + ``--ninja-compdb``/
#: ``--bazel-cquery``/``--bazel-aquery``/``--make-dry-run`` pre-captured paths)
#: collapse onto one repeatable ``--from adapter[=path]``. Live adapters take no
#: ``=path`` (they read ``--build-dir``); pre-captured ones require one.
_FROM_LIVE_ADAPTERS: frozenset[str] = frozenset({"cmake", "ninja"})
#: pre-captured adapter name → the ``_run_adapters`` kwarg it feeds.
_FROM_PATH_ADAPTERS: dict[str, str] = {
    "ninja-compdb": "ninja_compdb",
    "bazel-cquery": "bazel_cquery",
    "bazel-aquery": "bazel_aquery",
    "make": "make_dry_run",
}


def parse_from_specs(specs: tuple[str, ...]) -> dict[str, object]:
    """Parse ``collect --from adapter[=path]`` specs into ``_run_adapters`` kwargs.

    Returns a dict with ``cmake``/``ninja`` bools and ``ninja_compdb``/
    ``bazel_cquery``/``bazel_aquery``/``make_dry_run`` paths (None when unset).
    Raises :class:`click.UsageError` on an unknown adapter, a live adapter given
    a ``=path``, a pre-captured adapter given no path, or the same adapter passed
    twice (so a repeated ``--from`` never silently last-wins). Pure (no I/O) so it
    is unit-tested directly.
    """
    out: dict[str, object] = {
        "cmake": False,
        "ninja": False,
        "ninja_compdb": None,
        "bazel_cquery": None,
        "bazel_aquery": None,
        "make_dry_run": None,
    }
    valid = sorted(_FROM_LIVE_ADAPTERS | set(_FROM_PATH_ADAPTERS))
    seen: set[str] = set()
    for spec in specs:
        name, sep, value = spec.partition("=")
        name = name.strip()
        if name in seen:
            raise click.UsageError(
                f"--from {name} was given more than once; pass each adapter "
                "at most once."
            )
        if name in _FROM_LIVE_ADAPTERS:
            if sep:
                raise click.UsageError(
                    f"--from {name} is a live adapter and takes no '=path' "
                    "(it reads --build-dir)."
                )
            out[name] = True
        elif name in _FROM_PATH_ADAPTERS:
            if not value:
                raise click.UsageError(
                    f"--from {name} requires a pre-captured path "
                    f"(e.g. --from {name}=path)."
                )
            out[_FROM_PATH_ADAPTERS[name]] = Path(value)
        else:
            raise click.UsageError(
                f"--from: unknown adapter {name!r}; expected one of {valid}."
            )
        seen.add(name)
    return out


def _run_adapters(
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    compile_db: Path | None,
    build_dir: Path | None,
    cmake: bool,
    ninja: bool,
    ninja_compdb: Path | None,
    bazel_cquery: Path | None,
    bazel_aquery: Path | None,
    make_dry_run: Path | None,
    binary: Path | None,
    read_compiler_record: bool,
    build_system: str,
    record_bazel_inputs: bool,
    verbose: bool,
) -> None:
    """Run the requested build-evidence adapters and fold them into *merged*."""
    # Import adapters lazily so `collect --help` stays cheap.
    from .buildsource.adapters import (
        BazelAdapter,
        CMakeFileApiAdapter,
        CompileDbAdapter,
        MakeAdapter,
        NinjaAdapter,
    )

    if compile_db is not None:
        try:
            ev = CompileDbAdapter(compile_db, build_system=build_system).collect()
            merged.merge(ev)
            extractors.append(
                ExtractorRecord(
                    name="compile_commands",
                    status="ok",
                    inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                    detail=f"{len(ev.compile_units)} compile units",
                )
            )
        except (OSError, ValueError) as exc:
            extractors.append(
                ExtractorRecord(
                    name="compile_commands",
                    status="failed",
                    inputs=[DEFAULT_REDACTION.path(str(compile_db))],
                    detail=str(exc),
                )
            )
            merged.diagnostics.append(f"compile_commands: {exc}")

    if cmake:
        if build_dir is None:
            raise click.UsageError("--cmake requires --build-dir.")
        ev = CMakeFileApiAdapter(build_dir).collect()
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="cmake_file_api",
                status="ok" if ev.targets else "partial",
                inputs=[DEFAULT_REDACTION.path(str(build_dir))],
                detail=f"{len(ev.targets)} targets, {len(ev.toolchains)} toolchains",
            )
        )

    if ninja or ninja_compdb is not None:
        if build_dir is None and ninja_compdb is None:
            raise click.UsageError(
                "--ninja requires --build-dir (or pass --ninja-compdb)."
            )
        adapter = NinjaAdapter(build_dir, compdb=ninja_compdb)
        ev = adapter.collect()
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="ninja",
                status="ok" if ev.compile_units else "partial",
                inputs=[DEFAULT_REDACTION.path(str(build_dir or ninja_compdb))],
                detail=f"{len(ev.compile_units)} compile units",
            )
        )

    if bazel_cquery is not None or bazel_aquery is not None:
        ev = BazelAdapter(
            workspace=build_dir,
            cquery=bazel_cquery,
            aquery=bazel_aquery,
            record_inputs=record_bazel_inputs,
        ).collect()
        merged.merge(ev)
        inputs = [
            DEFAULT_REDACTION.path(str(p))
            for p in (bazel_cquery, bazel_aquery)
            if p is not None
        ]
        extractors.append(
            ExtractorRecord(
                name="bazel",
                status="ok"
                if (ev.targets or ev.compile_units or ev.link_units)
                else "partial",
                inputs=inputs,
                detail=(
                    f"{len(ev.targets)} targets, {len(ev.compile_units)} compile units, "
                    f"{len(ev.link_units)} link units"
                ),
            )
        )

    if make_dry_run is not None:
        # Only a pre-captured transcript — the Make adapter never runs make,
        # because `make -n` still executes `+` recipes and `$(shell …)`.
        ev = MakeAdapter(build_dir, dry_run=make_dry_run).collect()
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="make",
                status="ok" if ev.compile_units else "partial",
                inputs=[DEFAULT_REDACTION.path(str(make_dry_run))],
                detail=f"{len(ev.compile_units)} compile units (reduced confidence)",
            )
        )

    if read_compiler_record:
        if binary is None:
            raise click.UsageError("--read-compiler-record requires --binary.")
        from .buildsource.compiler_record import extract_compiler_record

        ev = extract_compiler_record(binary)
        merged.merge(ev)
        extractors.append(
            ExtractorRecord(
                name="compiler_record",
                status="ok" if (ev.toolchains or ev.compile_units) else "partial",
                inputs=[DEFAULT_REDACTION.path(str(binary))],
                detail=f"{len(ev.toolchains)} toolchains, {len(ev.compile_units)} compile units",
            )
        )


def _run_external_extractors(
    merged: BuildEvidence,
    extractors: list[ExtractorRecord],
    *,
    manifests: tuple[Path, ...],
    pack_root: Path,
    binary: Path | None,
    build_dir: Path | None,
    source_root: Path | None,
    compile_db: Path | None,
    allow_build_query: bool,
    collection_mode: str,
    verbose: bool,
) -> None:
    """Run explicitly-registered external CLI extractors (ADR-032 D3/D5/D9).

    Each manifest is loaded from the operator-provided path (never auto-
    discovered). The run-permitted action set starts at ``inspect`` and adds
    ``query_build_system`` only with ``--allow-build-query``; a manifest that
    needs an action outside that set is recorded as skipped rather than run
    (its declared actions are a ceiling intersected with what the run allows).
    Normalized ``build_evidence`` outputs are folded into *merged*; failures are
    captured as extractor rows so the collection-mode policy (D9) can act on them.
    """
    from .buildsource.build_evidence import BuildEvidence as _BuildEvidence
    from .buildsource.extractor import (
        CollectionAction,
        CollectionContext,
        CollectionMode,
    )
    from .buildsource.extractor_manifest import (
        ManifestError,
        load_extractor_manifest,
        run_external_extractor,
    )

    run_permitted = {CollectionAction.INSPECT}
    if allow_build_query:
        run_permitted.add(CollectionAction.QUERY_BUILD_SYSTEM)

    pack_root.mkdir(parents=True, exist_ok=True)

    for manifest_path in manifests:
        try:
            manifest = load_extractor_manifest(manifest_path)
        except ManifestError as exc:
            extractors.append(
                ExtractorRecord(
                    name=f"external:{manifest_path.name}",
                    status="failed",
                    inputs=[DEFAULT_REDACTION.path(str(manifest_path))],
                    detail=str(exc),
                )
            )
            merged.diagnostics.append(f"extractor manifest {manifest_path}: {exc}")
            continue

        context = CollectionContext(
            binary_paths=[binary] if binary else [],
            build_root=build_dir,
            source_root=source_root,
            compile_db=compile_db,
            allowed_actions=set(run_permitted),
            collection_mode=CollectionMode(collection_mode),
            redaction_policy=DEFAULT_REDACTION,
        )
        # An extractor gated out by the action ceiling comes back as a 'skipped'
        # record (run_external_extractor decides via discover()), so there is no
        # permission exception for the caller to handle here.
        _norm, record = run_external_extractor(manifest, context, pack_root)

        extractors.append(record)
        if record.status != "ok":
            merged.diagnostics.append(
                f"{manifest.name}: {record.detail or 'extractor did not complete'}"
            )
            _purge_external_outputs(pack_root, manifest)
            continue

        # Reject output kinds collect cannot fold yet — only
        # build_evidence is wired into the pack here. A manifest that advertises
        # a source_abi / source_graph_summary output would otherwise be recorded
        # ok while its evidence is silently dropped (and pack.write() removes the
        # canonical source/graph files), so the requested evidence is absent even
        # though the extractor "succeeded" (Codex P2). Fail loudly instead.
        unsupported = sorted(
            {o.kind for o in manifest.outputs if o.kind != "build_evidence"}
        )
        if unsupported:
            record.status = "failed"
            record.detail = (
                record.detail or f"unsupported output kind(s): {', '.join(unsupported)}"
            )
            # The outputs are about to be purged from the pack, so the ledger row
            # must not keep advertising their (now-removed) paths (Codex P2).
            record.artifacts = []
            merged.diagnostics.append(
                f"{manifest.name}: output kind(s) {', '.join(unsupported)} are not yet "
                "supported by collect (only build_evidence is folded into the pack)"
            )
            _purge_external_outputs(pack_root, manifest)
            continue

        # Fold any normalized build_evidence outputs into the merged L3 evidence.
        # `validate` only proved each file is JSON; it may still be structurally
        # invalid BuildEvidence (e.g. a compile unit missing its id), which
        # BuildEvidence.from_dict surfaces as KeyError/TypeError. Parse *all*
        # declared outputs first and merge only if every one is valid — so a
        # later malformed output never leaves an earlier one's evidence merged
        # from an extractor we then mark failed (D8: invalid output must not
        # influence collected facts). A failure downgrades the ledger row, never
        # crashes the command (D9 permissive), and makes strict mode reject it.
        import json as _json

        parsed: list[_BuildEvidence] = []
        fold_ok = True
        for output in manifest.outputs:
            if output.kind != "build_evidence":
                continue
            be_path = pack_root / output.path
            try:
                parsed.append(
                    _BuildEvidence.from_dict(
                        _json.loads(be_path.read_text(encoding="utf-8"))
                    )
                )
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                fold_ok = False
                record.status = "failed"
                record.detail = record.detail or f"invalid build_evidence output: {exc}"
                # _purge_external_outputs (below) removes these files, so the
                # failed ledger row must not keep advertising stale paths to a
                # missing/replaced artifact (Codex P2).
                record.artifacts = []
                merged.diagnostics.append(
                    f"{manifest.name}: could not fold {output.path}: {exc}"
                )
                break
        if fold_ok:
            for build_evidence in parsed:
                merged.merge(build_evidence)
        else:
            _purge_external_outputs(pack_root, manifest)


def _purge_external_outputs(pack_root: Path, manifest: object) -> None:
    """Remove a failed external extractor's normalized outputs from the pack.

    A failed/skipped extractor must be isolated from the collected pack: its
    normalized output files (and its ``normalized/<name>/`` subtree) would
    otherwise be hashed into ``BuildSourcePack`` ``manifest.artifacts`` and the
    content hash, so an invalid output would change pack identity and publish a
    digest for evidence that was never folded (Codex P2). Raw artifacts under
    ``raw/`` are *not* removed — they are provenance-only, never hashed, and are
    what audit mode preserves for debugging.
    """
    import shutil

    name = getattr(manifest, "name", "")
    for output in getattr(manifest, "outputs", []):
        try:
            (pack_root / output.path).unlink()
        except OSError:
            pass
    norm_dir = pack_root / "normalized" / name
    if norm_dir.is_dir():
        shutil.rmtree(norm_dir, ignore_errors=True)


def _ingest_graph_backends(
    graph: SourceGraphSummary,
    extractors: list[ExtractorRecord],
    *,
    kythe_entries: Path | None,
    codeql_results: Path | None,
    codeql_extends_results: Path | None,
) -> None:
    """Fold pre-captured Kythe/CodeQL exports into *graph* (ADR-031 D5).

    Non-executing (ADR-028 D6): reads the provided JSON exports only. A malformed
    or missing file records a failed extractor row and is skipped.
    """
    import json as _json

    from .buildsource.graph_backends import (
        ingest_codeql_call_results,
        ingest_codeql_extends_results,
        ingest_kythe_entries,
    )

    def _load(path: Path, name: str) -> object | None:
        try:
            parsed: object = _json.loads(Path(path).read_text(encoding="utf-8"))
            return parsed
        except (OSError, ValueError) as exc:
            extractors.append(
                ExtractorRecord(
                    name=name,
                    status="failed",
                    inputs=[DEFAULT_REDACTION.path(str(path))],
                    detail=str(exc),
                )
            )
            return None

    if kythe_entries is not None:
        data = _load(kythe_entries, "graph_backend:kythe")
        if data is not None:
            entries = (
                data
                if isinstance(data, list)
                else (data.get("entries", []) if isinstance(data, dict) else [])
            )
            added = ingest_kythe_entries(
                graph, entries, ref=DEFAULT_REDACTION.path(str(kythe_entries))
            )
            extractors.append(
                ExtractorRecord(
                    name="graph_backend:kythe",
                    status="ok" if added else "partial",
                    inputs=[DEFAULT_REDACTION.path(str(kythe_entries))],
                    detail=f"{added} edges ingested",
                )
            )

    if codeql_results is not None:
        data = _load(codeql_results, "graph_backend:codeql")
        if isinstance(data, dict):
            added = ingest_codeql_call_results(
                graph, data, ref=DEFAULT_REDACTION.path(str(codeql_results))
            )
            extractors.append(
                ExtractorRecord(
                    name="graph_backend:codeql",
                    status="ok" if added else "partial",
                    inputs=[DEFAULT_REDACTION.path(str(codeql_results))],
                    detail=f"{added} edges ingested",
                )
            )

    if codeql_extends_results is not None:
        data = _load(codeql_extends_results, "graph_backend:codeql_extends")
        if data is not None:
            if isinstance(data, dict):
                added = ingest_codeql_extends_results(
                    graph, data, ref=DEFAULT_REDACTION.path(str(codeql_extends_results))
                )
                extractors.append(
                    ExtractorRecord(
                        name="graph_backend:codeql_extends",
                        status="ok" if added else "partial",
                        inputs=[DEFAULT_REDACTION.path(str(codeql_extends_results))],
                        detail=f"{added} edges ingested",
                    )
                )
            else:
                # Codex review: valid JSON that isn't an object (e.g. a bare
                # array) used to leave no record at all, silently hiding that
                # the requested backend was never ingested.
                extractors.append(
                    ExtractorRecord(
                        name="graph_backend:codeql_extends",
                        status="failed",
                        inputs=[DEFAULT_REDACTION.path(str(codeql_extends_results))],
                        detail="expected a JSON object with a top-level \"#select\"",
                    )
                )
