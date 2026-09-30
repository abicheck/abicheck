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

"""HTML projection of a directory/package (release) comparison.

Input is the release summary document itself -- the mapping ``compare OLD_DIR
NEW_DIR -o json=...`` writes -- so the page can say nothing the JSON does
not, and rendering it can never change a verdict or an exit code. Sections:
the headline verdict and exit decision, one row per compared member, the
comparison scope (compared / unchecked / out of scope / proven removed /
proven added, with reasons), release-level surface changes when the document
carries them, release-coherence findings, and the recorded dependency graph.

Accessibility follows ``render_history_html``: every graph node states its
status in text as well as colour, the SVG has a title and description, each
node and edge has its own ``<title>``, and the complete edge list is a plain
table beside the picture.
"""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from ..html_template import _CSS, render_document, render_footer
from .comparison_scope import _STATE_LABEL
from .release_dependency_graph import (
    GraphNode,
    ReleaseDependencyGraph,
    compute_release_dependency_graph,
)
from .render_html_review_sections import render_surface_changes_html

_RELEASE_CSS = """
.rel-legend { display:flex; flex-wrap:wrap; gap:14px; padding:10px 16px; font-size:.85em; color:#455a64; }
.rel-svg-wrap { overflow-x:auto; padding:6px 16px 12px; }
.rel-svg text { font-family: inherit; font-size:12px; fill:#263238; }
.rel-svg .sub { font-size:10.5px; fill:#455a64; }
.rel-note { padding:8px 16px; margin:0; font-size:.88em; color:#455a64; }
"""

#: status -> (fill, stroke, dash); the status word is always drawn as text.
_STYLE = {
    "breaking": ("#ffebee", "#c62828", ""),
    "api_break": ("#fff3e0", "#ef6c00", ""),
    "compatible": ("#e8f5e9", "#2e7d32", ""),
    "no_change": ("#eceff1", "#78909c", ""),
    "unchecked": ("#fafafa", "#757575", "5 3"),
    "external": ("#ffffff", "#b0bec5", "2 3"),
}
_VERDICT_BG = {
    "BREAKING": ("#ffebee", "#b71c1c"),
    "API_BREAK": ("#fff3e0", "#e65100"),
    "COMPATIBLE_WITH_RISK": ("#fff8e1", "#8d6e00"),
    "COMPATIBLE": ("#e8f5e9", "#1b5e20"),
    "NO_CHANGE": ("#eceff1", "#37474f"),
}

_NODE_W = 176
_NODE_H = 44
_GAP_X = 24
_LAYER_H = 96
_PAD = 16


def _h(value: object) -> str:
    return html.escape(str(value))


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _headline(d: Mapping[str, Any]) -> str:
    verdict = str(d.get("verdict"))
    bg, fg = _VERDICT_BG.get(verdict, ("#eceff1", "#37474f"))
    exit_block = _mapping(d.get("exit"))
    code = exit_block.get("code", "?")
    reasons = ", ".join(str(r) for r in exit_block.get("reasons") or ()) or "none"
    outcome = _mapping(d.get("run_outcome"))
    outcome_txt = " · ".join(
        f"{_h(k)}: <strong>{_h(v)}</strong>"
        for k, v in outcome.items()
        if k != "schema_version" and v is not None
    )
    return (
        f"<div class='verdict-box' id='verdict' style='background:{bg}; color:{fg}; "
        f"border-left:6px solid {fg};'>"
        f"<h2>Release verdict: {_h(verdict)}</h2>"
        f"<div>Exit code <strong>{_h(code)}</strong> (reasons: {_h(reasons)})</div>"
        + (
            f"<div style='font-size:.85em;margin-top:4px'>{outcome_txt}</div>"
            if outcome_txt
            else ""
        )
        + "</div>"
    )


