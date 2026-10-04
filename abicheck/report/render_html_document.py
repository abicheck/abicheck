# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The whole-document HTML projection -- ADR-061 Phase 2 item 1's closing
piece for HTML.

``html_report.build_html_document`` resolves every fact the report needs
(filtering, bucketing, compatibility metrics, gate/scoped-verdict data, and
every table row via :class:`~abicheck.report.render_html.ChangeRow`) into
one JSON-shaped :class:`~abicheck.report.document.ReportDocument`. This
module renders that document with zero ``DiffResult``/``Change`` access and no
decision-making import. ``html_report.generate_html_report`` is a two-line
wrapper: build the document, then call :func:`render_html_document`.

Split out of ``report/render_html.py`` (which owns the smaller, reusable
per-section renderers this module calls) once the whole-document projection
pushed that file past the architecture check's new-file size ceiling --
D4/D5's "move responsibility to a properly-owned module, never trim to fit."
This module owns exactly one responsibility: turning a complete
``ReportDocument`` into the final HTML string. It does not decide what
belongs in the document -- that is ``html_report.py``'s job -- and it does
not define the small, reusable per-section renderers ``render_html.py``
still owns (a ``ReportDocument`` round-trips every dataclass into a plain
mapping, so the ``_*_from_mapping`` helpers below are this module's own
concern, not something the smaller renderers need).
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from ..demangle import prewarm_demangle_from_json_value
from ..html_template import _VERDICT_STYLE, render_document, render_footer
from .document import ReportDocument
from .edge_coverage_section import edge_coverage_section_from_mapping
from .render_edge_coverage import render_edge_coverage_html
from .render_html import (
    ChangeRow,
    ConfidenceData,
    FileMetadataTable,
    GateCardData,
    ImpactData,
    ImpactEntry,
    NavBarData,
    NotEvaluatedRow,
    NotEvaluatedSectionData,
    ScopedVerdictData,
    SummaryCategoryRow,
    SummaryTableData,
    render_changes_table,
    render_confidence,
    render_file_metadata,
    render_gate_card,
    render_impact,
    render_nav_bar,
    render_not_evaluated_section,
    render_scoped_verdict,
    render_summary_table,
    verdict_icon,
)
from .render_html_review_sections import (
    render_surface_changes_html,
    render_use_case_impact_html,
    render_versioning_policy_html,
)

# ---------------------------------------------------------------------------
# ReportDocument reconstruction -- the inverse of dataclasses.asdict(), since
# a ReportDocument round-trip turns every dataclass into a plain mapping and
# every tuple into a list (document.py's _freeze/_thaw). Each function below
# rebuilds exactly the struct the matching render_* in render_html.py already
# expects, so no render_* function needed to change shape for this to close
# item 1.
# ---------------------------------------------------------------------------


def _change_row_from_mapping(d: Mapping[str, Any]) -> ChangeRow:
    return ChangeRow(
        kind=d["kind"],
        category=d["category"],
        impact=d["impact"],
        severity=d["severity"],
        symbol=d["symbol"],
        description=d["description"],
        old_value=d["old_value"],
        new_value=d["new_value"],
        source_location=d["source_location"],
        affected_symbols=tuple(d["affected_symbols"]),
        caused_count=d["caused_count"],
        contract_relevance=d["contract_relevance"],
        contract_reason_code=d["contract_reason_code"],
        contract_assurance=d["contract_assurance"],
        compatibility_decision=d["compatibility_decision"],
        contract_evidence_refs=tuple(d["contract_evidence_refs"]),
        correlated_change_kind=d["correlated_change_kind"],
    )


def _change_rows_from_mapping(value: object) -> tuple[ChangeRow, ...]:
    assert isinstance(value, (list, tuple))
    return tuple(_change_row_from_mapping(item) for item in value)


def _file_metadata_from_mapping(
    d: Mapping[str, Any] | None,
) -> FileMetadataTable | None:
    if d is None:
        return None
    return FileMetadataTable(**d)


def _nav_bar_from_mapping(d: Mapping[str, Any]) -> NavBarData:
    return NavBarData(**d)


def _summary_table_from_mapping(d: Mapping[str, Any]) -> SummaryTableData:
    rows = tuple(SummaryCategoryRow(**row) for row in d["rows"])
    return SummaryTableData(
        rows=rows,
        total_removed=d["total_removed"],
        total_changed=d["total_changed"],
        total_added=d["total_added"],
        suppressed_count=d["suppressed_count"],
        detected_total=d.get("detected_total"),
        effective_total=d.get("effective_total"),
        disposition_counts=tuple(
            (name, count) for name, count in d.get("disposition_counts") or ()
        ),
        disposition_rules=tuple(d.get("disposition_rules") or ()),
        not_evaluated_detectors=tuple(d.get("not_evaluated_detectors") or ()),
        # Reconstructed like every other audit field. Falling through to the
        # dataclass default silently restored zero, so a run whose *only*
        # gate contributor is a policy overlay rendered an irreconcilable
        # summary -- "1 detected · 1 gating · 1 non gating" with nothing
        # explaining the difference (Codex review).
        policy_overlays=int(d.get("policy_overlays") or 0),
    )


