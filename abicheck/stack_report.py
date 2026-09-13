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

"""Stack report formatting — JSON and Markdown output for stack-level results.

The JSON half is now a canonical :class:`~abicheck.report.document.
ReportDocument` projection (ADR-068 D6, `one-comparison-product.md`
Phase 8): :func:`stack_to_json` is a thin
`render_json(compute_stack_report_document(result))` wrapper, and the
dict-building logic itself lives in :mod:`abicheck.report.stack`
(`report/` is the ADR-061 owner for report shapes; this module keeps
Markdown formatting only). See that module's own docstring.
"""

from __future__ import annotations

from pathlib import Path

from .binder import SymbolBinding
from .checker_types import Change
from .report.render_json import render_json
from .report.stack import (
    bindings_summary as _bindings_summary,
    compute_stack_report_document,
)
from .resolver import DependencyGraph
from .stack_checker import StackChange, StackCheckResult, StackVerdict

_VERDICT_EMOJI = {
    StackVerdict.PASS: "✅",
    StackVerdict.WARN: "⚠️",
    StackVerdict.FAIL: "❌",
}


def stack_to_json(result: StackCheckResult, indent: int = 2) -> str:
    """Render a StackCheckResult as JSON — a pure ReportDocument projection."""
    return render_json(compute_stack_report_document(result), indent=indent)


def _render_unresolved_section(lines: list[str], graph: DependencyGraph) -> None:
    """Append unresolved libraries section if any."""
    if not graph.unresolved:
        return
    lines += ["## ❌ Unresolved Libraries", ""]
    for consumer, soname in graph.unresolved:
        lines.append(f"- `{Path(consumer).name}` needs `{soname}` — **NOT FOUND**")
    lines.append("")


def _render_missing_symbols_section(
    lines: list[str], missing: list[SymbolBinding]
) -> None:
    """Append missing symbols section if any."""
    if not missing:
        return
    lines += ["## ❌ Missing Symbols", ""]
    for b in missing[:20]:
        ver = f"@{b.version}" if b.version else ""
        lines.append(
            f"- `{Path(b.consumer).name}` needs `{b.symbol}{ver}` — not found in any loaded DSO"
        )
    if len(missing) > 20:
        lines.append(f"- ... +{len(missing) - 20} more")
    lines.append("")


def _render_stack_changes_section(
    lines: list[str], stack_changes: list[StackChange]
) -> None:
    """Append stack changes section if any."""
    if not stack_changes:
        return
    lines += ["## Stack Changes", ""]
    for sc in stack_changes:
        if sc.change_type == "removed":
            lines.append(f"- ❌ **{sc.library}** — removed from candidate")
        elif sc.change_type == "added":
            lines.append(f"- ➕ **{sc.library}** — new in candidate")
        elif sc.change_type == "content_changed":
            if sc.not_comparable_reason:
                verdict = "not_comparable"
            else:
                verdict = sc.abi_diff.verdict.value if sc.abi_diff else "unknown"
            emoji = (
                "❌"
                if verdict == "BREAKING"
                else (
                    "⚠️"
                    if verdict
                    in ("API_BREAK", "COMPATIBLE_WITH_RISK", "not_comparable")
                    else "✅"
                )
            )
            lines.append(
                f"- {emoji} **{sc.library}** — content changed (ABI: `{verdict}`)"
            )
            if sc.not_comparable_reason:
                # Backtick-wrapped, matching this function's own convention
                # for other embedded free-text (e.g. the missing-symbols
                # section above) -- the reason can embed a filesystem path,
                # and an unwrapped path containing '*'/'_'/'`' would
                # otherwise corrupt this line's Markdown formatting.
                lines.append(f"  - Reason: `{sc.not_comparable_reason}`")
            if sc.abi_diff:
                conf = getattr(sc.abi_diff, "confidence", None)
                tiers = getattr(sc.abi_diff, "evidence_tiers", []) or []
                tier_str = ", ".join(f"`{t}`" for t in tiers) if tiers else "_none_"
                if conf is not None:
                    conf_val = conf.value if hasattr(conf, "value") else str(conf)
                    lines.append(
                        f"  - Confidence: **{conf_val.upper()}** | Evidence: {tier_str}"
                    )
                elif tiers:
                    lines.append(f"  - Evidence: {tier_str}")
                if sc.abi_diff.breaking:
                    for c in sc.abi_diff.breaking[:5]:
                        lines.append(f"  - `{c.kind.value}`: {c.description}")
    lines.append("")