def _members(d: Mapping[str, Any]) -> str:
    rows = []
    for m in d.get("libraries") or ():
        if not isinstance(m, Mapping):
            continue
        rows.append(
            f"<tr><td><code>{_h(m.get('library'))}</code></td>"
            f"<td><strong>{_h(m.get('verdict'))}</strong></td>"
            + "".join(
                f"<td>{_h(m.get(k, 0))}</td>"
                for k in (
                    "breaking",
                    "source_breaks",
                    "risk_changes",
                    "compatible_additions",
                    "quality_issues",
                )
            )
            + "</tr>"
        )
    body = (
        "<table class='changes'><thead><tr><th>Member</th><th>Verdict</th><th>Breaking</th>"
        "<th>Source breaks</th><th>Risk</th><th>Additions</th><th>Quality</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table>"
        if rows
        else "<p class='rel-note'>No member reached a completed comparison.</p>"
    )
    unmatched = ""
    for key, label in (
        ("unmatched_old", "Only in OLD"),
        ("unmatched_new", "Only in NEW"),
    ):
        names = d.get(key) or ()
        if names:
            unmatched += (
                f"<p class='rel-note'><strong>{label}:</strong> "
                + ", ".join(f"<code>{_h(n)}</code>" for n in names)
                + "</p>"
            )
    return f"<div class='section' id='members'><h3>Members ({len(rows)} compared)</h3>{body}{unmatched}</div>"


def _scope(d: Mapping[str, Any]) -> str:
    section = d.get("comparison_scope")
    if not isinstance(section, Mapping):
        return ""
    counts = _mapping(section.get("counts"))
    old_inv = section.get("old_inventory") or {}
    new_inv = section.get("new_inventory") or {}
    summary = (
        "<table class='summary-table'><tbody>"
        f"<tr><th>Completeness</th><td><code>{_h(section.get('completeness'))}</code>"
        f" &mdash; {_h(counts.get('available', 0))} member(s) compared</td></tr>"
        f"<tr><th>Selection</th><td>{_h(section.get('selection_reason') or section.get('selection'))}</td></tr>"
        f"<tr><th>Policy</th><td><code>scope.on_incomplete: {_h(section.get('policy', 'warn'))}</code>"
        f" (contributes {_h(section.get('incomplete_scope_exit_contribution', 0))} to the exit code)</td></tr>"
        f"<tr><th>Inventory</th><td>OLD <code>{_h(old_inv.get('completeness', '?'))}</code>"
        f" ({_h(old_inv.get('provenance', ''))}); NEW <code>{_h(new_inv.get('completeness', '?'))}</code>"
        f" ({_h(new_inv.get('provenance', ''))})</td></tr>"
        "</tbody></table>"
    )
    if section.get("no_comparison_completed"):
        summary = (
            "<p class='rel-note'><strong>No comparison completed</strong> &mdash; "
            "never a clean pass.</p>" + summary
        )
    members = {
        str(m.get("name")): m
        for m in section.get("members") or ()
        if isinstance(m, Mapping)
    }
    groups = []
    for key, title in (
        ("unchecked", "Unchecked"),
        ("out_of_scope", "Out of scope"),
        ("proven_removed", "Removed (inventory-proven)"),
        ("proven_added", "Added (inventory-proven)"),
    ):
        names = [str(n) for n in section.get(key) or ()]
        if not names:
            continue
        rows = "".join(
            f"<tr><td><code>{_h(n)}</code></td>"
            f"<td>{_h(_STATE_LABEL.get(str(members.get(n, {}).get('state')), members.get(n, {}).get('state', '')))}</td>"
            f"<td>{'yes' if members.get(n, {}).get('old_present') else 'no'}</td>"
            f"<td>{'yes' if members.get(n, {}).get('new_present') else 'no'}</td>"
            f"<td>{_h(members.get(n, {}).get('reason', ''))}</td></tr>"
            for n in names
        )
        groups.append(
            f"<h4 style='padding:0 16px'>{title} ({len(names)})</h4>"
            "<table class='changes'><thead><tr><th>Member</th><th>State</th><th>In OLD</th>"
            f"<th>In NEW</th><th>Reason</th></tr></thead><tbody>{rows}</tbody></table>"
        )
    return (
        "<div class='section' id='comparison-scope'><h3>Comparison scope</h3>"
        + summary
        + "".join(groups)
        + "</div>"
    )


