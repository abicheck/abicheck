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

"""Sprint 9: ABICC-compatible HTML report generator.

Generates a self-contained HTML report that mirrors the structure of
abi-compliance-checker (ABICC) reports:

  - Verdict banner (BREAKING / COMPATIBLE / NO_CHANGE)
  - Binary Compatibility % metric (based on old exported symbol count)
  - Summary table: changes by category (functions, variables, types, enums, ELF)
  - Sectioned changes: Removed | Changed | Added (with anchored navigation)
  - Suppressed changes section (if any)
  - Demangled symbol names as display text, mangled as tooltip

No external CSS/JS dependencies — fully self-contained single HTML file.
"""

from __future__ import annotations

import html
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, cast

from .checker_policy import HasKind

# Page chrome (DOCTYPE/head/stylesheet/body frame, verdict palette, footer) now
# lives in one shared seam (``html_template``). ``_VERDICT_STYLE`` /
# ``render_document`` / ``render_footer`` are used below; ``_CSS`` is re-exported
# via redundant alias (it was previously defined in this module) so any code that
# imported it from here keeps working.
from .html_template import (
    _CSS as _CSS,
)
from .html_template import (
    _VERDICT_STYLE,
    render_document,
    render_footer,
)
from .report_classifications import (
    ADDED_KINDS,
    CATEGORY_PREFIXES,
    REMOVED_KINDS,
    category,
    is_symbol_problem,
    is_type_problem,
    kind_str,
    severity,
)
from .report_summary import compatibility_metrics

if TYPE_CHECKING:
    from .checker import DiffResult


def _change_bucket(change: object) -> str:
    """Classify a change into 'removed', 'added', or 'changed'."""
    ks = kind_str(change)
    if ks in REMOVED_KINDS:
        return "removed"
    if ks in ADDED_KINDS:
        return "added"
    return "changed"


# ---------------------------------------------------------------------------
# HTML generation helpers
# ---------------------------------------------------------------------------


def _file_metadata_html(result: object) -> str:
    """Render library file metadata (path, SHA-256, size) as an HTML table."""
    old_meta = getattr(result, "old_metadata", None)
    new_meta = getattr(result, "new_metadata", None)
    if not old_meta and not new_meta:
        return ""
    h = html.escape

    def _row(label: str, old_val: str, new_val: str) -> str:
        return f"<tr><th>{label}</th><td>{h(old_val)}</td><td>{h(new_val)}</td></tr>"

    old_path = getattr(old_meta, "path", "—") if old_meta else "—"
    new_path = getattr(new_meta, "path", "—") if new_meta else "—"
    old_sha = getattr(old_meta, "sha256", "—") if old_meta else "—"
    new_sha = getattr(new_meta, "sha256", "—") if new_meta else "—"
    old_size = str(getattr(old_meta, "size_bytes", 0)) if old_meta else "—"
    new_size = str(getattr(new_meta, "size_bytes", 0)) if new_meta else "—"

    return f"""<div class='summary-section'>
  <h3>Library Files</h3>
  <table class='summary-table'>
    <thead><tr><th></th><th>Old</th><th>New</th></tr></thead>
    <tbody>
      {_row("Path", old_path, new_path)}
      {_row("SHA-256", old_sha[:16] + "…", new_sha[:16] + "…")}
      {_row("Size (bytes)", old_size, new_size)}
    </tbody>
  </table>
</div>"""


def _symbol_cell(change: object) -> str:
    """Render symbol name: demangled text with mangled name as tooltip."""
    h = html.escape
    mangled = h(getattr(change, "symbol", "") or "")
    demangled = h(getattr(change, "demangled_symbol", "") or mangled)
    if demangled and demangled != mangled and mangled:
        return f"<abbr title=\"{html.escape(mangled, quote=True)}\">{demangled}</abbr>"
    return demangled or mangled


