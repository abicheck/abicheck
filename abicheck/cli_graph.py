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

"""L5 source-graph load/localize helpers (ADR-031; ADR-043 D1).

The former `graph compare`/`graph explain` CLI commands were removed
(ADR-043): the L5 graph is an internal consequence of `--depth source`, and
its diff/localization feed the source-depth report rather than a separate
top-level command (ADR-028 D3: they only ever explain/prioritize impact,
never decide or suppress an artifact-proven ABI break on their own). The two
loader/resolver functions here remain as plain, Click-free library code —
`cli_buildsource.py` still re-exports them (its lazy `__getattr__` shim) for
callers that resolved them from that module historically.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import click

if TYPE_CHECKING:
    from .model.source_graph import SourceGraphSummary


def _load_source_graph(path: Path) -> SourceGraphSummary:
    """Load a source graph summary from a JSON file or an evidence-pack dir.

    Accepts either ``…/graph/source_graph_summary.json`` directly or a pack
    directory (the graph is read from its manifest layout). Raises a Click error
    when neither yields a graph so the failure is actionable.
    """
    from .errors import SnapshotError
    from .model.source_graph import SourceGraphSummary
    from .workflows.extraction import load_pack_or_raise

    if path.is_dir():
        try:
            pack = load_pack_or_raise(path)
        except SnapshotError as exc:
            raise click.ClickException(str(exc)) from exc
        if pack.source_graph is None:
            raise click.ClickException(
                f"Evidence pack at {path} has no L5 source graph."
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
    # ADR-063 Phase 10: a full snapshot document (e.g. one written by `dump
    # --sources -o out.abi.json`) carries its L5 graph nested under
    # `surface_graph` (the canonical location, schema v29+) or
    # `build_source.source_graph` (a pre-Phase-3 document, or the fallback
    # when `surface_graph` was never populated), never as top-level
    # `nodes`/`edges` -- accept that shape, preferring `surface_graph`, before
    # falling through to the bare-graph-JSON contract below. See
    # `docs/contribute/plans/one-semantic-pipeline.md`'s Phase 10 checklist.
    embedded = _embedded_source_graph_dict(data)
    if embedded is not None:
        return SourceGraphSummary.from_dict(embedded)
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


def _embedded_source_graph_dict(data: dict[str, Any]) -> dict[str, Any] | None:
    """The nested L5 graph dict inside a full snapshot document, ADR-063
    Phase 10's fallback rule applied to a raw JSON dict rather than a live
    ``AbiSnapshot``: prefers the canonical top-level ``surface_graph`` key,
    falling back to the legacy ``build_source.source_graph`` nested key only
    when ``surface_graph`` is absent. Returns ``None`` when *data* carries
    neither key at all -- i.e. it isn't a snapshot document -- so the caller
    falls through to the bare-graph-JSON contract instead.
    """
    graph = data.get("surface_graph")
    if isinstance(graph, dict):
        return graph
    build_source = data.get("build_source")
    if isinstance(build_source, dict):
        graph = build_source.get("source_graph")
        if isinstance(graph, dict):
            return graph
    return None


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
