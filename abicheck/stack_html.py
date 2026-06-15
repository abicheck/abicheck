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

"""HTML report generator for stack-level dependency analysis results.

Produces a self-contained HTML report showing the dependency tree,
symbol binding status, and ABI risk for a binary's full dependency stack.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .stack_checker import StackCheckResult

# Shared page chrome (document frame, embedded stylesheet, footer).
from .html_template import render_document, render_footer

_STACK_VERDICT_STYLE = {
    "pass": ("#1b5e20", "#c8e6c9"),
    "warn": ("#e65100", "#fff3e0"),
    "fail": ("#b71c1c", "#ffcdd2"),
}

_STACK_VERDICT_ICON = {
    "pass": "\u2705",
    "warn": "\u26a0\ufe0f",
    "fail": "\u274c",
}


def stack_to_html(result: StackCheckResult) -> str:
    """Generate a self-contained HTML report for a StackCheckResult."""
    h = html.escape

    load_val = result.loadability.value
    abi_val = result.abi_risk.value
    # Use the worse of loadability/abi_risk for overall styling
    worst = "fail" if "fail" in (load_val, abi_val) else (
        "warn" if "warn" in (load_val, abi_val) else "pass"
    )
    fg, bg = _STACK_VERDICT_STYLE.get(worst, ("#212121", "#f5f5f5"))
    icon = _STACK_VERDICT_ICON.get(worst, "\u26aa")

    # Summary table
    summary_rows = [
        f"<tr><th>Root binary</th><td><code>{h(result.root_binary)}</code></td></tr>",
        f"<tr><th>Loadability</th><td>{_STACK_VERDICT_ICON.get(load_val, '')} "
        f"<strong>{h(load_val.upper())}</strong></td></tr>",
        f"<tr><th>ABI Risk</th><td>{_STACK_VERDICT_ICON.get(abi_val, '')} "
        f"<strong>{h(abi_val.upper())}</strong></td></tr>",
        f"<tr><th>Risk score</th><td><code>{h(str(result.risk_score))}</code></td></tr>",
    ]

    if result.baseline_env and result.candidate_env and result.baseline_env != result.candidate_env:
        summary_rows += [
            f"<tr><th>Baseline env</th><td><code>{h(result.baseline_env)}</code></td></tr>",
            f"<tr><th>Candidate env</th><td><code>{h(result.candidate_env)}</code></td></tr>",
        ]

    summary_html = "\n".join(summary_rows)

    # Dependency tree
    tree_lines: list[str] = []
    _render_tree_html(tree_lines, result.candidate_graph)
    tree_html = "\n".join(tree_lines) if tree_lines else "<em>Empty graph</em>"

    # Unresolved libraries
    unresolved_html = ""
    if result.candidate_graph.unresolved:
        rows = "\n".join(
            f"<tr><td><code>{h(Path(consumer).name)}</code></td>"
            f"<td><code>{h(soname)}</code></td>"
            f"<td style='color:#b71c1c'><strong>NOT FOUND</strong></td></tr>"
            for consumer, soname in result.candidate_graph.unresolved
        )
        unresolved_html = f"""<div class='section section-removed'>
  <h3>\u274c Unresolved Libraries ({len(result.candidate_graph.unresolved)})</h3>
  <table class='changes'>
    <thead><tr><th>Consumer</th><th>SONAME</th><th>Status</th></tr></thead>
    <tbody>{rows}</tbody>
  </table>
</div>"""

    # Missing symbols
    missing_html = ""
    if result.missing_symbols:
        rows = "\n".join(
            f"<tr><td><code>{h(Path(b.consumer).name)}</code></td>"
            f"<td><code>{h(b.symbol)}</code></td>"
            f"<td><code>{h(b.version or '')}</code></td>"
            f"<td>{h(b.explanation or '')}</td></tr>"
            for b in result.missing_symbols[:50]
        )
        suffix = ""
        if len(result.missing_symbols) > 50:
            suffix = (
                f"<tr><td colspan='4' style='color:#999; font-style:italic'>"
                f"... +{len(result.missing_symbols) - 50} more</td></tr>"
            )
        missing_html = f"""<div class='section section-removed'>
  <h3>\u274c Missing Symbols ({len(result.missing_symbols)})</h3>
  <table class='changes'>
    <thead><tr><th>Consumer</th><th>Symbol</th><th>Version</th><th>Explanation</th></tr></thead>
    <tbody>{rows}{suffix}</tbody>
  </table>
</div>"""

    # Binding summary
    from .stack_report import _bindings_summary
    binding_counts = _bindings_summary(result.bindings_candidate)
    binding_rows = "\n".join(
        f"<tr><td><code>{h(status)}</code></td><td>{count}</td></tr>"
        for status, count in sorted(binding_counts.items())
    )
    binding_html = f"""<div class='summary-section'>
  <h3>Symbol Binding Summary</h3>
  <table class='summary-table'>
    <thead><tr><th>Status</th><th>Count</th></tr></thead>
    <tbody>{binding_rows}</tbody>
  </table>
