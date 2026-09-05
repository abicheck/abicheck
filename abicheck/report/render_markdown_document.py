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

"""Markdown's whole-document ``ReportDocument`` construction and projection:
review digest and the full-mode (``to_markdown``'s default view) report.

ADR-061 Phase 2 item 1: Markdown is the prose format whose renderer did not
used to fully consume one canonical, JSON-shaped ``ReportDocument`` -- unlike
JSON, SARIF, JUnit, ``--stat`` (``reporter_markdown.to_stat`` already builds
one via ``report/render_text.py``'s ``render_stat_document``), and HTML
(``html_report.build_html_document`` + ``report/render_html_document.py``).
This module (plus its sibling, ``render_markdown_alternate.py``, which owns
``--report-mode leaf``/``root-cause`` -- split out once the combined module
passed the architecture check's new-file 800-line ceiling, the same reason
``render_html.py``/``render_html_document.py`` are two files) is Markdown's
counterpart to that HTML split. Two views land here:

- ``--format review``'s digest (``build_review_digest_document``/
  ``render_review_digest_document``) -- already had the cleanest compute/
  render pair in ``reporter_markdown.py`` (``compute_review_digest`` ->
  ``render_markdown.render_review_digest``) of anything in that module, and
  every field is already JSON-safe, so the document wrap is a direct fold.
- The full-mode report (``build_markdown_document``/
  ``render_markdown_document``, ``report_mode == "full"``) -- ``to_markdown``'s
  default view.

Both ``build_*`` functions fold a dataclass (or several) into a JSON-shaped
mapping and wrap it as a ``ReportDocument``; the matching ``render_*``
reconstructs the dataclass(es) from the document's own mapping (a document
round trip turns every dataclass into a plain mapping and tuple into a list,
same as HTML's ``_*_from_mapping`` helpers) and calls the existing,
unmodified ``render_markdown.py`` render functions -- no rendering logic
duplicated here, with one exception: the full-mode report's per-``Change``
sections (severity-grouped changes, "Not Evaluated") need a JSON-safe row
shape ``Change`` itself isn't (mirroring HTML's ``ChangeRow``, which solved
the identical problem for HTML's own per-row table data) --
``_change_row``/``_row_contract_tag``/``_render_change_row``/
``_render_change_row_oneline`` below are that row type's compute/render
halves, kept local to this module rather than added to ``render_markdown.py``
(which has essentially no headroom left under its own 800-line cap) since
``_format_change_md``/``_format_change_md_oneline``/``_format_leaf_type_change``
there keep serving their other existing caller (the scoped-gate text append
in ``cli_compare_fold.py``) unconverted and unchanged. ``_not_evaluated_
mapping``/``_render_not_evaluated_lines`` are the identical split for the
"## Not Evaluated (Contract)" section every view (this module's full mode
and ``render_markdown_alternate.py``'s leaf/root-cause modes alike) shares
verbatim -- ``render_markdown_alternate.py`` imports these row/section
helpers from here rather than duplicating them (a same-package,
one-directional dependency, not a cycle).

Lives in this package, not the near-full flat ``reporter_markdown.py``
(``architecture/debt.yaml``'s ``no_growth`` baseline there had ~18 lines of
headroom before ``to_markdown``'s own full-mode body collapsed into a
five-line call to this module, freeing most of it back) or a new flat
``abicheck/reporter_*.py`` sibling (``architecture/modules.yaml``'s
``frozen_root_families`` closes that flat namespace to new members, same as
``report/scoped_gate.py``). Reaches ``reporter_markdown.py`` (a
``layers.report.legacy_paths`` member, same layer as this file) via
``importlib`` rather than a static import, mirroring ``report/
scoped_gate.py``'s own ``_reporter()`` helper: ``reporter_markdown.py``
imports this module's entry points via function-local (not module-level)
imports (``to_markdown``/``render_review_digest`` call the matching
``build_*``/``render_*`` pair above), so a static, module-level import back
here would close a real cycle ``check_ai_readiness.py``'s
``import-cycle-growth`` gate flags.

Every view's byte-for-byte output is unchanged by this split --
``tests/test_golden_output.py``/``tests/test_golden_review_digest.py`` pin
the exact text every ``build_*``/``render_*`` pair here must keep
reproducing.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from .disposition_audit import (
    DispositionAudit,
    compute_disposition_audit,
    render_disposition_audit_section,
)
from .document import ReportDocument
from .render_markdown import (
    ConfidenceSection,
    EnvironmentDriftEntry,
    EnvironmentDriftSection,
    HeadlineTable,
    ImpactedSymbol,
    ImpactRootEntry,
    ImpactTable,
    LibraryFilesSection,
    OutOfSurfaceNote,
    PolicySection,
    RecommendationSection,
    RedundancyNote,
    ReviewDigest,
    RttiNote,
    SeverityRow,
    SeveritySummary,
    SuppressedEntry,
    SuppressionNote,
    render_confidence_section,
    render_environment_drift_section,
    render_footer,
    render_headline_table,
    render_impact_table,
    render_library_files_section,
    render_out_of_surface_note,
    render_policy_section,
    render_recommendation_section,
    render_redundancy_note,
    render_review_digest,
    render_rtti_note,
    render_severity_summary,
    render_suppression_note,
)


def _reporter_markdown() -> Any:
    """``abicheck.reporter_markdown``, resolved via ``importlib``. See this
    module's own docstring for why -- mirrors ``report/scoped_gate.py``'s
    identical ``_reporter()`` helper."""
    import importlib

    return importlib.import_module("..reporter_markdown", __package__)


def _opt_asdict(value: Any) -> dict[str, Any] | None:
    return None if value is None else asdict(value)


def build_review_digest_document(
    result: Any, *, severity_config: Any = None
) -> ReportDocument:
    """The ``--format review`` digest as a ``ReportDocument``.

    *severity_config* is forwarded to :func:`~abicheck.reporter_markdown.
    compute_review_digest` unchanged -- see that function's own docstring
    for what it drives (the merge-effect phrase).
    """
    digest = _reporter_markdown().compute_review_digest(
        result, severity_config=severity_config
    )
    d: dict[str, object] = {
        "library": digest.library,
        "old_version": digest.old_version,
        "new_version": digest.new_version,
        "verdict_emoji": digest.verdict_emoji,
        "verdict_label": digest.verdict_label,
        "effect": digest.effect,
        "manual_review_banner": digest.manual_review_banner,
        "coverage_warnings": list(digest.coverage_warnings),
        "additions_label": digest.additions_label,
        "breaking_count": digest.breaking_count,
        "source_breaks_count": digest.source_breaks_count,
        "risk_count": digest.risk_count,
        "additions_count": digest.additions_count,
        "scoped": digest.scoped,
        "out_of_surface_count": digest.out_of_surface_count,
        "bump_value": digest.bump_value,
        "soname_value": digest.soname_value,
        "impacted": [{"symbol": s.symbol, "kind": s.kind} for s in digest.impacted],
        "disposition_audit": (
            None
            if digest.disposition_audit is None
            else digest.disposition_audit.to_dict()
        ),
    }
    return ReportDocument.from_mapping(d)


def _review_digest_from_mapping(d: Mapping[str, Any]) -> ReviewDigest:
    impacted = tuple(ImpactedSymbol(**item) for item in d["impacted"])
    return ReviewDigest(
        library=d["library"],
        old_version=d["old_version"],
        new_version=d["new_version"],
        verdict_emoji=d["verdict_emoji"],
        verdict_label=d["verdict_label"],
        effect=d["effect"],
        manual_review_banner=d["manual_review_banner"],
        coverage_warnings=tuple(d["coverage_warnings"]),
        additions_label=d["additions_label"],
        breaking_count=d["breaking_count"],
        source_breaks_count=d["source_breaks_count"],
        risk_count=d["risk_count"],
        additions_count=d["additions_count"],
        scoped=d["scoped"],
        out_of_surface_count=d["out_of_surface_count"],
        bump_value=d["bump_value"],
        soname_value=d["soname_value"],
        impacted=impacted,
        disposition_audit=(
            None
            if d.get("disposition_audit") is None
            else DispositionAudit.from_dict(d["disposition_audit"])
        ),
    )


def render_review_digest_document(doc: ReportDocument) -> str:
    """Project a review-digest ``ReportDocument`` to its Markdown text."""
    return render_review_digest(_review_digest_from_mapping(doc.to_mapping()))


# ---------------------------------------------------------------------------
# Full-mode report (to_markdown's default view)
# ---------------------------------------------------------------------------


def _change_row(c: Any) -> dict[str, Any]:
    """A JSON-safe row for one ``Change``, carrying every field
    ``_render_change_row``/``_render_change_row_oneline``/
    ``_render_leaf_type_change_row`` need -- including ``impact_for(kind)``,
    resolved here (compute side) rather than by the renderer, mirroring
    HTML's ``ChangeRow``/``compute_full_change_rows`` fix for the identical
    "registry lookup on the render side" issue. ``symbol`` is only read by
    the leaf-mode row renderer (``_format_leaf_type_change``'s ``###
    {symbol} — {desc}`` heading); every other caller ignores it.
    """
    from ..checker_policy import impact_for

    kind = getattr(c, "kind", None)
    relevance = getattr(c, "contract_relevance", None)
    assurance = getattr(c, "contract_assurance", None)
    affected = getattr(c, "affected_symbols", None)
    return {
        "kind": kind.value if kind else "",
        "symbol": getattr(c, "symbol", None),
        "description": getattr(c, "description", "") or "",
        "old_value": getattr(c, "old_value", None),
        "new_value": getattr(c, "new_value", None),
        "source_location": getattr(c, "source_location", None),
        "affected_symbols": list(affected) if affected else [],
        "caused_count": getattr(c, "caused_count", 0) or 0,
        "impact": impact_for(kind) if kind else None,
        "contract_relevance": getattr(relevance, "value", None),
        "contract_reason_code": getattr(c, "contract_reason_code", None),
        "contract_assurance": getattr(assurance, "value", None),
        "correlated_change_kind": getattr(c, "correlated_change_kind", None),
    }


def _row_contract_tag(row: Mapping[str, Any]) -> str | None:
    """The ``<relevance> (<reason_code>), assurance: <level>`` tag for one
    already-JSON-safe ``_change_row``, or ``None`` when the row was never
    contract-stamped. Shared by every row-based renderer below that shows a
    contract decision (mirrors ``render_markdown._contract_decision_text``,
    the ``Change``-based equivalent)."""
    if row.get("contract_relevance") is None:
        return None
    tag = str(row["contract_relevance"])
    if row.get("contract_reason_code"):
        tag += f" ({row['contract_reason_code']})"
    if row.get("contract_assurance") is not None:
        tag += f", assurance: {row['contract_assurance']}"
    return tag


def _render_change_row_oneline(row: Mapping[str, Any]) -> str:
    line = f"- **{row['kind']}**: {row['description']}"
    correlated = row.get("correlated_change_kind")
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"
    return line


def _render_change_row(row: Mapping[str, Any]) -> str:
    old_val, new_val = row.get("old_value"), row.get("new_value")
    old_new = ""
    if old_val is not None and new_val is not None:
        old_new = f" (`{old_val}` → `{new_val}`)"
    elif old_val is not None:
        old_new = f" (`{old_val}`)"
    elif new_val is not None:
        old_new = f" (`{new_val}`)"
    line = f"- **{row['kind']}**: {row['description']}{old_new}"
    loc = row.get("source_location")
    if loc:
        line += f" — `{loc}`"
    impact = row.get("impact")
    if impact:
        line += f"\n  > {impact}"
    caused_count = row.get("caused_count") or 0
    if caused_count > 0:
        line += f"\n  > {caused_count} derived change(s) collapsed"
    affected = row.get("affected_symbols")
    if affected:
        names = ", ".join(f"`{s}`" for s in affected[:5])
        suffix = f" (+{len(affected) - 5} more)" if len(affected) > 5 else ""
        line += f"\n  > Affected symbols: {names}{suffix}"
    tag = _row_contract_tag(row)
    if tag is not None:
        line += f"\n  > Contract: {tag}"
    correlated = row.get("correlated_change_kind")
    if correlated:
        line += f"\n  > See also: `{correlated}` finding for the same symbol"
    return line


def _not_evaluated_mapping(section: Any) -> dict[str, Any] | None:
    """JSON-safe fold of a ``NotEvaluatedSection`` (or ``None``), shared by
    every view that discloses ADR-049 D1's unscored findings (full, leaf,
    root-cause)."""
    if section is None:
        return None
    return {
        "entries": [
            {"row": _change_row(e.change), "label": e.label, "suffix": e.suffix}
            for e in section.entries
        ]
    }


def _render_not_evaluated_lines(d: Mapping[str, Any] | None) -> list[str]:
    """Render a ``_not_evaluated_mapping()`` result -- the "## Not Evaluated
    (Contract)" section every view shares verbatim
    (``render_markdown.render_not_evaluated_section``'s own heading/preamble
    text, kept identical here since that function takes a real
    ``NotEvaluatedSection`` of ``Change`` objects rather than JSON-safe rows).
    """
    if d is None:
        return []
    lines: list[str] = [
        "## 🔍 Not Evaluated (Contract)",
        "",
        "> These findings were detected but **not scored** by compatibility",
        "> policy: each is either proven outside the declared contract or",
        "> unresolved for want of evidence (ADR-049). They contribute nothing",
        "> to the verdict or the gate. Incomplete evidence is reported",
        "> separately on the contract-coverage axis, which has its own exit",
        "> code — uncertainty is never silently treated as compatible.",
        "",
    ]
    for entry in d["entries"]:
        lines.append(_render_change_row_oneline(entry["row"]))
        lines.append(f"  > Contract: {entry['label']}{entry['suffix']}")
    lines.append("")
    return lines


def build_markdown_document(
    result: Any,
    *,
    show_only: str | None = None,
    show_impact: bool = False,
    severity_config: Any = None,
    show_recommendation: bool = False,
    demangle: bool = False,
) -> ReportDocument:
    """The full-mode (``to_markdown`` default view) report as a
    ``ReportDocument``. See this module's own docstring for scope."""
    rm = _reporter_markdown()
    verdict = result.verdict
    emoji = rm._VERDICT_EMOJI[verdict]
    label = rm._VERDICT_LABEL[verdict]

    old_meta = getattr(result, "old_metadata", None)
    new_meta = getattr(result, "new_metadata", None)

    changes = list(result.changes)
    if show_only:
        changes = rm.apply_show_only(
            changes,
            show_only,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
        changes = rm._suppress_dangling_correlation_notes(changes)

    from ..report_model import ReportModel

    model = ReportModel.from_result(result, changes=changes)
    breaking, source_breaks, risk, compatible = (
        model.breaking,
        model.source_breaks,
        model.risk,
        model.compatible,
    )

    severity_data = rm.compute_severity_sections(
        breaking, source_breaks, risk, compatible, severity_config=severity_config
    )
    not_evaluated = rm.compute_not_evaluated(model.not_evaluated)

    d: dict[str, object] = {
        "report_mode": "full",
        "demangle": demangle,
        "headline": asdict(rm.compute_headline_table(result, emoji, label)),
        "rtti_note": _opt_asdict(rm.compute_rtti_note(breaking)),
        "confidence": _opt_asdict(rm.compute_confidence_section(result)),
        "policy": asdict(rm.compute_policy_section(result)),
        "recommendation": (
            asdict(rm.compute_recommendation_section(result))
            if show_recommendation
            else None
        ),
        "severity_summary": (
            asdict(
                rm.compute_severity_summary(
                    changes,
                    severity_config,
                    all_changes=list(result.changes),
                    policy=result.policy,
                    kind_sets=result._effective_kind_sets(),
                    policy_file=result.policy_file,
                )
            )
            if severity_config is not None
            else None
        ),
        "show_only_note": (
            {
                "show_only": show_only,
                "shown": len(changes),
                "total": len(result.changes),
            }
            if show_only
            else None
        ),
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
                "rows": [_change_row(c) for c in g.changes],
            }
            for g in severity_data.groups
        ],
        "not_evaluated": _not_evaluated_mapping(not_evaluated),
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
        "disposition_audit": compute_disposition_audit(
            result, severity_config
        ).to_dict(),
        "redundancy_note": _opt_asdict(rm.compute_redundancy_note(result)),
        "suppression_note": _opt_asdict(rm.compute_suppression_note(result)),
        "out_of_surface_note": _opt_asdict(rm.compute_out_of_surface_note(result)),
        "show_impact": show_impact,
        "impact_table": (
            _opt_asdict(rm.compute_impact_table(result, displayed_changes=changes))
            if show_impact
            else None
        ),
    }
    return ReportDocument.from_mapping(d)