def _changes_table(changes: list[object]) -> str:
    if not changes:
        return "<p class='empty'>No changes in this category.</p>"

    from .checker_policy import impact_for as _impact_for

    rows = []
    for ch in changes:
        ks = kind_str(ch)
        cat = category(ks)
        desc = html.escape(getattr(ch, "description", "") or "")
        old_val = html.escape(str(getattr(ch, "old_value", "") or ""))
        new_val = html.escape(str(getattr(ch, "new_value", "") or ""))
        sym_cell = _symbol_cell(ch)
        loc = getattr(ch, "source_location", None)
        affected = getattr(ch, "affected_symbols", None)

        # Build extended description with impact + affected + location
        desc_parts = [desc]
        kind = getattr(ch, "kind", None)
        if kind:
            impact = _impact_for(kind)
            if impact:
                desc_parts.append(
                    f"<div style='font-size:0.85em; color:#666; margin-top:3px;'>"
                    f"💡 {html.escape(impact)}</div>"
                )
        if affected:
            names = ", ".join(html.escape(s) for s in affected[:5])
            suffix = f" (+{len(affected) - 5} more)" if len(affected) > 5 else ""
            desc_parts.append(
                f"<div style='font-size:0.82em; color:#1565c0; margin-top:2px;'>"
                f"📎 Affected: <code>{names}</code>{suffix}</div>"
            )
        if loc:
            desc_parts.append(
                f"<div style='font-size:0.82em; color:#999; margin-top:2px;'>"
                f"📍 {html.escape(loc)}</div>"
            )
        caused_count = getattr(ch, "caused_count", 0)
        if caused_count > 0:
            desc_parts.append(
                f"<div style='font-size:0.82em; color:#e65100; margin-top:2px;'>"
                f"🔗 {caused_count} derived change(s) collapsed</div>"
            )
        full_desc = "".join(desc_parts)

        rows.append(
            f"<tr>"
            f"<td><span class='kind-badge'>{html.escape(ks)}</span></td>"
            f"<td class='sym'>{sym_cell}</td>"
            f"<td><span class='cat-badge'>{html.escape(cat)}</span></td>"
            f"<td>{full_desc}</td>"
            f"<td>{old_val}</td>"
            f"<td>{new_val}</td>"
            f"</tr>"
        )

    body = "\n".join(rows)
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


