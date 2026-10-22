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

"""Compute halves of the Markdown projections.

``render_markdown_document.py`` (full mode and the review digest) and
``render_markdown_alternate.py`` (``--report-mode leaf``/``root-cause``) are
pure functions of a finished :class:`~abicheck.report.document.ReportDocument`.
The functions here build that document's contents from a ``DiffResult``: they
resolve every decision a Markdown view displays -- kind sets, impact text,
disposition audit, severity groups -- through the report owners (``report/
policy_facts.py`` and the ``disposition_audit``/``surface_changes`` owners),
and return JSON-safe mappings for the renderers to format.

Nothing here formats Markdown. A projection that needs a fact this module
does not compute adds it here, not in a renderer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, replace
from typing import Any

# ADR-063 T10: static now that `reporter_markdown.py` no longer imports
# anything from this module (see this module's own docstring) -- the
# `_reporter_markdown()` importlib indirection this file used to need is
# retired; every call site below keeps the same `rm.compute_*(...)` shape
# it always had, just resolved through this module-level import.
import abicheck.reporter_markdown as _reporter_markdown_module

from .disposition_audit import (
    disposition_audit_dict_reusing_document,
)
from .document import ReportDocument
from .edge_coverage_section import (
    compute_edge_coverage_section,
)
from .envelope import ReportEnvelope, resolved_document
from .finding import ReportFinding, build_report_findings
from .not_evaluated_section import compute_not_evaluated
from .policy_facts import evidence_status_of, impact_text_for, kind_sets_for
from .review_digest_document import (
    build_review_digest_document as build_review_digest_document,
    render_review_digest_document as render_review_digest_document,
)
from .review_groups import build_review_groups
from .section_notes import (
    compute_out_of_surface_note,
    compute_redundancy_note,
    compute_rtti_note,
    compute_suppression_note,
)
from .surface_changes import (
    compute_surface_changes,
)
from .view import display_changes, resolve_scoped_gate_findings


def _reporter_markdown() -> Any:
    """``abicheck.reporter_markdown`` -- kept as a function for a minimal
    diff against every existing ``rm = _reporter_markdown()`` call site
    below (ADR-063 T10 made the underlying import static; this wrapper is
    not itself dynamic any more)."""
    return _reporter_markdown_module


def _opt_asdict(value: Any) -> dict[str, Any] | None:
    return None if value is None else asdict(value)


def _severity_counts_for(
    result: Any,
    changes: list[Any],
    envelope: ReportEnvelope | None,
) -> tuple[dict[str, int], dict[str, int]]:
    """Per-category totals for the severity table: ``(displayed, exit)``.

    *displayed* counts *changes* (the ``--show-only``-filtered set the ``Count``
    column reports); *exit* counts the set the gate scores (ADR-049 D1), which
    drives ``Exit Impact``. Both read each change's already-resolved
    :class:`~abicheck.report.finding.ReportFinding` category, so the table
    never classifies a change itself. A direct caller with no envelope resolves
    the findings here through the same primitive the envelope uses.
    """
    from ..policy.severity import gate_eligible_changes

    def findings_for(cs: Sequence[Any]) -> Sequence[ReportFinding]:
        if envelope is not None:
            return envelope.findings_for(list(cs))
        return build_report_findings(
            list(cs),
            policy=result.policy,
            kind_sets=kind_sets_for(result),
            policy_file=result.policy_file,
        )

    def tally(findings: Sequence[ReportFinding]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for finding in findings:
            key = finding.category.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    return (
        tally(findings_for(changes)),
        tally(findings_for(gate_eligible_changes(list(result.changes)))),
    )


def _severity_summary_mapping(
    result: Any,
    changes: list[Any],
    severity_config: Any,
    envelope: ReportEnvelope | None,
    *,
    scoped_gate: bool,
) -> dict[str, Any] | None:
    """The severity table as a JSON-safe mapping, or ``None`` with no severity config.

    *scoped_gate* is the root-cause view's opt-in to the ``--used-by`` /
    ``--required-symbol`` gate numbers; the full view never reads them.
    """
    if severity_config is None:
        return None
    displayed, exit_counts = _severity_counts_for(result, changes, envelope)
    return asdict(
        _reporter_markdown().compute_severity_summary(
            severity_config,
            counts=displayed,
            exit_counts=exit_counts,
            scoped_counts=(
                getattr(result, "scoped_severity_counts", None) if scoped_gate else None
            ),
            scoped_blocking_categories=(
                getattr(result, "scoped_blocking_categories", None)
                if scoped_gate
                else None
            ),
        )
    )


def _resolve_displayed_changes(
    result: Any, show_only: str | None, *, today: Any = None
) -> tuple[list[Any], dict[str, Any] | None]:
    """The changes this render actually displays, plus the ``--show-only``
    filter note describing it when one was applied (``None`` otherwise).

    ADR-063 T10: computed once here rather than independently re-derived by
    both full mode (:func:`build_markdown_document` below) and leaf/
    root-cause mode (``render_markdown_alternate.py``'s own
    ``_view_preamble_mapping``), which used to each run their own copy of
    the identical ``apply_show_only`` -> ``_suppress_dangling_correlation_
    notes`` pipeline and independently re-derive the same filter-note shape
    from it. *today*: an envelope's ``resolved_today`` (ADR-061 gap C,
    Codex, fresh).
    """
    changes = list(result.changes)
    show_only_note: dict[str, Any] | None = None
    if show_only:
        changes = display_changes(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=kind_sets_for(result),
            policy_file=result.policy_file,
            today=today,
        )
        show_only_note = {
            "show_only": show_only,
            "shown": len(changes),
            "total": len(result.changes),
        }
    return changes, show_only_note


def _change_row(
    c: Any,
    evidence_tiers: Sequence[str] = (),
    use_cases: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """A JSON-safe row for one ``Change``, carrying every field
    ``_render_change_row``/``_render_change_row_oneline``/
    ``_render_leaf_type_change_row`` need -- including ``impact_for(kind)``,
    resolved here (compute side) rather than by the renderer, mirroring
    HTML's ``ChangeRow``/``compute_full_change_rows`` fix for the identical
    "registry lookup on the render side" issue. ``symbol`` is only read by
    the leaf-mode row renderer (``_format_leaf_type_change``'s ``###
    {symbol} — {desc}`` heading); every other caller ignores it.

    *evidence_tiers* (``DiffResult.evidence_tiers``), when given, lets
    ``impact_for`` append its evidence caveat for an ``UNATTRIBUTED``
    finding here too -- Codex review, fresh evidence: this Markdown path
    kept the unconditional impact text even after the JSON reporter
    gained the caveat (Finding C(ii)). Omitting it (the default) keeps
    every pre-existing caller -- which has no evidence_tiers to hand --
    byte-identical to before this parameter existed.
    """
    from ..compare.finding_identity import report_finding_id

    kind = getattr(c, "kind", None)
    relevance = getattr(c, "contract_relevance", None)
    assurance = getattr(c, "contract_assurance", None)
    affected = getattr(c, "affected_symbols", None)
    cse = getattr(c, "cross_source_evolution", None)
    evidence_status = evidence_status_of(c, evidence_tiers) if kind else None
    return {
        "kind": kind.value if kind else "",
        "symbol": getattr(c, "symbol", None),
        "description": getattr(c, "description", "") or "",
        "old_value": getattr(c, "old_value", None),
        "new_value": getattr(c, "new_value", None),
        "source_location": getattr(c, "source_location", None),
        "affected_symbols": list(affected) if affected else [],
        "caused_count": getattr(c, "caused_count", 0) or 0,
        "impact": impact_text_for(kind, evidence_status) if kind else None,
        "contract_relevance": getattr(relevance, "value", None),
        "contract_reason_code": getattr(c, "contract_reason_code", None),
        "contract_assurance": getattr(assurance, "value", None),
        "correlated_change_kind": getattr(c, "correlated_change_kind", None),
        # ADR-068 finding A: this row type feeds the default full-mode
        # Markdown report (`to_markdown`'s primary view) -- until this fix
        # it never carried the OLD->NEW evolution state
        # `workflows.cross_source_hygiene` stamps on every hygiene
        # finding, so a `persistent` (pre-existing) finding rendered
        # indistinguishably from newly `introduced` drift.
        "cross_source_evolution": getattr(cse, "value", None),
        # `compare --use-cases`: the per-finding inverse of the
        # `use_case_impact` block (`UseCaseImpact.use_cases_by_finding`),
        # present only when the attribution ran so every other report's
        # rows are unchanged.
        **(
            {"affected_use_cases": list(use_cases.get(report_finding_id(c), ()))}
            if use_cases is not None
            else {}
        ),
    }


def _not_evaluated_mapping(
    section: Any, evidence_tiers: Sequence[str] = ()
) -> dict[str, Any] | None:
    """JSON-safe fold of a ``NotEvaluatedSection`` (or ``None``), shared by
    every view that discloses ADR-049 D1's unscored findings (full, leaf,
    root-cause)."""
    if section is None:
        return None
    return {
        "entries": [
            {
                "row": _change_row(e.change, evidence_tiers),
                "label": e.label,
                "suffix": e.suffix,
            }
            for e in section.entries
        ]
    }


def build_markdown_document(
    result: Any,
    *,
    show_only: str | None = None,
    show_impact: bool = False,
    severity_config: Any = None,
    show_recommendation: bool = False,
    demangle: bool = False,
    envelope: ReportEnvelope | None = None,
) -> ReportDocument:
    """The full-mode (``to_markdown`` default view) report as a
    ``ReportDocument``. See this module's own docstring for scope.

    *envelope* (ADR-061 gap C), when given, is the completed
    ``ReportEnvelope`` this render projects. Its shared
    ``report_mode="full"`` document (``--stat`` and the ``root-cause``
    alternate view stay their own documents, see this module's docstring)
    supplies ``disposition_audit`` verbatim instead of a second
    ``compute_disposition_audit`` over the same ledger, and its per-finding
    verdict/category set drives the ``surface_changes`` section. A direct
    caller with no envelope (a test) builds both itself. The severity groups, headline table and
    per-change rows this function assembles stay Markdown's own presentation
    -- an arrangement of already-decided findings, not a second opinion about
    them.
    """
    rm = _reporter_markdown()
    shared_document = resolved_document(envelope)
    verdict = result.verdict
    emoji = rm._VERDICT_EMOJI[verdict]
    label = rm._VERDICT_LABEL[verdict]

    old_meta = getattr(result, "old_metadata", None)
    new_meta = getattr(result, "new_metadata", None)

    resolved_today = None if envelope is None else envelope.resolved_today
    changes, show_only_note = _resolve_displayed_changes(
        result, show_only, today=resolved_today
    )

    from ..report_model import ReportModel

    # ADR-061 gap C: both the severity groups (filtered `changes`) and the
    # headline totals (the full, unfiltered `result.changes`) reuse the
    # envelope's own finalized verdicts, rather than `ReportModel.classify`/
    # `compute_headline_table` each independently calling
    # `policy.evaluate.effective_verdict` fresh -- which could disagree
    # with the envelope once a dated `PolicyFile.reclassify` rule expires
    # between construction and render (Codex review, fresh evidence).
    model = ReportModel.from_result(
        result,
        changes=changes,
        effective_verdicts=(
            [f.verdict for f in envelope.findings_for(changes)]
            if envelope is not None
            else None
        ),
    )
    breaking, source_breaks, risk, compatible = (
        model.breaking,
        model.source_breaks,
        model.risk,
        model.compatible,
    )

    # `compare --use-cases`: the per-finding inverse of the attached
    # `UseCaseImpact` (reached through the result, since `report` may not
    # import the unclassified `impact` package); `None` when none ran.
    use_cases = rm.use_cases_by_finding_for(result)
    severity_data = rm.compute_severity_sections(
        breaking, source_breaks, risk, compatible, severity_config=severity_config
    )
    not_evaluated = compute_not_evaluated(model.not_evaluated)

    # The headline's own totals are deliberately over the *full*, unfiltered
    # `result.changes` (a different population than the severity groups
    # above), but every one of those changes is already in the envelope's
    # own `findings` -- reused here instead of letting `compute_headline_
    # table` independently call `policy.evaluate.effective_verdict`
    # fresh a second time, which could disagree with the envelope once a
    # dated `PolicyFile.reclassify` rule expires (Codex review, evidence).
    headline_table = rm.compute_headline_table(result, emoji, label)
    if envelope is not None:
        ev = envelope.findings
        overrides = {id(c): f.verdict for c, f in zip(result.changes, ev)}
        hb, hsb, hr, hc = (
            len(b)
            for b in ReportModel.classify(
                list(result.changes), result, verdict_overrides=overrides
            )
        )
        headline_table = replace(
            headline_table, breaking=hb, source_breaks=hsb, risk=hr, compatible=hc
        )

    d: dict[str, object] = {
        "report_mode": "full",
        "demangle": demangle,
        "headline": asdict(headline_table),
        "rtti_note": _opt_asdict(compute_rtti_note(breaking)),
        "confidence": _opt_asdict(rm.compute_confidence_section(result)),
        "edge_coverage": _opt_asdict(compute_edge_coverage_section(result)),
        "contract_conflicts": _opt_asdict(
            rm.compute_contract_conflicts_section(result)
        ),
        "policy": asdict(rm.compute_policy_section(result, today=resolved_today)),
        "recommendation": (
            asdict(rm.compute_recommendation_section(result))
            if show_recommendation
            else None
        ),
        "severity_summary": _severity_summary_mapping(
            result, changes, severity_config, envelope, scoped_gate=False
        ),
        "show_only_note": show_only_note,
        "library_files": (
            asdict(rm.compute_library_files(old_meta, new_meta))
            if (old_meta or new_meta)
            else None
        ),
        "severity_groups": [
            {
                "heading": g.heading,
                "oneline": g.oneline,
                "note_lines": list(g.note_lines),
                "rows": [
                    _change_row(c, result.evidence_tiers, use_cases) for c in g.changes
                ],
                # The kinds rolled up instead of itemised; a rolled-up finding
                # is in exactly one of `rows` or here, never dropped.
                "rollups": [asdict(r) for r in g.rollups],
            }
            for g in severity_data.groups
        ],
        "not_evaluated": _not_evaluated_mapping(not_evaluated, result.evidence_tiers),
        "environment_drift": _opt_asdict(rm.compute_environment_drift(changes)),
        "empty_message": (
            None
            if changes
            else (
                "_No changes match the current filter._"
                if (show_only and result.changes)
                else "_No ABI changes detected._"
            )
        ),
        # ADR-067 D3: the counts belong in every projection, and the three
        # Markdown modes reach their renderer through this document, so the
        # block is a document field rather than something a renderer derives.
        # ADR-061 gap C: reused from the envelope's shared document when the
        # caller passed one (see this function's own docstring) rather than
        # independently resolved here.
        "disposition_audit": disposition_audit_dict_reusing_document(
            result, severity_config, shared_document
        ),
        "pattern_modulations": list(getattr(result, "pattern_modulations", ()) or ()),
        # Workstream G S1's "what changed / review actions" section: same
        # already-resolved findings the severity groups above use, projected
        # as additions/removals/modifications so a compatible run still
        # itemizes what it added.
        # ADR-061 gap C: the envelope already resolved a verdict/category per
        # change; grouping them is presentation, resolving them again is not.
        "surface_changes": compute_surface_changes(
            result,
            None if envelope is None else envelope.findings_for(changes),
            changes=changes,
        ).to_dict(),
        "review_groups": (
            shared_document.to_mapping().get("review_groups", [])
            if shared_document is not None and not show_only
            else [
                group.to_dict()
                for group in build_review_groups(
                    build_report_findings(
                        changes,
                        policy=result.policy,
                        kind_sets=kind_sets_for(result),
                        policy_file=result.policy_file,
                    )
                    if envelope is None
                    else envelope.findings_for(changes)
                )
            ]
        ),
        "redundancy_note": _opt_asdict(compute_redundancy_note(result)),
        "suppression_note": _opt_asdict(compute_suppression_note(result)),
        "out_of_surface_note": _opt_asdict(compute_out_of_surface_note(result)),
        "show_impact": show_impact,
        "impact_table": (
            _opt_asdict(
                rm.compute_impact_table(
                    result,
                    displayed_changes=changes,
                    findings=None
                    if envelope is None
                    else envelope.findings_for(changes),
                )
            )
            if show_impact
            else None
        ),
        "env_matrix_source_sha256": result.env_matrix_source_sha256,
    }
    return ReportDocument.from_mapping(d)


def _view_preamble_mapping(
    result: Any,
    view_label: str,
    *,
    show_only: str | None,
    show_recommendation: bool,
    severity_config: Any = None,
    envelope: ReportEnvelope | None = None,
) -> tuple[dict[str, Any], list[Any]]:
    """JSON-safe fields for the opening block ``--report-mode leaf``/
    ``root-cause`` share (title/verdict table, coverage-warning banner,
    optional recommendation section, ``--show-only`` filter note) plus the
    (possibly filtered) changes to render -- the compute half of
    ``reporter_markdown._view_preamble``'s split. See
    :func:`_render_view_preamble` for the render half."""
    rm = _reporter_markdown()

    changes, show_only_note = _resolve_displayed_changes(
        result,
        show_only,
        today=None if envelope is None else envelope.resolved_today,
    )

    d: dict[str, Any] = {
        "library": result.library,
        "view_label": view_label,
        "old_version": result.old_version,
        "new_version": result.new_version,
        "verdict_emoji": rm._VERDICT_EMOJI[result.verdict],
        "verdict_label": rm._VERDICT_LABEL[result.verdict],
        "coverage_warnings": (
            list(result.coverage_warnings) if result.coverage_warnings else []
        ),
        "recommendation": (
            asdict(rm.compute_recommendation_section(result))
            if show_recommendation
            else None
        ),
        "show_only_note": show_only_note,
        # ADR-067 D3: both alternate modes carry the same counts the full
        # view and the digest do -- computed here, in the one preamble the
        # two share, rather than twice at their own call sites.
        "disposition_audit": disposition_audit_dict_reusing_document(
            result, severity_config, resolved_document(envelope)
        ),
        # Same "what changed / review actions" section the full view and
        # the digest carry (workstream G S1) -- one preamble, both alternate
        # modes.
        "surface_changes": compute_surface_changes(result, changes=changes).to_dict(),
    }
    return d, changes


def build_root_cause_document(
    result: Any,
    *,
    show_only: str | None = None,
    show_recommendation: bool = False,
    show_impact: bool = False,
    severity_config: Any = None,
    contract_evaluation: bool = False,
    envelope: ReportEnvelope | None = None,
) -> ReportDocument:
    """``--report-mode root-cause`` (G29 Phase 3 slice 4, ADR-052) as a
    ``ReportDocument``. See this module's own docstring for scope --
    ``compute_root_cause_section``'s own ``RootCauseSectionData`` is already
    JSON-safe (every finding is pre-formatted to a plain string at compute
    time), so this fold needs no new row type of its own."""
    rm = _reporter_markdown()
    preamble, changes = _view_preamble_mapping(
        result,
        "root-cause view",
        show_only=show_only,
        show_recommendation=show_recommendation,
        severity_config=severity_config,
        envelope=envelope,
    )

    # G29 Phase 3 slice 3 follow-up: merge --used-by/--required-symbol
    # scoped-only findings into the same root-cause groups (see
    # reporter_markdown._to_markdown_root_cause's own note) -- resolved
    # before the severity table so a scoped run whose only gating issue is
    # one of these can pass the scoped counts below.
    scoped_only, missing_labels, blocks, missing_kind = resolve_scoped_gate_findings(
        result, severity_config, show_only
    )

    root_cause_section = rm.compute_root_cause_section(
        changes,
        scoped_only,
        missing_labels,
        blocks,
        missing_kind,
        contract_evaluation=contract_evaluation,
        evidence_tiers=result.evidence_tiers,
    )
    has_root_cause_entries = root_cause_section is not None

    d: dict[str, Any] = {
        **preamble,
        "report_mode": "root-cause",
        # This builder assembles its own document rather than reusing
        # `build_report_document`'s, so a field carried there is not carried
        # here for free -- which is why `--view root-cause` rendered an
        # accepted result naming neither the rule that demoted its findings
        # nor the reason (Codex review, PR #1284). Same key and same rows as
        # the full document, so the two projections cannot disagree.
        "pattern_modulations": list(getattr(result, "pattern_modulations", ()) or ()),
        "severity_summary": _severity_summary_mapping(
            result, changes, severity_config, envelope, scoped_gate=True
        ),
        "root_cause": (
            asdict(root_cause_section) if root_cause_section is not None else None
        ),
        # Codex review (see reporter_markdown._to_markdown_root_cause's own
        # note): a scoped-only change or missing-contract label can be the
        # *only* displayed finding, so gating the empty message purely on
        # `changes` would produce a contradictory report.
        "empty_message": (
            None
            if (changes or has_root_cause_entries)
            else (
                "_No changes match the current filter._"
                if (show_only and result.changes)
                else "_No ABI changes detected._"
            )
        ),
        "redundancy_note": _opt_asdict(compute_redundancy_note(result)),
        "suppression_note": _opt_asdict(compute_suppression_note(result)),
        "out_of_surface_note": _opt_asdict(compute_out_of_surface_note(result)),
        "show_impact": show_impact,
        "impact_table": (
            _opt_asdict(rm.compute_impact_table(result, displayed_changes=changes))
            if show_impact
            else None
        ),
    }
    return ReportDocument.from_mapping(d)