def _render_disposition_audit_from_mapping(d: Any) -> list[str]:
    """Rebuild and render the audit section from a document mapping.

    Shared by all three Markdown modes; ``None``/absent renders nothing, so a
    document built before this field existed still projects cleanly.
    """
    if not isinstance(d, Mapping):
        return []
    return render_disposition_audit_section(DispositionAudit.from_dict(d))


def _suppression_note_from_mapping(
    d: Mapping[str, Any] | None,
) -> SuppressionNote | None:
    if d is None:
        return None
    return SuppressionNote(
        suppressed_count=d["suppressed_count"],
        entries=tuple(SuppressedEntry(**e) for e in d["entries"]),
    )


def _impact_table_from_mapping(d: Mapping[str, Any] | None) -> ImpactTable | None:
    if d is None:
        return None
    return ImpactTable(
        root_entries=tuple(ImpactRootEntry(**e) for e in d["root_entries"]),
        direct_removals=d["direct_removals"],
    )


def _environment_drift_from_mapping(
    d: Mapping[str, Any] | None,
) -> EnvironmentDriftSection | None:
    if d is None:
        return None
    return EnvironmentDriftSection(
        entries=tuple(EnvironmentDriftEntry(**e) for e in d["entries"])
    )


def render_markdown_document(doc: ReportDocument) -> str:
    """Project a full-mode ``ReportDocument`` to its Markdown text."""
    d: dict[str, Any] = doc.to_mapping()
    lines: list[str] = render_headline_table(HeadlineTable(**d["headline"]))
    lines += render_rtti_note(
        None if d["rtti_note"] is None else RttiNote(**d["rtti_note"])
    )
    lines += render_confidence_section(
        None if d["confidence"] is None else ConfidenceSection(**d["confidence"])
    )
    lines += render_policy_section(PolicySection(**d["policy"]))
    if d["recommendation"] is not None:
        lines += render_recommendation_section(
            RecommendationSection(**d["recommendation"])
        )
    if d["severity_summary"] is not None:
        summary_rows = tuple(
            SeverityRow(**row) for row in d["severity_summary"]["rows"]
        )
        lines += render_severity_summary(SeveritySummary(rows=summary_rows))
    if d["show_only_note"] is not None:
        note = d["show_only_note"]
        lines.append(
            f"> Filtered by: `--show-only {note['show_only']}` "
            f"({note['shown']} of {note['total']} changes shown)"
        )
        lines.append("")
    if d["library_files"] is not None:
        lines += render_library_files_section(LibraryFilesSection(**d["library_files"]))
    for group in d["severity_groups"]:
        lines += [group["heading"], ""]
        if group["note_lines"]:
            lines += list(group["note_lines"])
            lines.append("")
        fmt = _render_change_row_oneline if group["oneline"] else _render_change_row
        for row in group["rows"]:
            lines.append(fmt(row))
        lines.append("")
    lines += _render_not_evaluated_lines(d["not_evaluated"])
    lines += render_environment_drift_section(
        _environment_drift_from_mapping(d["environment_drift"])
    )
    if d["empty_message"] is not None:
        lines.append(d["empty_message"])
    lines += _render_disposition_audit_from_mapping(d.get("disposition_audit"))
    lines += render_redundancy_note(
        None if d["redundancy_note"] is None else RedundancyNote(**d["redundancy_note"])
    )
    lines += render_suppression_note(
        _suppression_note_from_mapping(d["suppression_note"])
    )
    lines += render_out_of_surface_note(
        None
        if d["out_of_surface_note"] is None
        else OutOfSurfaceNote(**d["out_of_surface_note"])
    )
    if d["show_impact"]:
        lines.append("")
        lines += render_impact_table(_impact_table_from_mapping(d["impact_table"]))
    lines += render_footer()
    text = "\n".join(lines)
    if d["demangle"]:
        from ..demangle import demangle_text

        text = demangle_text(text)
    return text
