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

"""`graph` command group — L5 source-graph tools (ADR-031).

Two commands that *explain and prioritize* impact through the source→symbol
graph; per ADR-028 D3 they never, on their own, decide or suppress an
artifact-proven ABI break:

- ``graph compare`` — structural diff of two source-graph summaries.
- ``graph explain`` — localize an exported symbol through the graph.

This is a sibling sub-command module: it imports ``main`` from ``cli`` and
registers its group via the ``@main.group`` decorator (see the side-effect
import block at the tail of ``cli.py``).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click

from .cli import main

if TYPE_CHECKING:
    from .buildsource.source_graph import SourceGraphSummary


def _format_option(
    help_text: str,
) -> Callable[[Callable[..., object]], Callable[..., object]]:
    """Shared ``--format text|json`` option (defined once for both commands)."""
    return click.option(
        "--format",
        "fmt",
        default="text",
        show_default=True,
        type=click.Choice(["text", "json"], case_sensitive=False),
        help=help_text,
    )


@main.group("graph")
def graph_group() -> None:
    """L5 source-graph tools (ADR-031): structural diff and finding localization.

    These commands *explain and prioritize* impact through the source→symbol
    graph; per ADR-028 D3 they never, on their own, decide or suppress an
    artifact-proven ABI break.
    """


@graph_group.command("compare")
@click.argument("old", type=click.Path(path_type=Path))
@click.argument("new", type=click.Path(path_type=Path))
@_format_option("Output format for the structural graph diff.")
def compare_graph_cmd(old: Path, new: Path, fmt: str) -> None:
    """Compare two L5 source graph summaries (ADR-031 D6, D8).

    \b
    OLD and NEW may each be a `graph/source_graph_summary.json` file or an
    evidence-pack directory produced by `collect --source-graph summary`.

    The diff is structural — which nodes/edges entered or left the graph. Per
    ADR-028 D3 / ADR-031 D6 it *explains and prioritizes* impact; it never, on
    its own, decides or suppresses an artifact-proven ABI break.
    """
    from .buildsource.source_graph import diff_source_graph, diff_source_graph_findings

    old_graph = _load_source_graph(old)
    new_graph = _load_source_graph(new)
    delta = diff_source_graph(old_graph, new_graph)
    findings = diff_source_graph_findings(old_graph, new_graph)

    if fmt == "json":
        payload = delta.to_dict()
        payload["findings"] = [
            {
                "kind": c.kind.value,
                "symbol": c.symbol,
                "description": c.description,
                "old_value": c.old_value,
                "new_value": c.new_value,
            }
            for c in findings
        ]
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    if not delta.changed:
        click.echo("Source graphs are structurally identical.")
        click.echo(f"  graph_id: {old_graph.graph_id or old_graph.compute_graph_id()}")
        return

    click.echo("Source graph structural diff:")
    click.echo(
        f"  nodes: +{len(delta.added_nodes)} / -{len(delta.removed_nodes)}    "
        f"edges: +{len(delta.added_edges)} / -{len(delta.removed_edges)}"
    )
    for node in delta.added_nodes:
        click.echo(f"  + node [{node.kind}] {node.label or node.id}")
    for node in delta.removed_nodes:
        click.echo(f"  - node [{node.kind}] {node.label or node.id}")
    for edge in delta.added_edges:
        click.echo(f"  + edge {edge.kind}: {edge.src} -> {edge.dst}")
    for edge in delta.removed_edges:
        click.echo(f"  - edge {edge.kind}: {edge.src} -> {edge.dst}")

    if findings:
        # Graph-derived RISK findings (ADR-031 D6): explanation/prioritization,
        # never a standalone ABI-break verdict (ADR-028 D3).
        click.echo(f"\nGraph-derived risk findings ({len(findings)}):")
        for c in findings:
            click.echo(f"  [{c.kind.value}] {c.symbol}: {c.description}")


@graph_group.command("explain")
@click.option(
    "--sources",
    "sources",
    type=click.Path(path_type=Path),
    required=True,
    help="Source/graph pack directory (or a source_graph_summary.json) to explain through.",
)
@click.option(
    "--symbol",
    "symbol",
    default="",
    help="Exported (mangled) binary symbol to localize.",
)
@click.option(
    "--report",
    "report",
    type=click.Path(path_type=Path),
    default=None,
    help="A `compare --format json` report; with --finding-id, resolves the symbol from it.",
)
@click.option(
    "--finding-id",
    "finding_id",
    default="",
    help="Index (or symbol) of a finding in --report to localize.",
)
@_format_option("Output format for the localization result.")
def explain_finding_cmd(
    sources: Path,
    symbol: str,
    report: Path | None,
    finding_id: str,
    fmt: str,
) -> None:
    """Localize a finding through L5 source-graph evidence (ADR-031 D8).

    Given an exported symbol (directly via --symbol, or resolved from a
    `--report` finding via --finding-id), walks the graph to show what produced
    and reaches it: exporting target, source declaration(s), declaring public
    header(s), ABI-relevant build option(s), and static callees. This explains
    and prioritizes; it is never an ABI verdict (ADR-031 D6).
    """
    from .buildsource.source_graph import localize_symbol

    graph = _load_source_graph(sources)
    if not symbol and report is not None:
        symbol = _resolve_symbol_from_report(report, finding_id)
    if not symbol:
        raise click.ClickException(
            "No symbol to explain: pass --symbol, or --report with --finding-id."
        )

    result = localize_symbol(graph, symbol)
    if fmt == "json":
        click.echo(json.dumps(result, indent=2, sort_keys=True))
        return

    click.echo(f"Explaining symbol: {symbol}")
    if not result["found"]:
        # Nothing to localize — skip the five "(none in graph)" rows that would
        # otherwise add only noise after the not-present notice.
        click.echo(
            "  (symbol not present in the source graph — no localization available)"
        )
        return
    rows = [
        ("exported by target(s)", result["exported_by_targets"]),
        ("source declaration(s)", result["source_declarations"]),
        ("declared in header(s)", result["declared_in_headers"]),
        ("reached by build option(s)", result["reached_by_build_options"]),
        ("static callee(s)", result["static_callees"]),
    ]
    for label, values in rows:
        click.echo(f"  {label}: {', '.join(values) if values else '(none in graph)'}")


# ── Graph-input helpers (co-located with the only commands that use them) ──────


def _load_source_graph(path: Path) -> SourceGraphSummary:
    """Load a source graph summary from a JSON file or an evidence-pack dir.

    Accepts either ``…/graph/source_graph_summary.json`` directly or a pack
    directory (the graph is read from its manifest layout). Raises a Click error
    when neither yields a graph so the failure is actionable.
    """
    from .buildsource.pack import BuildSourcePack
    from .buildsource.source_graph import SourceGraphSummary

    if path.is_dir():
        # Load the pack directly (rather than via cli_buildsource_helpers) so this
        # module stays off the service/scan import cluster — keeping `cli_graph`'s
        # only cycle the by-design `cli <-> cli_graph` sibling-registration edge.
        try:
            pack = BuildSourcePack.load(path)
        except (FileNotFoundError, ValueError) as exc:
            raise click.ClickException(
                f"Invalid evidence pack at {path}: {exc}"
            ) from exc
        if pack.source_graph is None:
            raise click.ClickException(
                f"Evidence pack at {path} has no L5 source graph "
                "(collect it with `collect --source-graph summary`)."
            )
        return pack.source_graph
    if not path.is_file():
        raise click.ClickException(f"No source graph summary at {path}.")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(
            f"Cannot read source graph at {path}: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise click.ClickException(f"{path} must contain a JSON object.")
    # SourceGraphSummary.from_dict is intentionally forgiving (it defaults a
    # missing nodes/edges to empty), so guard here: an unrelated JSON file (e.g.
    # a pack manifest) would otherwise load as an empty graph and report a bogus
    # diff instead of an actionable error.
    if not isinstance(data.get("nodes"), list) or not isinstance(
        data.get("edges"), list
    ):
        raise click.ClickException(
            f"{path} is not a source graph summary "
            "(expected top-level 'nodes' and 'edges' lists)."
        )
    return SourceGraphSummary.from_dict(data)


def _resolve_symbol_from_report(report: Path, finding_id: str) -> str:
    """Resolve a symbol from a `compare --format json` report finding.

    ``finding_id`` may be a 0-based index into the report's changes, or a symbol
    substring to match. Returns the matched change's ``symbol`` (or "").
    """
    try:
        data = json.loads(Path(report).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Cannot read report {report}: {exc}") from exc
    if not isinstance(data, dict):
        # A valid-but-non-object report (e.g. a bare JSON list) would make the
        # `.get(...)` below raise AttributeError past Click's handling; turn it
        # into an actionable error, mirroring _load_source_graph's dict guard.
        raise click.ClickException(f"Report {report} must contain a JSON object.")
    changes = data.get("changes") or data.get("findings") or []
    if not isinstance(changes, list):
        return ""
    if finding_id.isdigit():
        idx = int(finding_id)
        if 0 <= idx < len(changes) and isinstance(changes[idx], dict):
            return str(changes[idx].get("symbol", ""))
        return ""
    for change in changes:
        if (
            isinstance(change, dict)
            and finding_id
            and finding_id in str(change.get("symbol", ""))
        ):
            return str(change.get("symbol", ""))
    return ""
