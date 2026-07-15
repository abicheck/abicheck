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

"""Stack report formatting — JSON and Markdown output for stack-level results."""
from __future__ import annotations

import json
from pathlib import Path

from .binder import SymbolBinding
from .checker_types import Change
from .resolver import DependencyGraph
from .stack_checker import StackChange, StackCheckResult, StackVerdict

_VERDICT_EMOJI = {
    StackVerdict.PASS: "✅",
    StackVerdict.WARN: "⚠️",
    StackVerdict.FAIL: "❌",
}

# Cap on embedded per-library findings in stack JSON — mirrors
# `cli_scan_baseline._MAX_BASELINE_FINDINGS`'s rationale: a large diff must
# not blow up the always-on stack-check output, but a bare count
# (`abi_breaking: 3`) leaves no way to tell *which* symbols broke without a
# separate `compare` run.
_MAX_STACK_FINDINGS_PER_LIBRARY = 10


def _stack_finding_dicts(diff: object) -> list[dict[str, object]]:
    """Project a library's gating findings (breaking/api_break/risk) into
    small, capped dicts — same shape as `cli_scan_baseline._baseline_finding_dicts`.

    Counts (not already-built dicts) decide the cap so a large diff never
    builds more dicts than the cap can ever keep.
    """
    findings: list[dict[str, object]] = []
    for bucket_name, bucket_changes in (
        ("breaking", getattr(diff, "breaking", [])),
        ("api_break", getattr(diff, "source_breaks", [])),
        ("risk", getattr(diff, "risk", [])),
    ):
        remaining = _MAX_STACK_FINDINGS_PER_LIBRARY - len(findings)
        if remaining <= 0:
            break
        for c in bucket_changes[:remaining]:
            kind = getattr(c, "kind", None)
            findings.append(
                {
                    "bucket": bucket_name,
                    "kind": getattr(kind, "value", str(kind)),
                    "symbol": getattr(c, "symbol", None),
                    "description": getattr(c, "description", None),
                    "source_location": getattr(c, "source_location", None),
                }
            )
    return findings


def stack_to_json(result: StackCheckResult, indent: int = 2) -> str:
    """Render a StackCheckResult as JSON."""
    d: dict[str, object] = {
        "root_binary": result.root_binary,
        "baseline_env": result.baseline_env,
        "candidate_env": result.candidate_env,
        "verdict": {
            "loadability": result.loadability.value,
            "abi_risk": result.abi_risk.value,
            "risk_score": result.risk_score,
        },
    }

    # Dependency graph nodes.
    d["baseline_graph"] = _graph_to_dict(result.baseline_graph)
    if result.baseline_graph is not result.candidate_graph:
        d["candidate_graph"] = _graph_to_dict(result.candidate_graph)

    # Binding summary.
    d["bindings_summary"] = _bindings_summary(result.bindings_candidate)

    # Missing symbols.
    if result.missing_symbols:
        d["missing_symbols"] = [
            {
                "consumer": b.consumer,
                "symbol": b.symbol,
                "version": b.version,
                "explanation": b.explanation,
            }
            for b in result.missing_symbols
        ]

    # Unresolved DSOs.
    if result.candidate_graph.unresolved:
        d["unresolved_libraries"] = [
            {"consumer": consumer, "soname": soname}
            for consumer, soname in result.candidate_graph.unresolved
        ]

    # Stack changes (two-env mode).
    if result.stack_changes:
        sc_list = []
        for sc in result.stack_changes:
            sc_dict: dict[str, object] = {
                "library": sc.library,
                "change_type": sc.change_type,
                "abi_verdict": sc.abi_diff.verdict.value if sc.abi_diff else None,
                "abi_breaking": len(sc.abi_diff.breaking) if sc.abi_diff else 0,
                "abi_changes": len(sc.abi_diff.changes) if sc.abi_diff else 0,
            }
            # Per-library confidence and evidence tiers
            if sc.abi_diff:
                diff = sc.abi_diff
                # Preserve the actual findings (kind/symbol/description/
                # location), not just their counts — a stack check used to
                # report e.g. "abi_breaking: 3" for a library with no way to
                # tell which symbols broke without a separate `compare` run.
                total_gating = (
                    len(diff.breaking) + len(diff.source_breaks) + len(diff.risk)
                )
                stack_findings = _stack_finding_dicts(diff)
                if stack_findings:
                    sc_dict["findings"] = stack_findings
                    if total_gating > _MAX_STACK_FINDINGS_PER_LIBRARY:
                        sc_dict["findings_truncated"] = True
                conf = getattr(diff, "confidence", None)
                if conf is not None:
                    sc_dict["confidence"] = conf.value if hasattr(conf, "value") else str(conf)
                tiers = getattr(diff, "evidence_tiers", []) or []
                if tiers:
                    sc_dict["evidence_tiers"] = list(tiers)
                cov_warns = getattr(diff, "coverage_warnings", []) or []
                if cov_warns:
                    sc_dict["coverage_warnings"] = list(cov_warns)
                # File metadata for traceability
                old_meta = getattr(diff, "old_metadata", None)
                new_meta = getattr(diff, "new_metadata", None)
                if old_meta:
                    sc_dict["old_file"] = {
                        "path": getattr(old_meta, "path", ""),
                        "sha256": getattr(old_meta, "sha256", ""),
                        "size_bytes": getattr(old_meta, "size_bytes", 0),
                    }
                if new_meta:
                    sc_dict["new_file"] = {
                        "path": getattr(new_meta, "path", ""),
                        "sha256": getattr(new_meta, "sha256", ""),
                        "size_bytes": getattr(new_meta, "size_bytes", 0),
                    }
            sc_list.append(sc_dict)
        d["stack_changes"] = sc_list

    # Runtime binding-provider changes (cross-environment rebound).
    if result.binding_changes:
        d["binding_changes"] = [
            {
                "kind": bc.kind.value,
                "symbol": bc.symbol,
                "description": bc.description,
                "old_value": bc.old_value,
                "new_value": bc.new_value,
            }
            for bc in result.binding_changes
        ]

    return json.dumps(d, indent=indent, default=str)