def _render_binding_changes_section(
    lines: list[str], binding_changes: list[Change]
) -> None:
    """Append runtime binding-provider changes section if any."""
    if not binding_changes:
        return
    lines += ["## Runtime Binding Changes", ""]
    for bc in binding_changes:
        lines.append(f"- `{bc.kind.value}`: {bc.description}")
    lines.append("")


def stack_to_markdown(result: StackCheckResult) -> str:
    """Render a StackCheckResult as Markdown."""
    lines: list[str] = []

    load_emoji = _VERDICT_EMOJI[result.loadability]
    abi_emoji = _VERDICT_EMOJI[result.abi_risk]

    lines += [
        f"# Stack Report: {result.root_binary}",
        "",
        "| | |",
        "|---|---|",
        f"| **Root binary** | `{result.root_binary}` |",
        f"| **Loadability** | {load_emoji} `{result.loadability.value.upper()}` |",
        f"| **ABI risk** | {abi_emoji} `{result.abi_risk.value.upper()}` |",
        f"| **Risk score** | `{result.risk_score}` |",
        "",
    ]

    if (
        result.baseline_env
        and result.candidate_env
        and result.baseline_env != result.candidate_env
    ):
        lines += [
            "## Environments",
            "",
            f"- **Baseline**: `{result.baseline_env}`",
            f"- **Candidate**: `{result.candidate_env}`",
            "",
        ]

    graph = result.candidate_graph
    lines += ["## Dependency Tree", ""]
    _render_tree(lines, graph)
    lines.append("")

    _render_unresolved_section(lines, graph)
    _render_missing_symbols_section(lines, result.missing_symbols)

    summary = _bindings_summary(result.bindings_candidate)
    lines += [
        "## Symbol Binding Summary",
        "",
        "| Status | Count |",
        "|--------|-------|",
    ]
    for status, count in sorted(summary.items()):
        lines.append(f"| `{status}` | {count} |")
    lines.append("")

    _render_stack_changes_section(lines, result.stack_changes)
    _render_binding_changes_section(lines, result.binding_changes)

    lines += [
        "---",
        "_Generated by [abicheck](https://github.com/abicheck/abicheck)_",
    ]
    return "\n".join(lines)


def _render_tree(lines: list[str], graph: DependencyGraph) -> None:
    """Render the dependency graph as an indented tree."""
    # Find root node.
    root_key = None
    for key, node in graph.nodes.items():
        if node.depth == 0:
            root_key = key
            break
    if root_key is None:
        lines.append("_(empty graph)_")
        return

    # Build adjacency from edges.
    adj: dict[str, list[str]] = {}
    for consumer, provider in graph.edges:
        if consumer not in adj:
            adj[consumer] = []
        if provider not in adj[consumer]:
            adj[consumer].append(provider)

    shown: set[str] = set()
    on_path: set[str] = set()
    _render_node(lines, graph, adj, root_key, "", True, shown, on_path)


def _render_node(
    lines: list[str],
    graph: DependencyGraph,
    adj: dict[str, list[str]],
    key: str,
    prefix: str,
    is_last: bool,
    shown: set[str],
    on_path: set[str],
) -> None:
    """Recursively render a tree node.

    Uses *on_path* for true cycle detection (ancestor on the current recursion
    stack) and *shown* for repeat-render suppression (node already emitted from
    a different parent).
    """
    node = graph.nodes.get(key)
    if node is None:
        return

    connector = "└── " if is_last else "├── "
    reason = f" ({node.resolution_reason})" if node.depth > 0 else ""
    line = f"{prefix}{connector}`{node.soname}`{reason}"

    if key in on_path:
        lines.append(f"{line} *(cycle)*")
        return

    if key in shown:
        lines.append(f"{line} *(already shown)*")
        return

    lines.append(line)
    shown.add(key)
    on_path.add(key)

    children = adj.get(key, [])
    child_prefix = prefix + ("    " if is_last else "│   ")
    for i, child in enumerate(children):
        _render_node(
            lines,
            graph,
            adj,
            child,
            child_prefix,
            i == len(children) - 1,
            shown,
            on_path,
        )

    on_path.discard(key)
