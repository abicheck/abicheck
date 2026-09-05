# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Pure HTML projection for html_report.py's structured sections.

ADR-061 Phase 2 item 1's HTML closure. ``html_report.py`` historically built
its ``<div>``/``<table>``/``<span>`` markup directly while walking a
``DiffResult`` -- format decisions (tag structure, inline styles, emoji,
colour palette, cell order) were interleaved with the business logic that
decides *what* belongs in a section (which changes fall in which bucket,
which policy rules are still active, whether a gate blocked and on which
categories, what a table's cell values are).

This module is the render half of that split for the *reusable, per-section*
pieces, and it is the exact counterpart of ``render_markdown.py`` for prose,
``render_json.py`` for JSON, and ``render_xml.py`` for JUnit XML. Each
``compute_*`` function in ``html_report.py`` reads a ``DiffResult``/
``Change`` sequence and returns one of the small, frozen dataclasses below --
plain data: strings, ints, bools and tuples, never a pre-built markup
fragment. The ``render_*`` function here consumes that structure and returns
the same HTML string the pre-split function used to build in one step.

The two low-level per-change formatters (``abbr_symbol_text``,
``render_changes_table``) and the ABICC-style ``render_compat_changes_table``
live here too, moved rather than left in ``html_report.py``, for the same
reason ``_format_change_md`` moved into ``render_markdown.py``: each takes a
JSON-safe :class:`ChangeRow` and returns a formatted string with no
``DiffResult``/``Change`` traversal or policy decision of its own, so it
belongs on the render side of the split -- and keeping them here (rather
than in ``html_report.py``, which needs to call into this module for every
``render_*`` function) avoids a same-layer import cycle between the two
modules. ``html_report.py`` re-exports the ones it still calls directly
under their original private names (``_abbr_symbol_text``, ``_changes_table``)
so every existing call site and its direct test coverage resolves unchanged.
``render_compat_changes_table`` has no such wrapper: the whole-document
closure moved its only caller (the ABICC-compatible layout) onto
``report.render_html_document`` directly, retiring the pre-split
``_compat_changes_table`` alongside it -- see this module's own test suite
(``tests/unit/report/test_render_html.py``) for its direct coverage now.

Every per-section ``render_*`` function here is behaviour-preserving by
construction: each was extracted line-for-line from the pre-split function it
replaces, with only the *source* of each value changed (a struct field
instead of a re-derivation from ``DiffResult``/``Change``). See
``tests/test_html_template_golden.py`` for the byte-exact contract this
rests on -- in particular the ``main_report_rich.html`` and
``main_report_scoped.html`` cases, added against the pre-split code
specifically to pin the sections below.

**The whole-document projection that literally closes item 1** --
``render_html_document``, which crosses the single canonical
``ReportDocument`` boundary every other format (JSON, SARIF, JUnit,
``--stat``) already crosses -- lives in the sibling module
:mod:`abicheck.report.render_html_document`, not here: routing the whole
document through this module too pushed it past the architecture check's
new-file size ceiling, and the two responsibilities are genuinely distinct
(this module's structs and formatters are reusable building blocks; that
module owns only "assemble the complete page from a finished document").
`ChangeRow` retires the previous ``id(change)``-keyed ``ChangeRowFactsById``
lookup table entirely: it is an ordinary, JSON-round-trippable value, so no
identity-based indexing trick is needed to carry a change's resolved facts
alongside its raw display fields.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

from ..demangle import demangle as _demangle_symbol, demangle_text
from ..html_template import _VERDICT_STYLE


def verdict_icon(verdict: str) -> str:
    """Return emoji icon for verdict."""
    return {
        "BREAKING": "🔴",
        "COMPATIBLE": "🟢",
        "COMPATIBLE_WITH_RISK": "🟠",
        "NO_CHANGE": "🔵",
        "API_BREAK": "🟠",
    }.get(verdict, "⚪")


def abbr_symbol_text(raw: str, demangle: bool = True) -> str:
    """Demangled text with the mangled name as an ``<abbr>`` tooltip --
    shared by any caller rendering one bare symbol string, so two mangled
    names that demangle identically (C1/C2 ctor variants) don't collapse
    into indistinguishable text. Demangles before ``html.escape``.

    Whole-string demangling (:func:`demangle`), not :func:`demangle_text`'s
    embedded-token scan -- *raw* is one symbol field, not prose, and a
    substring scan risks corrupting a real non-mangled name that merely
    contains a `_Z...`-shaped substring (e.g. `prefix_Z3foov`) (Codex
    review, fresh evidence)."""
    mangled = html.escape(raw)
    result = (
        _demangle_symbol(raw, accept_macho_prefix=True) if demangle and raw else None
    )
    if not result:
        return mangled
    demangled = html.escape(result)
    if demangled == mangled:
        return demangled
    return f'<abbr title="{html.escape(mangled, quote=True)}">{demangled}</abbr>'


@dataclass(frozen=True)
class ChangeRow:
    """One change's already-resolved facts and raw display fields, entirely
    as JSON-safe scalars and tuples -- the ``ReportDocument``-shaped
    counterpart to a live ``Change`` object (ADR-061 Phase 2 item 1).

    ``kind``/``category``/``impact``/``severity`` are registry lookups
    (`report_classifications.kind_str`/`category`/`severity`,
    `checker_policy.impact_for`) resolved once per change by
    ``html_report.compute_full_change_rows`` -- a Codex review on this
    module's original split correctly held that calling those lookups from
    inside a ``render_*`` function left real decisions on the render side,
    against this package's stated contract that a renderer "decides
    nothing". The remaining fields are raw, undemangled strings pulled
    straight off the ``Change``: escaping and demangling stay a render-time
    formatting choice, same as every other symbol-bearing cell in this
    module. ``None`` means the source ``Change`` never carried that field;
    an empty tuple means it carried an empty collection.
    """

    kind: str
    category: str
    impact: str
    severity: str
    symbol: str
    description: str
    old_value: str
    new_value: str
    source_location: str | None
    affected_symbols: tuple[str, ...]
    caused_count: int
    contract_relevance: str | None
    contract_reason_code: str | None
    contract_assurance: str | None
    compatibility_decision: str | None
    contract_evidence_refs: tuple[str, ...]
    correlated_change_kind: str | None


def render_changes_table(rows: tuple[ChangeRow, ...], demangle: bool = True) -> str:
    if not rows:
        return "<p class='empty'>No changes in this category.</p>"

    out_rows = []
    for row in rows:
        ks = row.kind
        cat = row.category
        desc = html.escape(
            demangle_text(row.description) if demangle else row.description
        )
        old_val = abbr_symbol_text(row.old_value, demangle)
        new_val = abbr_symbol_text(row.new_value, demangle)
        sym_cell = abbr_symbol_text(row.symbol, demangle)

        # Build extended description with impact + affected + location
        desc_parts = [desc]
        if row.impact:
            desc_parts.append(
                f"<div style='font-size:0.85em; color:#666; margin-top:3px;'>"
                f"💡 {html.escape(row.impact)}</div>"
            )
        if row.affected_symbols:
            names = ", ".join(
                abbr_symbol_text(s, demangle) for s in row.affected_symbols[:5]
            )
            suffix = (
                f" (+{len(row.affected_symbols) - 5} more)"
                if len(row.affected_symbols) > 5
                else ""
            )
            desc_parts.append(
                f"<div style='font-size:0.82em; color:#1565c0; margin-top:2px;'>"
                f"📎 Affected: <code>{names}</code>{suffix}</div>"
            )
        if row.source_location:
            desc_parts.append(
                f"<div style='font-size:0.82em; color:#999; margin-top:2px;'>"
                f"📍 {html.escape(row.source_location)}</div>"
            )
        if row.caused_count > 0:
            desc_parts.append(
                f"<div style='font-size:0.82em; color:#e65100; margin-top:2px;'>"
                f"🔗 {row.caused_count} derived change(s) collapsed</div>"
            )
        # CLI-audit P1: same per-finding contract-decision parity SARIF's
        # `properties`/JUnit's `<properties>` carry (always IN_CONTRACT/
        # NOT_APPLICABLE here; `gate_contribution` omitted, its own follow-up).
        if row.contract_relevance is not None:
            bits = [f"relevance: {html.escape(row.contract_relevance)}"]
            if row.contract_reason_code:
                bits.append(f"reason: {html.escape(row.contract_reason_code)}")
            if row.contract_assurance is not None:
                bits.append(f"assurance: {html.escape(row.contract_assurance)}")
            if row.compatibility_decision is not None:
                bits.append(f"decision: {html.escape(row.compatibility_decision)}")
            if row.contract_evidence_refs:
                bits.append(
                    f"evidence: {html.escape(', '.join(row.contract_evidence_refs))}"
                )
            desc_parts.append(
                f"<div style='font-size:0.82em; color:#6a1b9a; margin-top:2px;'>"
                f"📜 Contract — {' · '.join(bits)}</div>"
            )
        # Cross-detector correlation (e.g. LAYOUT_UNVERIFIABLE sharing its
        # evidence gap with a co-reported TYPE_VTABLE_CHANGED) -- only
        # JSON/SARIF rendered this field before (Codex review).
        if row.correlated_change_kind:
            desc_parts.append(
                f"<div style='font-size:0.82em; color:#999; margin-top:2px;'>"
                f"🔗 See also: <code>{html.escape(row.correlated_change_kind)}</code> "
                f"finding for the same symbol</div>"
            )
        full_desc = "".join(desc_parts)

        out_rows.append(
            f"<tr>"
            f"<td><span class='kind-badge'>{html.escape(ks)}</span></td>"
            f"<td class='sym'>{sym_cell}</td>"
            f"<td><span class='cat-badge'>{html.escape(cat)}</span></td>"
            f"<td>{full_desc}</td>"
            f"<td>{old_val}</td>"
            f"<td>{new_val}</td>"
            f"</tr>"
        )

    body = "\n".join(out_rows)
    return f"""<table class='changes'>
  <thead>
    <tr>
      <th>Kind</th><th>Symbol</th><th>Category</th>
      <th>Description</th><th>Old&nbsp;value</th><th>New&nbsp;value</th>
    </tr>
  </thead>
  <tbody>
    {body}
  </tbody>
</table>"""


def render_compat_changes_table(
    rows: tuple[ChangeRow, ...],
    show_severity: bool = False,
) -> str:
    """Render a changes table in ABICC style."""
    if not rows:
        return "<p>No changes.</p>"
    h = html.escape
    out_rows = []
    for row in rows:
        sym = h(row.symbol)
        desc = h(row.description)
        old_val = h(row.old_value)
        new_val = h(row.new_value)
        sev_cell = f"<td>{row.severity}</td>" if show_severity else ""
        # Cross-detector correlation: this ABICC-compatible table has its own
        # separate rendering from render_changes_table above, needing the same
        # note (Codex review, fresh evidence).
        if row.correlated_change_kind:
            desc += (
                f"<div style='font-size:0.82em; color:#999; margin-top:2px;'>"
                f"🔗 See also: <code>{h(row.correlated_change_kind)}</code></div>"
            )
        out_rows.append(
            f"<tr><td class='sym'>{sym}</td><td>{h(row.kind)}</td>"
            f"{sev_cell}<td>{desc}</td><td>{old_val}</td><td>{new_val}</td></tr>"
        )
    sev_hdr = "<th>Severity</th>" if show_severity else ""
    return (
        f"<table class='problem'><thead><tr>"
        f"<th>Symbol</th><th>Kind</th>{sev_hdr}"
        f"<th>Description</th><th>Old</th><th>New</th>"
        f"</tr></thead><tbody>{''.join(out_rows)}</tbody></table>"
    )


# ---------------------------------------------------------------------------
# Structured sections: one frozen ``*Data`` per section, one ``render_*`` each
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FileMetadataTable:
    """The six library-file facts the "Library Files" table shows.

    ``None`` from ``compute_file_metadata`` means "neither side carried
    metadata", which renders nothing at all -- distinct from a side that
    carried metadata with missing fields, which renders the ``—`` placeholder
    each field below already holds.
    """

    old_path: str
    new_path: str
    old_sha: str
    new_sha: str
    old_size: str
    new_size: str


def render_file_metadata(data: FileMetadataTable | None) -> str:
    if data is None:
        return ""
    h = html.escape

    def _row(label: str, old_val: str, new_val: str) -> str:
        return f"<tr><th>{label}</th><td>{h(old_val)}</td><td>{h(new_val)}</td></tr>"

    return f"""<div class='summary-section'>
  <h3>Library Files</h3>
  <table class='summary-table'>
    <thead><tr><th></th><th>Old</th><th>New</th></tr></thead>
    <tbody>
      {_row("Path", data.old_path, data.new_path)}
      {_row("SHA-256", data.old_sha[:16] + "…", data.new_sha[:16] + "…")}
      {_row("Size (bytes)", data.old_size, data.new_size)}
    </tbody>
  </table>
</div>"""


@dataclass(frozen=True)
class SummaryCategoryRow:
    """One category's removed/changed/added counts. Only non-empty rows reach
    here -- the "every count is zero, skip the row" decision is compute-side,
    since it is about what the report says, not how it looks."""

    label: str
    removed: int
    changed: int
    added: int


#: Detector names listed inline in the HTML summary row before it collapses
#: to a count (an evidence-poor comparison legitimately leaves a dozen
#: unevaluated).
_NOT_EVALUATED_INLINE = 4


@dataclass(frozen=True)
class SummaryTableData:
    rows: tuple[SummaryCategoryRow, ...]
    total_removed: int
    total_changed: int
    total_added: int
    suppressed_count: int
    #: ADR-067 D3's raw-versus-effective counts, already resolved: the
    #: detected total, the gating total, ``(disposition, count)`` pairs, and
    #: one already-formatted line per rule that produced a disposition. Plain
    #: values only, because this struct round-trips through a
    #: ``ReportDocument`` mapping. Empty tuple / ``None`` for a caller that
    #: has no audit to supply (several tests build this struct directly).
    detected_total: int | None = None
    effective_total: int | None = None
    disposition_counts: tuple[tuple[str, int], ...] = ()
    disposition_rules: tuple[str, ...] = ()
    #: Diagnostics the gate can score that are not observations -- in the
    #: effective total, in neither the detected total nor the counts.
    policy_overlays: int = 0
    #: Names of detectors that did not run. Carried separately from the
    #: counts because it is the one audit fact that can be non-empty while
    #: every count is zero — exactly the case (a zero-delta comparison with a
    #: real support gap) where dropping it renders a clean
    #: "0 detected · 0 gating" over an analysis that never happened.
    not_evaluated_detectors: tuple[str, ...] = ()


def render_summary_table(data: SummaryTableData) -> str:
    rows = []
    for row in data.rows:
        r = f"<span class='num num-red'>{row.removed}</span>" if row.removed else "—"
        ch_n = (
            f"<span class='num num-blue'>{row.changed}</span>" if row.changed else "—"
        )
        a = f"<span class='num num-green'>{row.added}</span>" if row.added else "—"
        rows.append(
            f"<tr><td>{html.escape(row.label)}</td><td>{r}</td><td>{ch_n}</td><td>{a}</td></tr>"
        )

    total_r = f"<span class='num num-red'>{data.total_removed}</span>"
    total_ch = f"<span class='num num-blue'>{data.total_changed}</span>"
    total_a = f"<span class='num num-green'>{data.total_added}</span>"
    rows.append(
        f"<tr style='border-top:2px solid #e0e0e0; font-weight:bold;'>"
        f"<td>Total</td><td>{total_r}</td><td>{total_ch}</td><td>{total_a}</td></tr>"
    )

    if data.suppressed_count:
        rows.append(
            f"<tr><td colspan='4' style='color:#6a1b9a; font-size:0.85em; padding:6px 12px;'>"
            f"ℹ️ {data.suppressed_count} change(s) suppressed by suppression file</td></tr>"
        )

    # ADR-067 D3: the category table above counts what is *displayed*, so it
    # reads "nothing happened" for a comparison whose findings were all
    # suppressed or scoped out. These rows are the raw-versus-effective
    # statement every projection owes, in the same table a reader is already
    # looking at.
    if data.detected_total is not None or data.not_evaluated_detectors:
        # Skipping the gating entry: it is already the headline number, and
        # repeating it in the detail reads as two different findings.
        detail = ", ".join(
            f"{count} {name.replace('_', ' ')}"
            for name, count in data.disposition_counts
            if count and name != "gating"
        )
        detail = html.escape(detail)
        if data.policy_overlays:
            detail += (
                f"{', ' if detail else ''}{data.policy_overlays} policy overlay(s)"
            )
        if data.not_evaluated_detectors:
            listed = ", ".join(
                html.escape(name)
                for name in data.not_evaluated_detectors[:_NOT_EVALUATED_INLINE]
            )
            remaining = len(data.not_evaluated_detectors) - _NOT_EVALUATED_INLINE
            more = f", … and {remaining} more" if remaining > 0 else ""
            detail += (
                f"{' · ' if detail else ''}"
                f"{len(data.not_evaluated_detectors)} detector(s) not evaluated "
                f"({listed}{more})"
            )
        rows.append(
            f"<tr style='border-top:1px solid #e0e0e0;'>"
            f"<td colspan='4' style='font-size:0.85em; padding:6px 12px;'>"
            f"🔎 {data.detected_total or 0} detected · "
            f"{data.effective_total or 0} gating"
            f"{f' · {detail}' if detail else ''}</td></tr>"
        )
        for line in data.disposition_rules:
            rows.append(
                f"<tr><td colspan='4' style='color:#6a1b9a; font-size:0.8em; "
                f"padding:2px 24px;'>{html.escape(line)}</td></tr>"
            )

    body = "\n".join(rows)
    return f"""<div class='summary-section'>
  <h3>📊 Change Summary</h3>
  <table class='summary-table'>
    <thead>
      <tr><th>Category</th><th>Removed</th><th>Changed</th><th>Added</th></tr>
    </thead>
    <tbody>
      {body}
    </tbody>
  </table>
</div>"""


@dataclass(frozen=True)
class NavBarData:
    removed: int
    changed: int
    added: int
    suppressed_count: int


def render_nav_bar(data: NavBarData) -> str:
    links = []
    if data.removed:
        links.append(
            f"<a href='#removed' class='breaking'>⛔ Removed ({data.removed})</a>"
        )
    if data.changed:
        links.append(
            f"<a href='#changed' class='breaking'>⚠️ Changed ({data.changed})</a>"
        )
    if data.added:
        links.append(f"<a href='#added' class='added'>✅ Added ({data.added})</a>")
    if data.suppressed_count:
        links.append(
            f"<a href='#suppressed'>🔕 Suppressed ({data.suppressed_count})</a>"
        )
    if not links:
        return ""
    return "<div class='nav'>" + "".join(links) + "</div>"


@dataclass(frozen=True)
class ConfidenceData:
    """Everything the "Analysis Confidence" table discloses.

    ``policy_reclassify`` holds the *already-filtered* rule descriptions:
    deciding which rules are still in effect (an expired rule must not be
    disclosed as though it were) is a policy question, so it stays
    compute-side; this struct carries only the resulting strings.
    """

    confidence: str
    evidence_tiers: tuple[str, ...]
    policy: str
    policy_overrides: tuple[tuple[str, str], ...]
    policy_reclassify: tuple[str, ...]
    coverage_warnings: tuple[str, ...]


def render_confidence(data: ConfidenceData | None) -> str:
    if data is None:
        return ""
    h = html.escape
    conf_color = {"high": "#1b5e20", "medium": "#e65100", "low": "#b71c1c"}.get(
        data.confidence, "#212121"
    )
    tier_badges = (
        " ".join(f"<span class='kind-badge'>{h(t)}</span>" for t in data.evidence_tiers)
        if data.evidence_tiers
        else "<em>none</em>"
    )

    rows = [
        f"<tr><th>Confidence</th>"
        f"<td><strong style='color:{conf_color}'>{h(data.confidence.upper())}</strong></td></tr>",
        f"<tr><th>Evidence tiers</th><td>{tier_badges}</td></tr>",
        f"<tr><th>Policy</th><td><code>{h(data.policy)}</code></td></tr>",
    ]
    if data.policy_overrides:
        overrides = ", ".join(
            f"<code>{h(k)}</code>&nbsp;→&nbsp;<code>{h(v)}</code>"
            for k, v in data.policy_overrides
        )
        rows.append(f"<tr><th>Policy overrides</th><td>{overrides}</td></tr>")
    if data.policy_reclassify:
        rules = ", ".join(h(rule) for rule in data.policy_reclassify)
        rows.append(f"<tr><th>Policy reclassify</th><td>{rules}</td></tr>")
    for w in data.coverage_warnings:
        rows.append(f"<tr><th>Coverage gap</th><td>{h(w)}</td></tr>")

    body = "\n".join(rows)
    return (
        f"<div class='summary-section'>"
        f"<h3>🔍 Analysis Confidence</h3>"
        f"<table class='summary-table'><tbody>{body}</tbody></table>"
        f"</div>"
    )


@dataclass(frozen=True)
class ImpactEntry:
    """One root type change and what it reaches. ``symbol`` is the raw,
    undemangled name -- ``render_impact`` applies the report's demangle
    setting, the same way every other symbol-bearing cell does."""

    symbol: str
    kind_value: str
    interface_count: int
    caused_count: int


@dataclass(frozen=True)
class ImpactData:
    entries: tuple[ImpactEntry, ...]


def render_impact(data: ImpactData | None, demangle: bool = True) -> str:
    if data is None:
        return ""
    rows = []
    for entry in data.entries:
        iface_str = (
            f"{entry.interface_count} interface(s)"
            if entry.interface_count > 0
            else "—"
        )
        caused_str = (
            f" (+{entry.caused_count} collapsed)" if entry.caused_count > 0 else ""
        )
        rows.append(
            f"<tr><td><code>{abbr_symbol_text(entry.symbol, demangle)}</code></td>"
            f"<td><span class='kind-badge'>{html.escape(entry.kind_value)}</span></td>"
            f"<td>{iface_str}{caused_str}</td></tr>"
        )
    body = "\n".join(rows)
    return (
        f"<div class='section' id='impact'>"
        f"<h3>📊 Impact Summary</h3>"
        f"<table class='changes'>"
        f"<thead><tr><th>Root Change</th><th>Kind</th><th>Affected Interfaces</th></tr></thead>"
        f"<tbody>{body}</tbody></table></div>"
    )


@dataclass(frozen=True)
class GateCardData:
    """The CI-gate card's facts, all of them already decided.

    ``scoped`` selects which of the two cards this is: the scoped
    ``--used-by``/``--required-symbol`` gate the CLI process actually exits
    on, or the full-library one. The gate decision itself is never made here
    or in ``compute_gate_card`` -- it is projected from
    :func:`abicheck.policy.gate_decision.gate_decision_for_result` (ADR-061
    D9). ``blocking_categories`` is empty for a scoped card by construction:
    the categories correspond 1:1 to the *full* gate only, so naming them
    beside a scoped-only failure would attribute it to the wrong decision.
    """

    scoped: bool
    passed: bool
    exit_code: int
    full_gate_label: str
    blocking_categories: tuple[str, ...]


def render_gate_card(data: GateCardData | None) -> str:
    if data is None:
        return ""
    h = html.escape
    if data.scoped:
        gate_title = "CI Gate (scoped)"
        gate_note = (
            f"Reflects the scoped --used-by/--required-symbol severity gate "
            f"the CLI process actually exits on (full-library gate: "
            f"{h(data.full_gate_label)})."
        )
    else:
        gate_title = "CI Gate"
        gate_note = (
            "Reflects the configured severity gate — may differ from the "
            "Compatibility verdict above (e.g. an addition promoted to "
            "<code>error</code> still fails CI)."
        )
    gate_fg, gate_bg = ("#1b5e20", "#e8f5e9") if data.passed else ("#b71c1c", "#ffebee")
    gate_label = "PASS" if data.passed else f"FAIL (exit {data.exit_code})"
    gate_icon = "✅" if data.passed else "🛑"
    # Names which severity category(ies) actually gated CI — without this,
    # "FAIL" reads as an undifferentiated red box even for a policy-blocked
    # COMPATIBLE addition rather than a genuine ABI/API break.
    gate_categories_html = ""
    if not data.passed and data.blocking_categories:
        cats = ", ".join(
            f"<code>{h(c)}</code>" for c in sorted(data.blocking_categories)
        )
        gate_categories_html = (
            f"<div class='bc-metric' style='font-size:0.85em; opacity:0.85;'>"
            f"Blocked by: {cats}</div>"
        )
    return (
        f"<div class='verdict-box' "
        f"style='background:{gate_bg}; color:{gate_fg}; "
        f"border-left:6px solid {gate_fg};'>"
        f"<h2>{gate_icon} {h(gate_title)}: {h(gate_label)}</h2>"
        f"<div class='bc-metric' style='font-size:0.85em; opacity:0.85;'>"
        f"{gate_note}"
        f"</div>"
        f"{gate_categories_html}"
        f"</div>"
    )


@dataclass(frozen=True)
class ScopedVerdictData:
    """The ``--used-by``/``--required-symbol`` scoped-verdict box (ADR-043).

    ``exit_code``/``exit_code_scheme`` are stated rather than derived from
    ``verdict_value``: under a severity scheme the scoped exit code is *not*
    a fixed BREAKING->4/API_BREAK->2 mapping of the scoped verdict (e.g.
    ``--severity-preset info-only`` can floor it at 0 for a BREAKING scoped
    verdict), so implying that equivalence here would be wrong.
    """

    verdict_value: str
    exit_code: int | None
    exit_code_scheme: str | None


def render_scoped_verdict(data: ScopedVerdictData | None) -> str:
    if data is None:
        return ""
    h = html.escape
    scoped_fg, scoped_bg = _VERDICT_STYLE.get(
        data.verdict_value, ("#212121", "#f5f5f5")
    )
    exit_note = (
        f"The CLI process exits {data.exit_code} under the "
        f"{data.exit_code_scheme} exit-code scheme for this "
        f"--used-by/--required-symbol run"
        if data.exit_code is not None
        else "This is what the CLI process exit code reflects for this "
        "--used-by/--required-symbol run"
    )
    return (
        f"<div class='verdict-box' "
        f"style='background:{scoped_bg}; color:{scoped_fg}; "
        f"border-left:6px solid {scoped_fg};'>"
        f"<h2>{verdict_icon(data.verdict_value)} Scoped verdict: {h(data.verdict_value)}</h2>"
        f"<div class='bc-metric' style='font-size:0.85em; opacity:0.85;'>"
        f"{h(exit_note)} — it may differ from the "
        f"full-library Compatibility verdict above."
        f"</div></div>"
    )


@dataclass(frozen=True)
class NotEvaluatedRow:
    """One finding compatibility policy never scored (ADR-049 D1).

    ``symbol`` is raw, like :class:`ImpactEntry`'s; ``relevance``/``reason``/
    ``correlated`` are already-resolved plain strings, empty when absent.
    """

    symbol: str
    kind_value: str
    relevance: str
    reason: str
    correlated: str


@dataclass(frozen=True)
class NotEvaluatedSectionData:
    rows: tuple[NotEvaluatedRow, ...]


def render_not_evaluated_section(
    data: NotEvaluatedSectionData, demangle: bool = True
) -> str:
    rows = []
    for row in data.rows:
        # Cross-detector correlation (e.g. LAYOUT_UNVERIFIABLE annotated
        # by post_processing.AnnotateLayoutUnverifiableCoveredByVtable
        # Changed) -- this bespoke table renders a finding contract
        # evaluation excluded from every verdict section, so it never
        # runs through render_changes_table's own correlation rendering; a
        # correlated finding routed here would otherwise go right back
        # to being unexplained (Codex review, fresh evidence).
        see_also = (
            f"<div style='font-size:0.82em; color:#999; margin-top:2px;'>"
            f"🔗 See also: <code>{html.escape(row.correlated)}</code></div>"
            if row.correlated
            else ""
        )
        rows.append(
            f"<tr><td><code>{abbr_symbol_text(row.symbol, demangle)}</code></td>"
            f"<td>{html.escape(row.kind_value)}{see_also}</td>"
            f"<td>{html.escape(row.relevance)}</td><td>{html.escape(row.reason)}</td></tr>"
        )
    return (
        "<div class='section section-suppressed' id='not-evaluated'>"
        f"<h3>🔎 Not Evaluated (Contract) ({len(data.rows)})</h3>"
        "<p class='empty'>Compatibility policy did not score these findings: "
        "the selected contract domain either proved them outside the "
        "promised contract or could not resolve them. They are reported "
        "here with the reason, and contributed nothing to the verdict or "
        "the exit code above.</p>"
        "<table><thead><tr><th>Symbol</th><th>Kind</th>"
        "<th>Contract relevance</th><th>Reason</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        "</div>"
    )