def _bundle(d: Mapping[str, Any]) -> str:
    findings = [f for f in d.get("bundle_findings") or () if isinstance(f, Mapping)]
    if not findings:
        return ""
    rows = "".join(
        f"<tr><td>{_h(f.get('kind'))}</td><td><code>{_h(f.get('symbol'))}</code></td>"
        f"<td>{_h(f.get('consumer_library') or '')}</td><td>{_h(f.get('provider_library') or '')}</td>"
        f"<td>{_h(f.get('description') or '')}</td></tr>"
        for f in findings
    )
    return (
        f"<div class='section' id='bundle'><h3>Release coherence ({_h(d.get('bundle_verdict'))}, "
        f"{len(findings)} finding(s))</h3><table class='changes'><thead><tr><th>Kind</th>"
        "<th>Symbol</th><th>Consumer</th><th>Provider</th><th>Detail</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _node_svg(node: GraphNode, x: float, y: float) -> str:
    fill, stroke, dash = _STYLE.get(node.status, ("#ffffff", "#90a4ae", ""))
    label = node.name if len(node.name) <= 24 else node.name[:23] + "…"
    status = node.status.replace("_", " ")
    dash_attr = f" stroke-dasharray='{dash}'" if dash else ""
    return (
        f"<g class='rel-node' data-name='{_h(node.name)}'><title>{_h(node.name)}: {_h(status)} "
        f"({_h(node.detail)})</title>"
        f"<rect x='{x:.1f}' y='{y:.1f}' width='{_NODE_W}' height='{_NODE_H}' rx='6' "
        f"fill='{fill}' stroke='{stroke}' stroke-width='2'{dash_attr}/>"
        f"<text x='{x + _NODE_W / 2:.1f}' y='{y + 18:.1f}' text-anchor='middle'>{_h(label)}</text>"
        f"<text class='sub' x='{x + _NODE_W / 2:.1f}' y='{y + 34:.1f}' text-anchor='middle'>{_h(status)}</text>"
        "</g>"
    )


def _graph_svg(graph: ReleaseDependencyGraph) -> str:
    layers = sorted({n.layer for n in graph.nodes}, reverse=True)
    rows = {layer: [n for n in graph.nodes if n.layer == layer] for layer in layers}
    widest = max(len(r) for r in rows.values())
    width = int(2 * _PAD + widest * (_NODE_W + _GAP_X))
    height = int(2 * _PAD + len(layers) * _LAYER_H - (_LAYER_H - _NODE_H))
    pos: dict[str, tuple[float, float]] = {}
    for i, layer in enumerate(layers):
        row = rows[layer]
        offset = (width - len(row) * (_NODE_W + _GAP_X) + _GAP_X) / 2
        for j, node in enumerate(row):
            pos[node.name] = (offset + j * (_NODE_W + _GAP_X), _PAD + i * _LAYER_H)
    edges = []
    for e in graph.edges:
        (sx, sy), (tx, ty) = pos[e.source], pos[e.target]
        x1, y1 = sx + _NODE_W / 2, sy + _NODE_H
        x2, y2 = tx + _NODE_W / 2, ty
        if ty <= sy:  # a cycle or same-layer relation: route around the side
            x1, y1, x2, y2 = (
                sx + _NODE_W,
                sy + _NODE_H / 2,
                tx + _NODE_W,
                ty + _NODE_H / 2,
            )
            path = f"M{x1:.1f} {y1:.1f} C{x1 + 40:.1f} {y1:.1f} {x2 + 40:.1f} {y2:.1f} {x2:.1f} {y2:.1f}"
        elif (
            ty - sy > _LAYER_H
        ):  # skips a layer: bow sideways, clear of the boxes between
            side = 1 if x2 >= x1 else -1
            bow = side * (_NODE_W * 0.6 + _GAP_X)
            x1 = sx + (_NODE_W if side > 0 else 0)
            y1 = sy + _NODE_H / 2
            path = (
                f"M{x1:.1f} {y1:.1f} C{x1 + bow:.1f} {y1 + _LAYER_H:.1f} "
                f"{x2 + bow:.1f} {y2 - _LAYER_H / 2:.1f} {x2:.1f} {y2 - 4:.1f}"
            )
        else:
            mid = (y1 + y2) / 2
            path = f"M{x1:.1f} {y1:.1f} C{x1:.1f} {mid:.1f} {x2:.1f} {mid:.1f} {x2:.1f} {y2 - 4:.1f}"
        dash = "" if e.sides == "old+new" else " stroke-dasharray='6 4'"
        edges.append(
            f"<path class='rel-edge' d='{path}' fill='none' stroke='#78909c' stroke-width='1.5'"
            f"{dash} marker-end='url(#rel-arrow)'><title>{_h(e.source)} needs {_h(e.target)} "
            f"(recorded on {_h(e.sides)})</title></path>"
        )
    nodes = "".join(_node_svg(n, *pos[n.name]) for n in graph.nodes)
    return (
        f"<svg class='rel-svg' role='img' aria-labelledby='rel-title rel-desc' width='{width}' "
        f"height='{height}' viewBox='0 0 {width} {height}'>"
        f"<title id='rel-title'>Dependency graph of {len(graph.nodes)} libraries and "
        f"{len(graph.edges)} recorded dependencies</title>"
        "<desc id='rel-desc'>Each box is a release member or a library a member needs; its "
        "second line states its status. An arrow points from a library to one it needs, "
        "as recorded in its dynamic section; a dashed arrow was recorded on one side only. "
        "Libraries that need nothing recorded are at the bottom. The table below lists "
        "every drawn dependency.</desc>"
        "<defs><marker id='rel-arrow' viewBox='0 0 10 10' refX='9' refY='5' markerWidth='7' "
        "markerHeight='7' orient='auto-start-reverse'><path d='M0 0 L10 5 L0 10 z' fill='#78909c'/>"
        "</marker></defs>" + "".join(edges) + nodes + "</svg>"
    )


def _graph_section(d: Mapping[str, Any]) -> str:
    graph = compute_release_dependency_graph(d)
    legend = (
        "<div class='rel-legend'>"
        + "".join(
            f"<span><svg width='14' height='14'><rect x='1' y='1' width='12' height='12' rx='2' "
            f"fill='{fill}' stroke='{stroke}' stroke-width='2'"
            + (f" stroke-dasharray='{dash}'" if dash else "")
            + f"/></svg> {status.replace('_', ' ')}</span>"
            for status, (fill, stroke, dash) in _STYLE.items()
        )
        + "<span>solid arrow: recorded on both sides · dashed: one side only</span></div>"
    )
    notes = [
        f"{graph.members_with_facts} of {graph.members_total} compared member(s) "
        "recorded dependency facts (ELF dynamic section)."
    ]
    if graph.omitted_nodes or graph.omitted_edges:
        notes.append(
            f"{graph.omitted_nodes} more librar(ies) and {graph.omitted_edges} more "
            "dependenc(ies) are not drawn or listed here; the JSON report records every one."
        )
    note_html = "".join(f"<p class='rel-note'>{_h(n)}</p>" for n in notes)
    if not graph.nodes:
        return (
            "<div class='section' id='dependency-graph'><h3>Dependency graph</h3>"
            f"{note_html}<p class='rel-note'>No libraries to draw.</p></div>"
        )
    table = (
        "<table class='changes' id='dependency-edges'><thead><tr><th>Library</th><th>Needs</th>"
        "<th>Needed library status</th><th>Recorded on</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td><code>{_h(e.source)}</code></td><td><code>{_h(e.target)}</code></td>"
            f"<td>{_h(next(n.status for n in graph.nodes if n.name == e.target).replace('_', ' '))}</td>"
            f"<td>{_h(e.sides)}</td></tr>"
            for e in graph.edges
        )
        + "</tbody></table>"
        if graph.edges
        else "<p class='rel-note'>No dependency between drawn libraries is recorded.</p>"
    )
    return (
        "<div class='section' id='dependency-graph'><h3>Dependency graph</h3>"
        f"{legend}<div class='rel-svg-wrap'>{_graph_svg(graph)}</div>{note_html}"
        f"<h4 style='padding:0 16px'>Recorded dependencies</h4>{table}</div>"
    )


def render_release_html(document: Mapping[str, Any]) -> str:
    """The complete HTML page for one release comparison document."""
    old_dir = _h(document.get("old_dir", ""))
    new_dir = _h(document.get("new_dir", ""))
    surface = render_surface_changes_html(document.get("surface_changes"))
    warnings = [str(w) for w in document.get("warnings") or ()]
    warn_html = (
        "<div class='section' id='warnings'><h3>Warnings</h3><ul>"
        + "".join(f"<li>{_h(w)}</li>" for w in warnings)
        + "</ul></div>"
        if warnings
        else ""
    )
    body = f"""
<div class="header">
  <h1>Release ABI Compatibility Report</h1>
  <div class="meta">{old_dir} → {new_dir} &nbsp;|&nbsp; release schema {_h(document.get("release_schema_version", "?"))}</div>
</div>
{_headline(document)}
{_members(document)}
{_scope(document)}
{surface}
{_graph_section(document)}
{_bundle(document)}
{warn_html}
{render_footer("release comparison")}
"""
    return render_document(
        title=f"Release ABI Report: {old_dir} → {new_dir}",
        body=body,
        css=_CSS + _RELEASE_CSS,
    )