def _confidence_from_mapping(d: Mapping[str, Any] | None) -> ConfidenceData | None:
    if d is None:
        return None
    return ConfidenceData(
        confidence=d["confidence"],
        evidence_tiers=tuple(d["evidence_tiers"]),
        policy=d["policy"],
        policy_overrides=tuple(tuple(pair) for pair in d["policy_overrides"]),
        policy_reclassify=tuple(d["policy_reclassify"]),
        coverage_warnings=tuple(d["coverage_warnings"]),
        comparability_dimensions=tuple(
            tuple(pair) for pair in d["comparability_dimensions"]
        ),
    )


def _impact_from_mapping(d: Mapping[str, Any] | None) -> ImpactData | None:
    if d is None:
        return None
    entries = tuple(ImpactEntry(**entry) for entry in d["entries"])
    return ImpactData(entries=entries)


def _gate_card_from_mapping(d: Mapping[str, Any] | None) -> GateCardData | None:
    if d is None:
        return None
    return GateCardData(
        scoped=d["scoped"],
        passed=d["passed"],
        exit_code=d["exit_code"],
        full_gate_label=d["full_gate_label"],
        blocking_categories=tuple(d["blocking_categories"]),
    )


def _scoped_verdict_from_mapping(
    d: Mapping[str, Any] | None,
) -> ScopedVerdictData | None:
    if d is None:
        return None
    return ScopedVerdictData(**d)


def _not_evaluated_from_mapping(d: Mapping[str, Any]) -> NotEvaluatedSectionData:
    rows = tuple(NotEvaluatedRow(**row) for row in d["rows"])
    return NotEvaluatedSectionData(rows=rows)


# ---------------------------------------------------------------------------
# The whole-document projection -- ADR-061 Phase 2 item 1's closing piece.
# ---------------------------------------------------------------------------


def render_html_document(document: ReportDocument) -> str:
    """Render a complete HTML report from a fully-resolved
    :class:`~abicheck.report.document.ReportDocument`.

    Every fact this needs (buckets, counts, section contents, gate/scoped-
    verdict data, per-row table facts) was already decided by
    ``html_report.build_html_document``; this function and its two
    mode-specific halves below only format it. Neither reads a
    ``DiffResult``/``Change`` or imports a policy/classification module.

    Demangling is a formatting choice (see ``abbr_symbol_text``'s own
    contract in ``render_html.py``), so batch-prewarming the demangle cache
    belongs here rather than in ``build_html_document``: this is the one
    function that actually walks every row and calls into
    ``demangle``/``demangle_text``, including when it runs standalone on a
    document built (or deserialized) in an earlier process, with no warm
    cache carried over. Skipped for a document with ``demangle`` off,
    matching ``abbr_symbol_text``'s own no-op -- prewarming would only
    populate a cache nothing downstream reads.
    """
    d = document.to_mapping()
    if d["demangle"]:
        prewarm_demangle_from_json_value(d)
    return _render_native_html_document(d)