</div>"""

    # Stack changes
    stack_changes_html = ""
    if result.stack_changes:
        sc_rows: list[str] = []
        for sc in result.stack_changes:
            if sc.change_type == "removed":
                icon_sc = "\u274c"
                detail = "Removed from candidate"
                abi_info = "\u2014"
            elif sc.change_type == "added":
                icon_sc = "\u2795"
                detail = "New in candidate"
                abi_info = "\u2014"
            else:
                abi_verdict = sc.abi_diff.verdict.value if sc.abi_diff else "unknown"
                abi_breaking = len(sc.abi_diff.breaking) if sc.abi_diff else 0
                abi_total = len(sc.abi_diff.changes) if sc.abi_diff else 0
                icon_sc = "\u274c" if abi_verdict == "BREAKING" else (
                    "\u26a0\ufe0f" if abi_verdict in ("API_BREAK", "COMPATIBLE_WITH_RISK") else "\u2705"
                )
                detail = "Content changed"
                abi_info = f"{h(abi_verdict)} ({abi_breaking} breaking / {abi_total} total)"
            sc_rows.append(
                f"<tr><td>{icon_sc}</td>"
                f"<td><code>{h(sc.library)}</code></td>"
                f"<td>{h(sc.change_type)}</td>"
                f"<td>{detail}</td>"
                f"<td>{abi_info}</td></tr>"
            )
        stack_changes_html = f"""<div class='section section-changed'>
  <h3>Stack Changes ({len(result.stack_changes)})</h3>
  <table class='changes'>
    <thead><tr><th></th><th>Library</th><th>Change</th><th>Detail</th><th>ABI Info</th></tr></thead>
    <tbody>{chr(10).join(sc_rows)}</tbody>
  </table>
</div>"""

    body = f"""
<div class="header">
  <h1>Stack Dependency Report</h1>
  <div class="meta">
    Binary: <strong>{h(result.root_binary)}</strong> &nbsp;|&nbsp;
    Generated by <strong>abicheck</strong>
  </div>
</div>

<div class="verdict-box" style="background:{bg}; color:{fg}; border-left:6px solid {fg};">
  <h2>{icon} Risk: {h(str(result.risk_score).upper())}</h2>
  <div class="bc-metric">
    Loadability: <strong>{h(load_val.upper())}</strong>
    &nbsp;&nbsp;|&nbsp;&nbsp;
    ABI Risk: <strong>{h(abi_val.upper())}</strong>
  </div>
</div>

<div class='summary-section'>
  <h3>Summary</h3>
  <table class='summary-table'><tbody>{summary_html}</tbody></table>
</div>

<div class='summary-section'>
  <h3>Dependency Tree</h3>
  <pre style="padding:12px 16px; font-size:0.85em; overflow-x:auto;">{tree_html}</pre>
</div>

{binding_html}
{unresolved_html}
{missing_html}
{stack_changes_html}

{render_footer("Stack Dependency Report")}
"""
    return render_document(title=f"Stack Report: {h(result.root_binary)}", body=body)


def _render_tree_html(lines: list[str], graph: object) -> None:
    """Render the dependency graph as indented text for <pre> display."""
    nodes = getattr(graph, "nodes", {})
    edges = getattr(graph, "edges", [])

    root_key = None
    for key, node in nodes.items():
        if node.depth == 0:
            root_key = key
            break
    if root_key is None:
        lines.append("<em>(empty graph)</em>")
        return

    adj: dict[str, list[str]] = {}
    for consumer, provider in edges:
        adj.setdefault(consumer, []).append(provider)

    shown: set[str] = set()
    on_path: set[str] = set()
    _render_node_html(lines, nodes, adj, root_key, "", True, shown, on_path)


def _render_node_html(
    lines: list[str],
    nodes: dict[str, Any],
    adj: dict[str, list[str]],
    key: str,
    prefix: str,
    is_last: bool,
    shown: set[str],
    on_path: set[str],
) -> None:
    node = nodes.get(key)
    if node is None:
        return

    h_esc = html.escape
    connector = "\u2514\u2500\u2500 " if is_last else "\u251c\u2500\u2500 "
    reason = f" ({h_esc(node.resolution_reason)})" if node.depth > 0 and node.resolution_reason else ""
    line = f"{h_esc(prefix)}{connector}<code>{h_esc(node.soname)}</code>{reason}"

    if key in on_path:
        lines.append(f"{line} <em>(cycle)</em>")
        return
    if key in shown:
        lines.append(f"{line} <em>(already shown)</em>")
        return

    lines.append(line)
    shown.add(key)
    on_path.add(key)

    children = adj.get(key, [])
    child_prefix = prefix + ("    " if is_last else "\u2502   ")
    for i, child in enumerate(children):
        _render_node_html(lines, nodes, adj, child, child_prefix, i == len(children) - 1, shown, on_path)

    on_path.discard(key)


def write_stack_html(result: StackCheckResult, path: Path) -> None:
    """Write a Stack HTML report to *path*."""
    path.write_text(stack_to_html(result), encoding="utf-8")
