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

"""Sprint 9: HTML report generator.

Generates a self-contained HTML report:

  - Verdict banner (BREAKING / COMPATIBLE / NO_CHANGE)
  - Binary Compatibility % metric (based on old exported symbol count)
  - Summary table: changes by category (functions, variables, types, enums, ELF)
  - Sectioned changes: Removed | Changed | Added (with anchored navigation)
  - Suppressed changes section (if any)
  - Demangled symbol names as display text, mangled as tooltip

No external CSS/JS dependencies — fully self-contained single HTML file.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

# Page chrome (DOCTYPE/head/stylesheet/body frame, verdict palette, footer) now
# lives in one shared seam (``html_template``). ``_CSS`` is re-exported via
# redundant alias (it was previously defined in this module) so any code that
# imported it from here keeps working. ``generate_html_report`` itself no
# longer touches ``_VERDICT_STYLE``/``render_document``/``render_footer``
# directly -- those moved into ``report/render_html.py`` alongside the rest
# of this module's formatting responsibility (ADR-061 Phase 2 item 1).
from ..checker_types import DiffResult
from ..html_template import _CSS as _CSS
from ..model.change_catalog.kinds import HasKind
from .change_operation import entity_for_change
from .change_summary import ENTITY_LABELS, UNCLASSIFIED_LABEL

# ADR-061 Phase 2 item 1: the pure HTML projection half of this module.
# Every ``compute_*`` below returns one of these frozen structs (or, for the
# whole document, a plain JSON-shaped mapping); the matching ``render_*``
# turns it into markup and makes no decision of its own. The low-level
# formatters re-exported under their original private names physically live
# there now (see ``render_html``'s own docstring for why moving them, rather
# than leaving them here, is what avoids a same-layer import cycle) -- every
# existing caller and its direct test coverage resolves through these
# aliases unchanged.
from .disposition_audit import disposition_audit_reusing_document
from .document import ReportDocument
from .edge_coverage_section import compute_edge_coverage_section
from .envelope import ReportEnvelope, resolved_document
from .finding import (
    active_reclassify_labels,
    effective_bucket,
    kind_sets_for_result,
)
from .gating_budget import non_gating_change_ids
from .html_change_rows import (
    compute_full_change_rows as compute_full_change_rows,
)
from .html_sections import (
    build_sections_data,
    compute_not_evaluated_section as compute_not_evaluated_section,
)
from .outcome_summary import project_outcome
from .policy_facts import accepted_break_ids, compatibility_metrics, kind_str
from .redundancy_reasons import redundancy_reason_text
from .render_html import (
    ConfidenceData,
    FileMetadataTable,
    GateCardData,
    ImpactData,
    ImpactEntry,
    NavBarData,
    ScopedVerdictData,
    SummaryCategoryRow,
    SummaryTableData,
)
from .render_html_document import render_html_document
from .use_case_impact_render import use_cases_by_finding_for
from .versioning_policy_section import compute_versioning_policy

if TYPE_CHECKING:
    from datetime import date


# ---------------------------------------------------------------------------
# HTML generation helpers
# ---------------------------------------------------------------------------


def compute_file_metadata(result: object) -> FileMetadataTable | None:
    """Collect the library-file facts the "Library Files" table shows.

    ``None`` means neither side carried metadata at all, which renders
    nothing -- as distinct from a side that carried metadata with missing
    fields, which keeps the per-field ``—`` placeholder.
    """
    old_meta = getattr(result, "old_metadata", None)
    new_meta = getattr(result, "new_metadata", None)
    if not old_meta and not new_meta:
        return None
    return FileMetadataTable(
        old_path=getattr(old_meta, "path", "—") if old_meta else "—",
        new_path=getattr(new_meta, "path", "—") if new_meta else "—",
        old_sha=getattr(old_meta, "sha256", "—") if old_meta else "—",
        new_sha=getattr(new_meta, "sha256", "—") if new_meta else "—",
        old_size=str(getattr(old_meta, "size_bytes", 0)) if old_meta else "—",
        new_size=str(getattr(new_meta, "size_bytes", 0)) if new_meta else "—",
    )


def compute_summary_table(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed_count: int,
    audit: object | None = None,
    *,
    entity_of: Callable[[object], str | None] | None = None,
) -> SummaryTableData:
    """Bucket the three change lists by canonical entity (ABICC-style overview).

    The rows are the catalog's declared ``ChangeEntity`` per finding
    (*entity_of*, normally the render's ``ReportFinding.entity``), labelled
    and ordered exactly as the PR comment's entity-by-operation summary
    (:data:`abicheck.report.change_summary.ENTITY_LABELS`), so the two
    cannot disagree about which row a finding belongs to. An all-zero row is
    dropped. A superseded revision classified by kind-*name* prefix
    (``func_``/``type_``/...), which filed e.g. ``needed_added`` and every
    ``python_api_*`` kind under a row other than its declared entity.
    """
    resolve_entity = entity_of or (lambda ch: entity_for_change(ch, kind_str(ch)))
    labels = [*ENTITY_LABELS, UNCLASSIFIED_LABEL]
    cats: dict[str, dict[str, int]] = {
        key: {"removed": 0, "changed": 0, "added": 0} for key in labels
    }
    for bucket_name, members in (
        ("removed", removed),
        ("changed", changed),
        ("added", added),
    ):
        for ch in members:
            entity = resolve_entity(ch)
            key = entity if entity in ENTITY_LABELS else UNCLASSIFIED_LABEL
            cats[key][bucket_name] += 1

    rows = []
    for key in labels:
        c = cats[key]
        if c["removed"] == 0 and c["changed"] == 0 and c["added"] == 0:
            continue
        rows.append(
            SummaryCategoryRow(
                label=ENTITY_LABELS.get(key, UNCLASSIFIED_LABEL),
                removed=c["removed"],
                changed=c["changed"],
                added=c["added"],
            )
        )
    return SummaryTableData(
        rows=tuple(rows),
        total_removed=len(removed),
        total_changed=len(changed),
        total_added=len(added),
        suppressed_count=suppressed_count,
        detected_total=getattr(audit, "detected_total", None),
        effective_total=getattr(audit, "effective_total", None),
        disposition_counts=getattr(audit, "counts", ()),
        policy_overlays=int(getattr(audit, "policy_overlays", 0) or 0),
        acknowledged_total=int(getattr(audit, "acknowledged_total", 0) or 0),
        # Formatted here, compute-side: the renderer decides nothing, and a
        # rule line is one already-resolved sentence either way.
        disposition_rules=tuple(
            f"{rule.rule_id or 'rule'} — {rule.reason or rule.label or 'no reason given'}"
            f" [intent: {rule.intent}"
            f"{f', expires {rule.expires}' if rule.expires else ''}"
            f"{f', from {rule.source_file}' if rule.source_file else ''}]"
            f" — {rule.matched_count} finding(s)"
            for rule in getattr(audit, "rules", ())
        ),
        not_evaluated_detectors=tuple(
            det.name for det in getattr(audit, "not_evaluated_detectors", ())
        ),
    )


def compute_nav_bar(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed_count: int,
) -> NavBarData:
    return NavBarData(
        removed=len(removed),
        changed=len(changed),
        added=len(added),
        suppressed_count=suppressed_count,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def compute_confidence(
    result: object, *, today: date | None = None
) -> ConfidenceData | None:
    """Collect the confidence/evidence/policy disclosure facts.

    ``None`` when the result carries no confidence at all, which renders no
    section. The reclassify rules are filtered through
    ``active_reclassify_rules`` here rather than on the render side: whether
    a rule is still in effect is a policy question, and disclosing an expired
    one as though it were would misstate the run (Codex review, mirroring the
    JSON ``policy_reclassify`` disclosure in ``json_sections.add_policy_overrides``
    -- the active rule set, not a per-finding "which rule fired" attribution).
    *today* (ADR-061 gap C): an envelope's own ``resolved_today``, so that
    expiry check can't drift from the envelope's own finalized verdicts
    (Codex review, fresh evidence).
    """
    conf = getattr(result, "confidence", None)
    if conf is None:
        return None
    conf_val = conf.value if hasattr(conf, "value") else str(conf)
    policy_file = getattr(result, "policy_file", None)

    overrides: tuple[tuple[str, str], ...] = ()
    if policy_file and getattr(policy_file, "overrides", None):
        overrides = tuple((k.value, v.value) for k, v in policy_file.overrides.items())
    reclassify = active_reclassify_labels(policy_file, today)
    comparability = getattr(result, "comparability_assurance", None)
    return ConfidenceData(
        confidence=conf_val,
        evidence_tiers=tuple(getattr(result, "evidence_tiers", []) or []),
        policy=getattr(result, "policy", "strict_abi") or "strict_abi",
        policy_overrides=overrides,
        policy_reclassify=reclassify,
        coverage_warnings=tuple(getattr(result, "coverage_warnings", []) or []),
        comparability_dimensions=(
            tuple(sorted(comparability.items())) if comparability else ()
        ),
    )


def compute_impact(
    result: DiffResult,
    displayed_changes: list[object] | None = None,
) -> ImpactData | None:
    """Collect the root type changes worth an impact row.

    When *displayed_changes* is given, only those changes are considered.
    Interface counts use unique ``affected_symbols``; ``caused_count`` is
    carried separately to avoid double-counting. ``None`` when no root change
    reaches anything, which renders no section.
    """
    from ..checker import _ROOT_TYPE_CHANGE_KINDS

    entries: list[ImpactEntry] = []
    changes = (
        displayed_changes
        if displayed_changes is not None
        else (getattr(result, "changes", []) or [])
    )
    for c in changes:
        kind = getattr(c, "kind", None)
        if kind and kind in _ROOT_TYPE_CHANGE_KINDS:
            affected = getattr(c, "affected_symbols", None)
            affected_count = len(affected) if affected else 0
            caused = getattr(c, "caused_count", 0)
            if affected_count > 0 or caused > 0:
                entries.append(
                    ImpactEntry(
                        symbol=getattr(c, "symbol", ""),
                        kind_value=kind.value,
                        interface_count=affected_count,
                        caused_count=caused,
                    )
                )
    if not entries:
        return None
    return ImpactData(entries=tuple(entries))


def compute_gate_card(
    result: DiffResult, severity_config: Any, *, envelope: ReportEnvelope | None = None
) -> GateCardData | None:
    """Collect the CI-gate card's facts, or ``None`` when no severity gate is
    configured.

    The gate decision itself is not computed here -- it is projected from
    :func:`abicheck.policy.gate_decision.gate_decision_for_result`, the same
    single call site ``json_sections.build_severity_json`` and
    ``sarif._severity_gate_properties`` read (ADR-061 D9): this function makes
    no policy decision of its own.

    Workstream D-S1 (vision-api-abi-evolution.md "D. Optional
    prebuilt-consumer lifecycle") reverted the earlier design where a
    supplied ``--used-by``/``consumers.required_symbols`` consumer's own scoped
    gate replaced this card: the CLI process always exits on the full-library
    gate this function reports, consumer or no consumer supplied. A
    consumer's own assessment is surfaced separately, see
    :func:`compute_scoped_verdict`.
    """
    # ADR-061 gap C: read the gate the render already resolved when this is a
    # projection of a `ReportEnvelope`; resolve one only for a direct caller.
    full_gate = project_outcome(
        result, envelope=envelope, severity_config=severity_config
    ).gate
    if full_gate is None:
        return None
    return GateCardData(
        scoped=False,
        passed=not full_gate.blocking,
        exit_code=full_gate.exit_code,
        full_gate_label="",
        blocking_categories=tuple(full_gate.blocking_categories),
    )


def compute_scoped_verdict(
    result: DiffResult, *, envelope: ReportEnvelope | None = None
) -> ScopedVerdictData | None:
    """Collect the ``--used-by``/``consumers.required_symbols`` scoped-verdict box
    (ADR-043), or ``None`` when no consumer was supplied.

    Purely informational (workstream D-S1): the verdict box above this one is
    computed from the full-library diff, and the CLI process's own exit code
    always comes from it -- this box states what a supplied consumer's own
    assessment would have concluded on its own, beside that full-library
    result, so a reader can see the two without either one overriding the
    other (mirrors the human-format banner, ``_fold_scoped_compat_into_text``).
    """
    scoped = project_outcome(result, envelope=envelope).scoped_gate
    if scoped is None:
        return None
    scheme = scoped.gate_exit_code_scheme
    return ScopedVerdictData(
        verdict_value=scoped.gate_verdict,
        exit_code=scoped.gate_exit_code,
        exit_code_scheme=None if scheme is None else str(scheme),
    )


def build_html_document(
    result: DiffResult,
    lib_name: str = "",
    old_version: str = "",
    new_version: str = "",
    old_symbol_count: int | None = None,
    title: str | None = None,
    *,
    show_only: str | None = None,
    show_impact: bool = False,
    severity_config: object | None = None,
    demangle: bool = True,
    envelope: ReportEnvelope | None = None,
) -> ReportDocument:
    """Resolve every fact the HTML report needs into one JSON-shaped
    :class:`~abicheck.report.document.ReportDocument` -- the compute half of
    ADR-061 Phase 2 item 1's HTML closure.

    ``generate_html_report`` is now a two-line wrapper over this function and
    ``report.render_html.render_html_document``: every business decision
    this module makes (show_only filtering, bucketing, compatibility
    metrics, which sections exist) lives here, never in the renderer.

    *envelope* (ADR-061 gap C), when given, is the completed
    :class:`~abicheck.report.envelope.ReportEnvelope` this render projects.
    Its shared ``report_mode="full"`` document supplies ``disposition_audit``
    verbatim (reconstructed via ``DispositionAudit.from_dict``) instead of a
    second, independently-resolved :func:`~abicheck.report.
    disposition_audit.compute_disposition_audit` over the same ledger; its
    gate drives the CI-gate card, and its one already-resolved verdict/
    category per change drives the section rows. The bucketing and row
    layout stay HTML's own presentation; what a row *says* about a finding
    is the envelope's. HTML's remaining facts (bucketing) are not shared-document
    fields -- see ADR-061's Gap C status note. A direct caller with no
    envelope (a test) keeps the independent build.
    """
    shared_disposition_audit = disposition_audit_reusing_document(
        result, severity_config, resolved_document(envelope)
    )

    verdict = (
        result.verdict.value
        if hasattr(result.verdict, "value")
        else str(result.verdict)
    )

    all_changes: list[object] = list(getattr(result, "changes", None) or [])

    # Apply show_only filter (display-only, does not affect metrics)
    if show_only:
        from ..checker import Change as _Change
        from .view import display_changes as _display_changes

        typed_changes = [c for c in all_changes if isinstance(c, _Change)]
        filtered = _display_changes(
            typed_changes,
            show_only,
            policy=result.policy,
            kind_sets=kind_sets_for_result(result)
            if isinstance(result, DiffResult)
            else None,
            policy_file=getattr(result, "policy_file", None),
            today=None if envelope is None else envelope.resolved_today,
        )
        display_changes: list[object] = list(filtered)
    else:
        display_changes = all_changes

    suppressed: list[object] = list(getattr(result, "suppressed_changes", None) or [])
    suppressed_count: int = getattr(result, "suppressed_count", len(suppressed))

    # Split display changes into buckets; duck-typed like compatibility_metrics.
    # ADR-061 Phase 2 item 4b: a real DiffResult reads each verdict from a
    # ReportFinding resolved once per change; a stub falls back as before.
    # report_findings_for needs policy/policy_file too, absent from a
    # verdict-only stub (Codex review).
    # A stub result carries no policy inputs: its bucket and entity come
    # from the kind's declared defaults, the same primitives
    # `build_report_findings` applies to a real one.
    def _bucket_of(change: object) -> str:
        return effective_bucket(change, None)

    def _entity_of(change: object) -> str | None:
        return entity_for_change(change, kind_str(change))

    # The findings the gate does not count: the only rows the size cap may
    # summarise (a stub result has none, so every row is itemised as before).
    non_gating: frozenset[int] = frozenset()
    if isinstance(result, DiffResult):
        from .finding import build_report_findings, findings_by_change_id

        # ADR-061 gap C: the envelope resolved these once for the whole
        # render (over the same `result.changes`); resolve them here only for
        # a direct caller that supplied none. Read through `findings_for`
        # rather than the bare `.findings` tuple: `display_changes` can hold
        # `suppress_dangling_correlation_notes`' own shallow `Change` copies
        # (a `--show-only` render with a dangling `correlated_change_kind`),
        # which have no entry in an id-keyed index built from `.findings`
        # alone -- `findings_for` is exactly the primitive that resolves
        # those through the same policy inputs instead of raising `KeyError`
        # (CodeRabbit review).
        if envelope is not None:
            _resolved_findings = envelope.findings_for(display_changes)  # type: ignore[arg-type]
        else:
            _resolved_findings = build_report_findings(
                display_changes,  # type: ignore[arg-type]
                policy=result.policy,
                kind_sets=kind_sets_for_result(result),
                policy_file=result.policy_file,
            )
        _findings_by_id = findings_by_change_id(_resolved_findings)
        non_gating = non_gating_change_ids(
            _resolved_findings,
            severity_config,
            accepted_break_ids(result),
        )

        def _lookup_bucket(change: object) -> str:
            return _findings_by_id[id(change)].bucket

        def _lookup_entity(change: object) -> str | None:
            return _findings_by_id[id(change)].entity

        _bucket_of = _lookup_bucket
        _entity_of = _lookup_entity
    # ADR-049 D1: a NOT_EVALUATED finding is partitioned out of the three
    # verdict buckets before they are built, the same way Markdown's own
    # "Not Evaluated (Contract)" section works -- bucketing one by its raw
    # effective verdict rendered it under the red "Changed Symbols (1)"
    # heading on a page whose banner reads NO_CHANGE (Codex review). It
    # gets its own section below instead. Empty without `--contract`.
    from ..model.contract_finding_relevance import contract_relevance_of, is_evaluated

    not_evaluated = [ch for ch in display_changes if not is_evaluated(ch)]
    scored_changes = [ch for ch in display_changes if is_evaluated(ch)]
    # Single pass, not three (one per candidate bucket, each re-resolving
    # the effective verdict) as this used to be.
    removed: list[object] = []
    added: list[object] = []
    changed: list[object] = []
    _buckets = {"removed": removed, "added": added, "changed": changed}
    for ch in scored_changes:
        _buckets[_bucket_of(ch)].append(ch)

    # Metrics always use the full (unfiltered) change list. policy/kind_sets/
    # policy_file make the Binary Compatibility % agree with the verdict
    # banner above: without them, a policy-demoted removal still counts
    # toward breaking_count by its raw kind, producing e.g. "0.0% binary
    # compatibility" on the same page whose verdict reads COMPATIBLE.
    # Duck-typed via getattr so a lightweight stub result without a real
    # DiffResult's policy machinery still renders (falls back to raw-kind
    # counting when kind_sets is None). ADR-049 D1, same reasoning one level
    # up in `report_summary.build_summary`: a NOT_EVALUATED finding is off
    # the compatibility axis, so counting it here would produce the same
    # kind of disagreement (a page whose banner reads NO_CHANGE showing
    # "0.0% binary compatibility") the policy/kind_sets note above already
    # guards against. Filtered via the shared predicate rather than
    # `policy.evaluate.evaluated_changes(result)` since a stub result need not expose it.
    _metrics_changes = [c for c in cast(list[HasKind], all_changes) if is_evaluated(c)]
    # ADR-061 gap C: an envelope has already resolved every change's verdict
    # once, at construction -- reading it here instead of calling
    # effective_verdict_for_change again keeps this percentage from moving
    # if a dated PolicyFile.reclassify rule expires between construction and
    # render (Codex review, fresh evidence: `policy`/`kind_sets`/
    # `policy_file` alone re-resolve against *today* on every call).
    metrics = compatibility_metrics(
        _metrics_changes,
        old_symbol_count,
        policy=getattr(result, "policy", None),
        kind_sets=kind_sets_for_result(result)
        if isinstance(result, DiffResult)
        else None,
        policy_file=getattr(result, "policy_file", None),
        effective_verdicts=(
            [f.verdict for f in envelope.findings_for(_metrics_changes)]  # type: ignore[arg-type]
            if envelope is not None
            else None
        ),
    )
    breaking_count = metrics.breaking_count
    bc_pct = metrics.binary_compatibility_pct
    affected_pct = metrics.affected_pct

    file_metadata = compute_file_metadata(result)
    shared: dict[str, object] = {
        "verdict": verdict,
        "lib_name": lib_name,
        "old_version": old_version,
        "new_version": new_version,
        "title": title,
        "old_symbol_count": old_symbol_count,
        "bc_pct": bc_pct,
        "affected_pct": affected_pct,
        "breaking_count": breaking_count,
        "file_metadata": (
            dataclasses.asdict(file_metadata) if file_metadata is not None else None
        ),
        # Codex review, P2 (Finding 4): the same declared-deployment-floor
        # digest the JSON/Markdown/SARIF/JUnit projections carry under
        # `env_matrix_source_sha256` -- `None` when no `deployment:` contract
        # governed this run.
        "env_matrix_source_sha256": getattr(result, "env_matrix_source_sha256", None),
    }

    # Demangle-cache prewarming is a rendering concern, not a document fact,
    # so it happens once in `render_html_document` (the function that
    # actually walks rows and calls `demangle`/`demangle_text`) rather than
    # here -- see that function's own docstring. Prewarming here too, ahead
    # of a render that always follows in the same process via
    # `generate_html_report`, would just be redundant cache-hit work.

    sections = build_sections_data(
        removed,
        changed,
        added,
        suppressed,
        suppressed_count,
        not_evaluated=not_evaluated,
        relevance_of=contract_relevance_of,
        evidence_tiers=getattr(result, "evidence_tiers", None) or (),
        use_cases=use_cases_by_finding_for(result),
        non_gating=non_gating,
    )

    empty_state: dict[str, object] | None = None
    if not sections:
        if show_only and all_changes:
            empty_state = {
                "kind": "filtered",
                "show_only": show_only,
                "all_changes_count": len(all_changes),
            }
        else:
            empty_state = {"kind": "no_changes"}

    confidence = compute_confidence(
        result, today=None if envelope is None else envelope.resolved_today
    )
    gate_card = compute_gate_card(result, severity_config, envelope=envelope)
    scoped_verdict = compute_scoped_verdict(result, envelope=envelope)
    impact = compute_impact(result, display_changes) if show_impact else None

    return ReportDocument.from_mapping(
        {
            **shared,
            "mode": "native",
            "demangle": demangle,
            "show_only": show_only,
            "show_impact": show_impact,
            "all_changes_count": len(all_changes),
            "display_changes_count": len(display_changes),
            "redundant_count": getattr(result, "redundant_count", 0),
            "redundant_reasons": redundancy_reason_text(result),
            "nav_bar": dataclasses.asdict(
                compute_nav_bar(removed, changed, added, suppressed_count)
            ),
            "summary_table": dataclasses.asdict(
                compute_summary_table(
                    removed,
                    changed,
                    added,
                    suppressed_count,
                    shared_disposition_audit,
                    entity_of=_entity_of,
                )
            ),
            "confidence": (
                dataclasses.asdict(confidence) if confidence is not None else None
            ),
            "edge_coverage": (
                None
                if (edge_coverage := compute_edge_coverage_section(result)) is None
                else dataclasses.asdict(edge_coverage)
            ),
            "gate_card": (
                dataclasses.asdict(gate_card) if gate_card is not None else None
            ),
            "scoped_verdict": (
                dataclasses.asdict(scoped_verdict)
                if scoped_verdict is not None
                else None
            ),
            "impact": dataclasses.asdict(impact) if impact is not None else None,
            "versioning_policy": compute_versioning_policy(result),
            "surface_changes": _surface_changes(
                result, display_changes, envelope, severity_config
            ),
            "use_case_impact": compute_use_case_impact(
                result, display_changes if show_only else None
            ),
            "sections": sections,
            "empty_state": empty_state,
        }
    )


def generate_html_report(
    result: DiffResult,
    lib_name: str = "",
    old_version: str = "",
    new_version: str = "",
    old_symbol_count: int | None = None,
    *,
    show_only: str | None = None,
    show_impact: bool = False,
    severity_config: object | None = None,
    demangle: bool = True,
    envelope: ReportEnvelope | None = None,
) -> str:
    """Generate a standalone HTML ABI report.

    Args:
        result: DiffResult from checker.compare().
        lib_name: Library name for the report header.
        old_version: Old library version string.
        new_version: New library version string.
        old_symbol_count: Total exported public symbol count in the old library.
            Used to compute Binary Compatibility %. If None, approximated from
            changes (legacy behaviour).
        show_only: Optional --show-only filter string (display-only).
        show_impact: If True, append an impact summary table.
        demangle: Demangle C++ symbols in the native table (see ``abbr_symbol_text``).
        severity_config: Optional severity configuration. When given, a separate "CI Gate" headline card is rendered
            alongside "Compatibility" so a configured severity gate (e.g. an
            addition promoted to ``error``) is visible even when the
            Compatibility verdict itself reads COMPATIBLE.
        envelope: The completed ``ReportEnvelope`` this render projects (ADR-061
            gap C) -- forwarded unchanged; see :func:`build_html_document`.

    Returns:
        Complete self-contained HTML document as a string.
    """
    document = build_html_document(
        result,
        lib_name=lib_name,
        old_version=old_version,
        new_version=new_version,
        old_symbol_count=old_symbol_count,
        show_only=show_only,
        show_impact=show_impact,
        severity_config=severity_config,
        demangle=demangle,
        envelope=envelope,
    )
    return render_html_document(document)


def _surface_changes(
    result: object,
    changes: list[object],
    envelope: ReportEnvelope | None,
    severity_config: object | None = None,
) -> dict[str, object] | None:
    """Additions/removals/modifications over the displayed changes (``None``
    for a duck-typed result that carries no policy to resolve them with).

    Reads the envelope's already-resolved findings when one is given, so the
    projection never resolves a verdict a second time."""
    from .surface_changes import compute_surface_changes

    if not isinstance(result, DiffResult):
        return None
    if envelope is not None:
        findings = envelope.findings_for(changes)  # type: ignore[arg-type]
        return compute_surface_changes(
            result,
            findings,  # type: ignore[arg-type]
            severity_config=envelope.severity_config,
        ).to_dict()
    return compute_surface_changes(
        result,
        changes=changes,  # type: ignore[arg-type]
        severity_config=severity_config,  # type: ignore[arg-type]
    ).to_dict()


def compute_use_case_impact(
    result: object, displayed: list[object] | None
) -> dict[str, object] | None:
    """``compare --use-cases``'s attribution, projected onto what is shown."""
    from .use_case_impact_render import use_case_impact_block

    return use_case_impact_block(result, displayed)  # type: ignore[return-value]