def _render_native_html_document(d: Mapping[str, Any]) -> str:
    verdict = d["verdict"]
    fg, bg = _VERDICT_STYLE.get(verdict, ("#212121", "#f5f5f5"))
    h = html.escape
    lib_name = d["lib_name"]
    title = d["title"]
    demangle = d["demangle"]
    lib_display = h(lib_name) if lib_name else "library"
    old_display = h(d["old_version"]) if d["old_version"] else "old"
    new_display = h(d["new_version"]) if d["new_version"] else "new"

    gate_html = render_gate_card(_gate_card_from_mapping(d["gate_card"]))
    scoped_html = render_scoped_verdict(
        _scoped_verdict_from_mapping(d["scoped_verdict"])
    )
    summary_html = render_summary_table(_summary_table_from_mapping(d["summary_table"]))
    nav = _nav_bar_from_mapping(d["nav_bar"])
    nav_html = render_nav_bar(nav)
    confidence_html = render_confidence(_confidence_from_mapping(d["confidence"]))
    edge_coverage_html = render_edge_coverage_html(
        edge_coverage_section_from_mapping(d.get("edge_coverage"))
    )
    file_metadata_html = render_file_metadata(
        _file_metadata_from_mapping(d["file_metadata"])
    )

    section_htmls: list[str] = []
    for section in d["sections"]:
        kind = section["kind"]
        if kind == "changes":
            rows = _change_rows_from_mapping(section["rows"])
            tbl = render_changes_table(rows, demangle)
            section_htmls.append(
                f"<div class='section {section['css_class']}' id='{section['anchor']}'>"
                f"<h3>{section['title']} ({len(rows)})</h3>"
                f"{tbl}"
                f"</div>"
            )
        elif kind == "suppressed_placeholder":
            section_htmls.append(
                f"<div class='section section-suppressed' id='suppressed'>"
                f"<h3>🔕 Suppressed Changes ({section['count']})</h3>"
                f"<p class='empty'>Details not available (suppressed_changes list is empty).</p>"
                f"</div>"
            )
        else:  # "not_evaluated"
            section_htmls.append(
                render_not_evaluated_section(
                    _not_evaluated_from_mapping(section["data"]), demangle
                )
            )

    if not section_htmls:
        empty_state = d["empty_state"]
        if empty_state is not None and empty_state["kind"] == "filtered":
            # Codex review (PR #1154 second follow-up: "Render repeated show
            # groups as repeated view options") -- this "no changes match"
            # note has the identical raw-separator problem the "Filtered
            # by" note above does; same fix, same helper.
            from ..reporter_markdown import render_show_only_cli_hint

            cli_hint = render_show_only_cli_hint(empty_state["show_only"])
            section_htmls.append(
                "<div class='section'><p class='empty'>"
                f"No changes match the current filter "
                f"(<code>{h(cli_hint)}</code>). "
                f"{empty_state['all_changes_count']} change(s) exist but are "
                f"excluded by the filter."
                "</p></div>"
            )
        else:
            section_htmls.append(
                "<div class='section'><p class='empty'>"
                "No ABI changes detected between the two versions."
                "</p></div>"
            )

    sections_html = "\n".join(section_htmls)

    old_symbol_count = d["old_symbol_count"]
    symbol_count_note = (
        f" / {old_symbol_count} exported symbols" if old_symbol_count else ""
    )

    redundant_count = d["redundant_count"]
    redundancy_note = ""
    if redundant_count > 0:
        redundancy_note = (
            f"<div class='section' style='background:#fff3e0; padding:10px; border-left:4px solid #ff9800;'>"
            f"<strong>ℹ️ {redundant_count} redundant change(s)</strong> hidden "
            f"(derived from root type changes). Set <code>scope.show_redundant: true</code> "
            f"in <code>.abicheck.yml</code> to show all."
            f"</div>"
        )

    show_only = d["show_only"]
    filter_note = ""
    if show_only:
        # Codex review (PR #1154 second follow-up: "Render repeated show
        # groups as repeated view options") -- render each `;`-joined
        # internal group as its own `--view show=...` token, the same
        # helper the Markdown "Filtered by" notes use.
        from ..reporter_markdown import render_show_only_cli_hint

        cli_hint = render_show_only_cli_hint(show_only)
        filter_note = (
            f"<div class='section' style='background:#e3f2fd; padding:10px; border-left:4px solid #1976d2;'>"
            f"<strong>🔍 Filtered by:</strong> <code>{h(cli_hint)}</code> "
            f"({d['display_changes_count']} of {d['all_changes_count']} changes shown)"
            f"</div>"
        )

    versioning_html = render_versioning_policy_html(d.get("versioning_policy"))
    use_case_html = render_use_case_impact_html(d.get("use_case_impact"))
    surface_html = render_surface_changes_html(d.get("surface_changes"))

    impact_html = ""
    if d["show_impact"]:
        impact_html = render_impact(_impact_from_mapping(d["impact"]), demangle)

    # Codex review, P2 (Finding 4): the same declared-deployment-floor
    # digest the JSON/Markdown/SARIF/JUnit projections carry under
    # `env_matrix_source_sha256` -- omitted entirely (not an empty div) when
    # no `deployment:` contract governed this run.
    env_matrix_digest = d.get("env_matrix_source_sha256")
    env_matrix_html = (
        ""
        if env_matrix_digest is None
        else (
            f"<div class='meta'>Deployment floor digest: "
            f"<code>{h(env_matrix_digest)}</code></div>"
        )
    )

    body = f"""
<div class="header">
  <h1>{h(title) if title else f"ABI Compatibility Report — {lib_display}"}</h1>
  <div class="meta">
    {old_display} → {new_display} &nbsp;|&nbsp;
    Generated by <strong>abicheck</strong>
  </div>
  {file_metadata_html}{env_matrix_html}
</div>

<div class="verdict-box" style="background:{bg}; color:{fg}; border-left:6px solid {fg};">
  <h2>{verdict_icon(verdict)} Compatibility: {h(verdict)}</h2>
  <div class="bc-metric">
    Binary Compatibility: <strong>{d["bc_pct"]:.1f}%</strong>
    <span style="font-size:0.82em; opacity:0.75">
      ({d["breaking_count"]} breaking change(s){symbol_count_note})
    </span>
    &nbsp;&nbsp;
    <span style="font-size:0.85em;">
      Removed: <strong>{nav.removed}</strong>
      &nbsp;|&nbsp; Changed: <strong>{nav.changed}</strong>
      &nbsp;|&nbsp; Added: <strong>{nav.added}</strong>
    </span>
  </div>
</div>

{gate_html}
{scoped_html}{versioning_html}
{confidence_html}
{edge_coverage_html}
{nav_html}
{summary_html}
{filter_note}
{redundancy_note}
{surface_html}{sections_html}{use_case_html}
{impact_html}

{render_footer("ABI compatibility report")}
"""
    return render_document(
        title=h(title)
        if title
        else f"ABI Report: {lib_display} {old_display} → {new_display}",
        body=body,
    )
