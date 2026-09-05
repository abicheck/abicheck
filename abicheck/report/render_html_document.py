# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The whole-document HTML projection -- ADR-061 Phase 2 item 1's closing
piece for HTML.

``html_report.build_html_document`` resolves every fact the report needs
(filtering, bucketing, compatibility metrics, gate/scoped-verdict data, and
every table row via :class:`~abicheck.report.render_html.ChangeRow`) into
one JSON-shaped :class:`~abicheck.report.document.ReportDocument`. This
module renders that document -- native abicheck layout or the ABICC-
compatible clone layout -- with zero ``DiffResult``/``Change`` access and no
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
    render_compat_changes_table,
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

# ---------------------------------------------------------------------------
# ABICC-compatible HTML (compat_html mode) -- moved from html_report.py
# alongside the rest of this module's formatting responsibility. Data-only:
# no decision of any kind lives in this stylesheet.
# ---------------------------------------------------------------------------

_COMPAT_CSS = """\
body { font-family: Arial, sans-serif; margin: 0; padding: 20px; color: #333; }
h1 { font-size: 1.6em; }
h2 { font-size: 1.2em; border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 24px; }
table.summary { border-collapse: collapse; margin: 8px 0; }
table.summary td, table.summary th { padding: 4px 12px; border: 1px solid #ddd; }
table.summary th { background: #f5f5f5; text-align: left; }
td.compatible { color: #1b5e20; font-weight: bold; }
td.incompatible { color: #b71c1c; font-weight: bold; }
td.warning { color: #e65100; font-weight: bold; }
table.problem { border-collapse: collapse; width: 100%; margin: 8px 0; }
table.problem td, table.problem th { padding: 4px 8px; border: 1px solid #ddd; vertical-align: top; }
table.problem th { background: #f5f5f5; text-align: left; }
.sym { font-family: monospace; font-size: 0.9em; }
"""


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
    """Render a complete HTML report -- native abicheck layout or the
    ABICC-compatible clone layout -- from a fully-resolved
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
    cache carried over. Skipped for the compat-mode layout, which never
    demangles, and for a native document with ``demangle`` off, matching
    ``abbr_symbol_text``'s own no-op in both cases -- prewarming would only
    populate a cache nothing downstream reads.
    """
    d = document.to_mapping()
    if d["mode"] == "compat":
        return _render_compat_html_document(d)
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
            section_htmls.append(
                "<div class='section'><p class='empty'>"
                f"No changes match the current filter "
                f"(<code>--show-only {h(empty_state['show_only'])}</code>). "
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
        filter_note = (
            f"<div class='section' style='background:#e3f2fd; padding:10px; border-left:4px solid #1976d2;'>"
            f"<strong>🔍 Filtered by:</strong> <code>--show-only {h(show_only)}</code> "
            f"({d['display_changes_count']} of {d['all_changes_count']} changes shown)"
            f"</div>"
        )

    impact_html = ""
    if d["show_impact"]:
        impact_html = render_impact(_impact_from_mapping(d["impact"]), demangle)

    body = f"""
<div class="header">
  <h1>{h(title) if title else f"ABI Compatibility Report — {lib_display}"}</h1>
  <div class="meta">
    {old_display} → {new_display} &nbsp;|&nbsp;
    Generated by <strong>abicheck</strong> (ABICC-compatible)
  </div>
  {file_metadata_html}
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
{scoped_html}
{confidence_html}
{nav_html}
{summary_html}
{filter_note}
{redundancy_note}
{sections_html}
{impact_html}

{render_footer("ABICC-compatible report format")}
"""
    return render_document(
        title=h(title)
        if title
        else f"ABI Report: {lib_display} {old_display} → {new_display}",
        body=body,
    )


def _compat_audit_html(d: Mapping[str, Any]) -> str:
    """ADR-067 D3's raw-versus-effective statement, for the ABICC layout.

    Rendered *inside* the existing ``Summary`` div and as an ordinary
    ``table.summary``, so no ABICC element id changes: consumers of this
    layout key off `Summary`/`Added`/`Removed`/`TypeProblems_*`, and this adds
    none of those.

    D3 applies to every projection, and this one returned before the native
    branch's audit was ever built -- so a fully suppressed comparison rendered
    here showed no raw total, no disposition counts and no coverage
    limitation, which is exactly the "looks clean" the audit exists to
    prevent (Codex review).
    """
    audit = d.get("disposition_audit") or {}
    detected = int(audit.get("detected_total") or 0)
    effective = int(audit.get("effective_total") or 0)
    overlays = int(audit.get("policy_overlays") or 0)
    not_evaluated = audit.get("not_evaluated_detectors") or []
    if not (detected or overlays or not_evaluated):
        return ""
    rows = [
        f"<tr><th>Detected (raw)</th><td>{detected}</td></tr>",
        f"<tr><th>Effective (gating)</th><td>{effective}</td></tr>",
    ]
    for name, count in (audit.get("counts") or {}).items():
        if count and name != "gating":
            label = html.escape(str(name).replace("_", " "))
            rows.append(f"<tr><th>… {label}</th><td>{int(count)}</td></tr>")
    if overlays:
        rows.append(f"<tr><th>Policy overlays</th><td>{overlays}</td></tr>")
    if not_evaluated:
        names = ", ".join(
            html.escape(str(det.get("name", ""))) for det in not_evaluated
        )
        rows.append(
            f"<tr><th>Not evaluated</th><td>{len(not_evaluated)} detector(s) "
            f"— {names}</td></tr>"
        )
    body = "\n".join(rows)
    return f"""
<h2>Disposition Audit</h2>
<table class='summary'>
{body}
</table>"""


def _render_compat_html_document(d: Mapping[str, Any]) -> str:
    h = html.escape
    compat = d["compat"]
    report_kind = d["report_kind"]
    bc_pct = d["bc_pct"]
    affected_pct = d["affected_pct"]
    old_symbol_count = d["old_symbol_count"]
    title = d["title"]
    lib_display = h(d["lib_name"]) if d["lib_name"] else "library"
    old_display = h(d["old_version"]) if d["old_version"] else "old"
    new_display = h(d["new_version"]) if d["new_version"] else "new"

    added_rows = _change_rows_from_mapping(compat["added_rows"])
    removed_rows = _change_rows_from_mapping(compat["removed_rows"])
    type_problems = {
        sev: _change_rows_from_mapping(compat["type_problems"][sev])
        for sev in ("High", "Medium", "Low")
    }
    symbol_problems = {
        sev: _change_rows_from_mapping(compat["symbol_problems"][sev])
        for sev in ("High", "Medium", "Low")
    }
    other_problems = {
        sev: _change_rows_from_mapping(compat["other_problems"][sev])
        for sev in ("High", "Medium", "Low")
    }

    tp_high, tp_med, tp_low = (len(type_problems[s]) for s in ("High", "Medium", "Low"))
    sp_high, sp_med, sp_low = (
        len(symbol_problems[s]) for s in ("High", "Medium", "Low")
    )

    # Already-resolved by build_html_document -- the 5-way Verdict -> ABICC's
    # 2-way compatible/incompatible bucketing is a policy interpretation, not
    # a formatting choice, so the renderer only reads it.
    compat_verdict = d["compat_verdict"]
    bc_css = (
        "incompatible" if bc_pct < 90 else ("warning" if bc_pct < 100 else "compatible")
    )
    affected_pct_label = f"{affected_pct:.1f}" if old_symbol_count else "0"
    kind_label = report_kind.capitalize()

    meta_data = (
        f"verdict:{compat_verdict};kind:{report_kind};"
        f"affected:{affected_pct_label};"
        f"added:{len(added_rows)};removed:{len(removed_rows)};"
        f"type_problems_high:{tp_high};"
        f"type_problems_medium:{tp_med};"
        f"type_problems_low:{tp_low};"
        f"interface_problems_high:{sp_high};"
        f"interface_problems_medium:{sp_med};"
        f"interface_problems_low:{sp_low};"
        f"changed_constants:0;"
        f"tool_version:abicheck"
    )

    abicc_title = (
        h(title)
        if title
        else f"{kind_label} compatibility report for the <b>{lib_display}</b> "
        f"library between <b>{old_display}</b> and <b>{new_display}</b> versions"
    )

    sections_html = []

    sections_html.append(f"""
<div id='Summary'>
<h2>Test Info</h2>
<table class='summary'>
<tr><th>Library Name</th><td>{lib_display}</td></tr>
<tr><th>Version #1</th><td>{old_display}</td></tr>
<tr><th>Version #2</th><td>{new_display}</td></tr>
</table>
{render_file_metadata(_file_metadata_from_mapping(d["file_metadata"]))}

<h2>Test Results</h2>
<table class='summary'>
<tr><th>Total Symbols</th><td>{old_symbol_count or "N/A"}</td></tr>
<tr><th>{kind_label} Compatibility</th><td class='{bc_css}'>{bc_pct:.1f}%</td></tr>
<tr><th>Verdict</th><td class='{bc_css}'>{compat_verdict}</td></tr>
</table>

<h2>Problem Summary</h2>
<table class='summary'>
<tr><th></th><th>High</th><th>Medium</th><th>Low</th></tr>
<tr><th>Type Problems</th><td>{tp_high}</td><td>{tp_med}</td><td>{tp_low}</td></tr>
<tr><th>Interface Problems</th><td>{sp_high}</td><td>{sp_med}</td><td>{sp_low}</td></tr>
<tr><th>Added Symbols</th><td colspan='3'>{len(added_rows)}</td></tr>
<tr><th>Removed Symbols</th><td colspan='3'>{len(removed_rows)}</td></tr>
</table>
{_compat_audit_html(d)}
</div>""")

    if added_rows:
        sections_html.append(f"""
<div id='Added'>
<h2>Added Symbols ({len(added_rows)})</h2>
{render_compat_changes_table(added_rows)}
</div>""")

    if removed_rows:
        sections_html.append(f"""
<div id='Removed'>
<h2>Removed Symbols ({len(removed_rows)})</h2>
{render_compat_changes_table(removed_rows)}
</div>""")

    for sev in ("High", "Medium", "Low"):
        items = type_problems[sev]
        if items:
            sections_html.append(f"""
<div id='TypeProblems_{sev}'>
<h2>Problems with Data Types — {sev} Severity ({len(items)})</h2>
{render_compat_changes_table(items, show_severity=True)}
</div>""")

    for sev in ("High", "Medium", "Low"):
        items = symbol_problems[sev]
        if items:
            sections_html.append(f"""
<div id='InterfaceProblems_{sev}'>
<h2>Problems with Symbols — {sev} Severity ({len(items)})</h2>
{render_compat_changes_table(items, show_severity=True)}
</div>""")

    other_all = (
        other_problems["High"] + other_problems["Medium"] + other_problems["Low"]
    )
    if other_all:
        sections_html.append(f"""
<div id='OtherProblems'>
<h2>Other Problems ({len(other_all)})</h2>
{render_compat_changes_table(other_all, show_severity=True)}
</div>""")

    body_html = "\n".join(sections_html)

    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>{kind_label} compatibility report for {lib_display} between {old_display} and {new_display}</title>
<style>{_COMPAT_CSS}</style>
</head>
<body>
<!-- {meta_data} -->
<div id='Title'>
<h1>{abicc_title}</h1>
</div>
{body_html}
<br/>
<hr/>
<p style="font-size:0.85em; color:#999;">
Generated by <b>abicheck</b> (ABICC-compatible mode)
</p>
</body>
</html>
"""