def _render_unresolved_section(lines: list[str], graph: DependencyGraph) -> None:
    """Append unresolved libraries section if any."""
    if not graph.unresolved:
        return
    lines += ["## ❌ Unresolved Libraries", ""]
    for consumer, soname in graph.unresolved:
        lines.append(f"- `{Path(consumer).name}` needs `{soname}` — **NOT FOUND**")
    lines.append("")


def _render_missing_symbols_section(lines: list[str], missing: list[SymbolBinding]) -> None:
    """Append missing symbols section if any."""
    if not missing:
        return
    lines += ["## ❌ Missing Symbols", ""]
    for b in missing[:20]:
        ver = f"@{b.version}" if b.version else ""
        lines.append(f"- `{Path(b.consumer).name}` needs `{b.symbol}{ver}` — not found in any loaded DSO")
    if len(missing) > 20:
        lines.append(f"- ... +{len(missing) - 20} more")
    lines.append("")


def _render_stack_changes_section(lines: list[str], stack_changes: list[StackChange]) -> None:
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
            verdict = sc.abi_diff.verdict.value if sc.abi_diff else "unknown"
            emoji = "❌" if verdict == "BREAKING" else ("⚠️" if verdict in ("API_BREAK", "COMPATIBLE_WITH_RISK") else "✅")
            lines.append(f"- {emoji} **{sc.library}** — content changed (ABI: `{verdict}`)")
            if sc.abi_diff:
                conf = getattr(sc.abi_diff, "confidence", None)
                tiers = getattr(sc.abi_diff, "evidence_tiers", []) or []
                tier_str = ", ".join(f"`{t}`" for t in tiers) if tiers else "_none_"
                if conf is not None:
                    conf_val = conf.value if hasattr(conf, "value") else str(conf)
                    lines.append(f"  - Confidence: **{conf_val.upper()}** | Evidence: {tier_str}")
                elif tiers:
                    lines.append(f"  - Evidence: {tier_str}")
                if sc.abi_diff.breaking:
                    for c in sc.abi_diff.breaking[:5]:
                        lines.append(f"  - `{c.kind.value}`: {c.description}")
    lines.append("")


def _render_binding_changes_section(lines: list[str], binding_changes: list[Change]) -> None:
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

    if result.baseline_env and result.candidate_env and result.baseline_env != result.candidate_env:
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


def _graph_to_dict(graph: DependencyGraph) -> dict[str, object]:
    """Convert a DependencyGraph to a JSON-serializable dict."""
    return {
        "root": graph.root,
        "node_count": graph.node_count,
        "nodes": [
            {
                "path": str(node.path),
                "soname": node.soname,
                "needed": node.needed,
                "depth": node.depth,
                "resolution_reason": node.resolution_reason,
            }
            for node in sorted(graph.nodes.values(), key=lambda n: (n.depth, n.soname))
        ],
        "edges": [
            {"consumer": consumer, "provider": provider}
            for consumer, provider in graph.edges
        ],
        "unresolved": [
            {"consumer": consumer, "soname": soname}
            for consumer, soname in graph.unresolved
        ],
    }


def _bindings_summary(bindings: list[SymbolBinding]) -> dict[str, int]:
    """Count bindings by status."""
    summary: dict[str, int] = {}
    for b in bindings:
        key = b.status.value
        summary[key] = summary.get(key, 0) + 1
    return summary


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
        _render_node(lines, graph, adj, child, child_prefix, i == len(children) - 1, shown, on_path)

    on_path.discard(key)