def _summary_table(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed_count: int,
) -> str:
    """Build category-level summary table (mirrors ABICC's overview section)."""

    # Count by category
    cats: dict[str, dict[str, int]] = {}
    for label, _ in CATEGORY_PREFIXES:
        cats[label] = {"removed": 0, "changed": 0, "added": 0}
    cats["Other"] = {"removed": 0, "changed": 0, "added": 0}

    for ch in removed:
        cats[category(kind_str(ch))]["removed"] += 1
    for ch in changed:
        cats[category(kind_str(ch))]["changed"] += 1
    for ch in added:
        cats[category(kind_str(ch))]["added"] += 1

    rows = []
    for label in [lbl for lbl, _ in CATEGORY_PREFIXES] + ["Other"]:
        c = cats[label]
        if c["removed"] == 0 and c["changed"] == 0 and c["added"] == 0:
            continue
        r = f"<span class='num num-red'>{c['removed']}</span>" if c["removed"] else "—"
        ch_n = (
            f"<span class='num num-blue'>{c['changed']}</span>" if c["changed"] else "—"
        )
        a = f"<span class='num num-green'>{c['added']}</span>" if c["added"] else "—"
        rows.append(
            f"<tr><td>{html.escape(label)}</td><td>{r}</td><td>{ch_n}</td><td>{a}</td></tr>"
        )

    total_r = f"<span class='num num-red'>{len(removed)}</span>"
    total_ch = f"<span class='num num-blue'>{len(changed)}</span>"
    total_a = f"<span class='num num-green'>{len(added)}</span>"
    rows.append(
        f"<tr style='border-top:2px solid #e0e0e0; font-weight:bold;'>"
        f"<td>Total</td><td>{total_r}</td><td>{total_ch}</td><td>{total_a}</td></tr>"
    )

    if suppressed_count:
        rows.append(
            f"<tr><td colspan='4' style='color:#6a1b9a; font-size:0.85em; padding:6px 12px;'>"
            f"ℹ️ {suppressed_count} change(s) suppressed by suppression file</td></tr>"
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


def _nav_bar(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed_count: int,
) -> str:
    links = []
    if removed:
        links.append(
            f"<a href='#removed' class='breaking'>⛔ Removed ({len(removed)})</a>"
        )
    if changed:
        links.append(
            f"<a href='#changed' class='breaking'>⚠️ Changed ({len(changed)})</a>"
        )
    if added:
        links.append(f"<a href='#added' class='added'>✅ Added ({len(added)})</a>")
    if suppressed_count:
        links.append(f"<a href='#suppressed'>🔕 Suppressed ({suppressed_count})</a>")
    if not links:
        return ""
    return "<div class='nav'>" + "".join(links) + "</div>"


# ---------------------------------------------------------------------------
# ABICC-compatible HTML (compat_html mode)
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


def _compat_changes_table(items: list[object], show_severity: bool = False) -> str:
    """Render a changes table in ABICC style."""
    if not items:
        return "<p>No changes.</p>"
    h = html.escape
    rows = []
    for ch in items:
        ks = kind_str(ch)
        sym = h(getattr(ch, "symbol", "") or "")
        desc = h(getattr(ch, "description", "") or "")
        old_val = h(str(getattr(ch, "old_value", "") or ""))
        new_val = h(str(getattr(ch, "new_value", "") or ""))
        sev_cell = f"<td>{severity(ks)}</td>" if show_severity else ""
        rows.append(
            f"<tr><td class='sym'>{sym}</td><td>{h(ks)}</td>"
            f"{sev_cell}<td>{desc}</td><td>{old_val}</td><td>{new_val}</td></tr>"
        )
    sev_hdr = "<th>Severity</th>" if show_severity else ""
    return (
        f"<table class='problem'><thead><tr>"
        f"<th>Symbol</th><th>Kind</th>{sev_hdr}"
        f"<th>Description</th><th>Old</th><th>New</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _generate_compat_html(
    result: object,
    changes: list[object],
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed: list[object],
    suppressed_count: int,
    bc_pct: float,
    affected_pct: float,
    breaking_count: int,
    verdict: str,
    lib_display: str,
    old_display: str,
    new_display: str,
    old_symbol_count: int | None,
    title: str | None,
    report_kind: str = "binary",
) -> str:
    """Generate ABICC-compatible HTML with matching element IDs and structure.

    Produces HTML with the same DOM IDs and section structure that ABICC
    report parsers expect: #Title, #Summary, #Added, #Removed,
    #TypeProblems_High, etc.

    Also embeds the META_DATA comment that ABICC includes for machine parsing.

    Args:
        report_kind: "binary" or "source" — controls title, META_DATA kind, and
            section ID prefixes to match ABICC's per-kind report structure.
    """
    h = html.escape

    # Classify type vs symbol vs other problems by severity.
    # "other" catches ELF-layer kinds (soname_, symbol_, needed_, rpath_, …)
    # that are neither type-scoped nor symbol/interface-scoped.
    type_problems: dict[str, list[object]] = {"High": [], "Medium": [], "Low": []}
    symbol_problems: dict[str, list[object]] = {"High": [], "Medium": [], "Low": []}
    other_problems: dict[str, list[object]] = {"High": [], "Medium": [], "Low": []}
    for ch in changed:
        ks = kind_str(ch)
        sev = severity(ks)
        if is_type_problem(ks):
            type_problems[sev].append(ch)
        elif is_symbol_problem(ks):
            symbol_problems[sev].append(ch)
        else:
            other_problems[sev].append(ch)

    # Counts for META_DATA
    tp_high = len(type_problems["High"])
    tp_med = len(type_problems["Medium"])
    tp_low = len(type_problems["Low"])
    sp_high = len(symbol_problems["High"])
    sp_med = len(symbol_problems["Medium"])
    sp_low = len(symbol_problems["Low"])

    compat_verdict = (
        "incompatible" if verdict in ("BREAKING", "API_BREAK") else "compatible"
    )
    bc_css = (
        "incompatible" if bc_pct < 90 else ("warning" if bc_pct < 100 else "compatible")
    )
    affected_pct_label = f"{affected_pct:.1f}" if old_symbol_count else "0"

    kind_label = report_kind.capitalize()  # "Binary" or "Source"

    # META_DATA comment (semicolon-delimited, matches ABICC format)
    meta_data = (
        f"verdict:{compat_verdict};kind:{report_kind};"
        f"affected:{affected_pct_label};"
        f"added:{len(added)};removed:{len(removed)};"
        f"type_problems_high:{tp_high};"
        f"type_problems_medium:{tp_med};"
        f"type_problems_low:{tp_low};"
        f"interface_problems_high:{sp_high};"
        f"interface_problems_medium:{sp_med};"
        f"interface_problems_low:{sp_low};"
        f"changed_constants:0;"
        f"tool_version:abicheck"
    )

    # Build title matching ABICC convention
    abicc_title = (
        h(title)
        if title
        else f"{kind_label} compatibility report for the <b>{lib_display}</b> "
        f"library between <b>{old_display}</b> and <b>{new_display}</b> versions"
    )

    # Build sections
    sections_html = []

    # Problem Summary
    sections_html.append(f"""
<div id='Summary'>
<h2>Test Info</h2>
<table class='summary'>
<tr><th>Library Name</th><td>{lib_display}</td></tr>
<tr><th>Version #1</th><td>{old_display}</td></tr>
<tr><th>Version #2</th><td>{new_display}</td></tr>
</table>
{_file_metadata_html(result)}

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
<tr><th>Added Symbols</th><td colspan='3'>{len(added)}</td></tr>
<tr><th>Removed Symbols</th><td colspan='3'>{len(removed)}</td></tr>
</table>
</div>""")

    # Added symbols section
    if added:
        sections_html.append(f"""
<div id='Added'>
<h2>Added Symbols ({len(added)})</h2>
{_compat_changes_table(added)}
</div>""")

    # Removed symbols section
    if removed:
        sections_html.append(f"""
<div id='Removed'>
<h2>Removed Symbols ({len(removed)})</h2>
{_compat_changes_table(removed)}
</div>""")

    # Type problems by severity
    for sev in ("High", "Medium", "Low"):
        items = type_problems[sev]
        if items:
            sections_html.append(f"""
<div id='TypeProblems_{sev}'>
<h2>Problems with Data Types — {sev} Severity ({len(items)})</h2>
{_compat_changes_table(items, show_severity=True)}
</div>""")

    # Interface (symbol) problems by severity
    for sev in ("High", "Medium", "Low"):
        items = symbol_problems[sev]
        if items:
            sections_html.append(f"""
<div id='InterfaceProblems_{sev}'>
<h2>Problems with Symbols — {sev} Severity ({len(items)})</h2>
{_compat_changes_table(items, show_severity=True)}
</div>""")

    # Other problems (ELF-layer: soname, symbol versioning, calling convention, etc.)
    other_all = other_problems["High"] + other_problems["Medium"] + other_problems["Low"]
    if other_all:
        sections_html.append(f"""
<div id='OtherProblems'>
<h2>Other Problems ({len(other_all)})</h2>
{_compat_changes_table(other_all, show_severity=True)}
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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _confidence_html(result: object) -> str:
    """Render confidence, evidence tiers, and coverage warnings as HTML."""
    conf = getattr(result, "confidence", None)
    if conf is None:
        return ""
    conf_val = conf.value if hasattr(conf, "value") else str(conf)
    tiers = getattr(result, "evidence_tiers", []) or []
    cov_warns = getattr(result, "coverage_warnings", []) or []
    policy = getattr(result, "policy", "strict_abi") or "strict_abi"
    policy_file = getattr(result, "policy_file", None)

    h = html.escape

    # Confidence colour
    conf_color = {"high": "#1b5e20", "medium": "#e65100", "low": "#b71c1c"}.get(
        conf_val, "#212121"
    )
    tier_badges = " ".join(
        f"<span class='kind-badge'>{h(t)}</span>" for t in tiers
    ) if tiers else "<em>none</em>"

    rows = [
        f"<tr><th>Confidence</th>"
        f"<td><strong style='color:{conf_color}'>{h(conf_val.upper())}</strong></td></tr>",
        f"<tr><th>Evidence tiers</th><td>{tier_badges}</td></tr>",
        f"<tr><th>Policy</th><td><code>{h(policy)}</code></td></tr>",
    ]
    if policy_file and getattr(policy_file, "overrides", None):
        overrides = ", ".join(
            f"<code>{h(k.value)}</code>&nbsp;→&nbsp;<code>{h(v.value)}</code>"
            for k, v in policy_file.overrides.items()
        )
        rows.append(f"<tr><th>Policy overrides</th><td>{overrides}</td></tr>")
    for w in cov_warns:
        rows.append(f"<tr><th>Coverage gap</th><td>{h(w)}</td></tr>")

    body = "\n".join(rows)
    return (
        f"<div class='summary-section'>"
        f"<h3>🔍 Analysis Confidence</h3>"
        f"<table class='summary-table'><tbody>{body}</tbody></table>"
        f"</div>"
    )


def _build_impact_html(
    result: DiffResult,
    displayed_changes: list[object] | None = None,
) -> str:
    """Build an HTML impact summary table.

    When *displayed_changes* is given, only those changes are considered.
    Interface counts use unique ``affected_symbols``; ``caused_count`` is
    shown separately to avoid double-counting.
    """
    from .checker import _ROOT_TYPE_CHANGE_KINDS

    root_entries: list[tuple[str, str, int, int]] = []
    changes = displayed_changes if displayed_changes is not None else (getattr(result, "changes", []) or [])
    for c in changes:
        kind = getattr(c, "kind", None)
        if kind and kind in _ROOT_TYPE_CHANGE_KINDS:
            affected = getattr(c, "affected_symbols", None)
            affected_count = len(affected) if affected else 0
            caused = getattr(c, "caused_count", 0)
            if affected_count > 0 or caused > 0:
                symbol = getattr(c, "symbol", "")
                root_entries.append((symbol, kind.value, affected_count, caused))

    if not root_entries:
        return ""

    rows = []
    for symbol, kind_val, iface_count, caused in root_entries:
        iface_str = f"{iface_count} interface(s)" if iface_count > 0 else "—"
        caused_str = f" (+{caused} collapsed)" if caused > 0 else ""
        rows.append(
            f"<tr><td><code>{html.escape(symbol)}</code></td>"
            f"<td><span class='kind-badge'>{html.escape(kind_val)}</span></td>"
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


def generate_html_report(
    result: DiffResult,
    lib_name: str = "",
    old_version: str = "",
    new_version: str = "",
    old_symbol_count: int | None = None,
    title: str | None = None,
    compat_html: bool = False,
    report_kind: str = "binary",
    *,
    show_only: str | None = None,
    show_impact: bool = False,
) -> str:
    """Generate a standalone ABICC-compatible HTML ABI report.

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

    Returns:
        Complete self-contained HTML document as a string.
    """
    verdict = (
        result.verdict.value
        if hasattr(result.verdict, "value")
        else str(result.verdict)
    )
    fg, bg = _VERDICT_STYLE.get(verdict, ("#212121", "#f5f5f5"))

    all_changes: list[object] = list(getattr(result, "changes", None) or [])

    # Apply show_only filter (display-only, does not affect metrics)
    if show_only:
        from .checker import Change as _Change
        from .reporter import apply_show_only
        typed_changes = [c for c in all_changes if isinstance(c, _Change)]
        filtered = apply_show_only(typed_changes, show_only, policy=result.policy)
        display_changes: list[object] = list(filtered)
    else:
        display_changes = all_changes

    suppressed: list[object] = list(getattr(result, "suppressed_changes", None) or [])
    suppressed_count: int = getattr(result, "suppressed_count", len(suppressed))

    # Split display changes into buckets (for rendering)
    removed = [ch for ch in display_changes if _change_bucket(ch) == "removed"]
    added = [ch for ch in display_changes if _change_bucket(ch) == "added"]
    changed = [ch for ch in display_changes if _change_bucket(ch) == "changed"]

    # Metrics always use the full (unfiltered) change list
    metrics = compatibility_metrics(cast(list[HasKind], all_changes), old_symbol_count)
    breaking_count = metrics.breaking_count
    bc_pct = metrics.binary_compatibility_pct
    affected_pct = metrics.affected_pct

    h = html.escape
    lib_display = h(lib_name) if lib_name else "library"
    old_display = h(old_version) if old_version else "old"
    new_display = h(new_version) if new_version else "new"

    if compat_html:
        return _generate_compat_html(
            result,
            display_changes,
            removed,
            changed,
            added,
            suppressed,
            suppressed_count,
            bc_pct,
            affected_pct,
            breaking_count,
            verdict,
            lib_display,
            old_display,
            new_display,
            old_symbol_count,
            title,
            report_kind=report_kind,
        )

    verdict_icon = _verdict_icon(verdict)

    summary_html = _summary_table(removed, changed, added, suppressed_count)
    nav_html = _nav_bar(removed, changed, added, suppressed_count)

    def _section(title: str, anchor: str, css_class: str, items: list[object]) -> str:
        count = len(items)
        tbl = _changes_table(items)
        return (
            f"<div class='section {css_class}' id='{anchor}'>"
            f"<h3>{title} ({count})</h3>"
            f"{tbl}"
            f"</div>"
        )

    sections = _build_sections_html(
        removed, changed, added, suppressed, suppressed_count, _section,
    )

    if not sections:
        if show_only and all_changes:
            sections.append(
                "<div class='section'><p class='empty'>"
                f"No changes match the current filter (<code>--show-only {h(show_only)}</code>). "
                f"{len(all_changes)} change(s) exist but are excluded by the filter."
                "</p></div>"
            )
        else:
            sections.append(
                "<div class='section'><p class='empty'>"
                "No ABI changes detected between the two versions."
                "</p></div>"
            )

    sections_html = "\n".join(sections)

    symbol_count_note = f" / {old_symbol_count} exported symbols" if old_symbol_count else ""

    redundant_count = getattr(result, "redundant_count", 0)
    redundancy_note = ""
    if redundant_count > 0:
        redundancy_note = (
            f"<div class='section' style='background:#fff3e0; padding:10px; border-left:4px solid #ff9800;'>"
            f"<strong>ℹ️ {redundant_count} redundant change(s)</strong> hidden "
            f"(derived from root type changes). Use <code>--show-redundant</code> to show all."
            f"</div>"
        )

    filter_note = ""
    if show_only:
        filter_note = (
            f"<div class='section' style='background:#e3f2fd; padding:10px; border-left:4px solid #1976d2;'>"
            f"<strong>🔍 Filtered by:</strong> <code>--show-only {h(show_only)}</code> "
            f"({len(display_changes)} of {len(all_changes)} changes shown)"
            f"</div>"
        )

    impact_html = ""
    if show_impact:
        impact_html = _build_impact_html(result, displayed_changes=display_changes)

    body = f"""
<div class="header">
  <h1>{h(title) if title else f"ABI Compatibility Report — {lib_display}"}</h1>
  <div class="meta">
    {old_display} → {new_display} &nbsp;|&nbsp;
    Generated by <strong>abicheck</strong> (ABICC-compatible)
  </div>
  {_file_metadata_html(result)}
</div>

<div class="verdict-box" style="background:{bg}; color:{fg}; border-left:6px solid {fg};">
  <h2>{verdict_icon} Verdict: {h(verdict)}</h2>
  <div class="bc-metric">
    Binary Compatibility: <strong>{bc_pct:.1f}%</strong>
    <span style="font-size:0.82em; opacity:0.75">
      ({breaking_count} breaking change(s){symbol_count_note})
    </span>
    &nbsp;&nbsp;
    <span style="font-size:0.85em;">
      Removed: <strong>{len(removed)}</strong>
      &nbsp;|&nbsp; Changed: <strong>{len(changed)}</strong>
      &nbsp;|&nbsp; Added: <strong>{len(added)}</strong>
    </span>
  </div>
</div>

{_confidence_html(result)}
{nav_html}
{summary_html}
{filter_note}
{redundancy_note}
{sections_html}
{impact_html}

{render_footer("ABICC-compatible report format")}
"""
    return render_document(
        title=h(title) if title else f"ABI Report: {lib_display} {old_display} → {new_display}",
        body=body,
    )


def _verdict_icon(verdict: str) -> str:
    """Return emoji icon for verdict."""
    return {
        "BREAKING": "🔴",
        "COMPATIBLE": "🟢",
        "COMPATIBLE_WITH_RISK": "🟠",
        "NO_CHANGE": "🔵",
        "API_BREAK": "🟠",
    }.get(verdict, "⚪")


def _build_sections_html(
    removed: list[object],
    changed: list[object],
    added: list[object],
    suppressed: list[object],
    suppressed_count: int,
    section_builder: Callable[[str, str, str, list[object]], str],
) -> list[str]:
    """Build ordered section blocks for HTML report body."""
    sections: list[str] = []
    for title, anchor, css_class, items in (
        ("⛔ Removed Symbols", "removed", "section-removed", removed),
        ("⚠️ Changed Symbols", "changed", "section-changed", changed),
        ("✅ Added Symbols", "added", "section-added", added),
        ("🔕 Suppressed Changes", "suppressed", "section-suppressed", suppressed),
    ):
        if items:
            sections.append(section_builder(title, anchor, css_class, items))
    if suppressed_count and not suppressed:
        sections.append(
            f"<div class='section section-suppressed' id='suppressed'>"
            f"<h3>🔕 Suppressed Changes ({suppressed_count})</h3>"
            f"<p class='empty'>Details not available (suppressed_changes list is empty).</p>"
            f"</div>"
        )
    return sections


def write_html_report(
    result: DiffResult,
    output_path: Path,
    lib_name: str = "",
    old_version: str = "",
    new_version: str = "",
    old_symbol_count: int | None = None,
    title: str | None = None,
    compat_html: bool = False,
    report_kind: str = "binary",
) -> None:
    """Write HTML report to *output_path*, creating parent directories as needed."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = generate_html_report(
        result,
        lib_name=lib_name,
        old_version=old_version,
        new_version=new_version,
        old_symbol_count=old_symbol_count,
        title=title,
        compat_html=compat_html,
        report_kind=report_kind,
    )
    output_path.write_text(content, encoding="utf-8")
