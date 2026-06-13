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

"""External graph-backend adapters: Kythe and CodeQL (ADR-031 D5, phase 7).

These backends are *adapters, not core dependencies* (ADR-031 D5): abicheck
never runs Kythe or CodeQL. It ingests a **pre-captured export** — a Kythe
entries JSON or a CodeQL query-result JSON — and folds the relevant edges into
the abicheck-owned :class:`SourceGraphSummary`, exactly as the Bazel/Android
adapters consume pre-captured query output (ADR-028 D6, non-executing). The
external store itself is referenced via ``external_graph_refs`` (ADR-031 D1/D7),
so the compact summary never has to embed a whole external graph.

Every ingested edge is tagged with its backend provenance and a reduced
confidence: cross-reference graphs from external indexers are mature but
approximate for virtual dispatch / templates (ADR-031 D4, D9).
"""
from __future__ import annotations

from typing import Any

from .source_graph import (
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    _decl_node_id,
)

#: Kythe edge-kind prefixes we care about, mapped to abicheck edge kinds.
_KYTHE_CALL_PREFIX = "/kythe/edge/ref/call"
_KYTHE_REF_PREFIX = "/kythe/edge/ref"


def _kythe_identity(vname: Any) -> str:
    """Stable identity for a Kythe VName: its signature, else its path."""
    if not isinstance(vname, dict):
        return ""
    return str(vname.get("signature") or vname.get("path") or "")


def _add_decl(graph: SourceGraphSummary, ident: str, provenance: str) -> str:
    node_id = _decl_node_id(ident)
    if not graph.has_node(node_id):
        graph.add_node(GraphNode(
            id=node_id, kind="source_decl", label=ident,
            provenance=provenance, confidence=CONF_REDUCED,
        ))
    return node_id


def ingest_kythe_entries(
    graph: SourceGraphSummary, entries: list[dict[str, Any]], *, ref: str = ""
) -> int:
    """Fold a Kythe *entries* export into *graph* (ADR-031 D5).

    Each entry is a node/edge fact; entries whose ``edge_kind`` is a call
    (``/kythe/edge/ref/call``) become ``DECL_CALLS_DECL`` edges, other ``ref``
    edges become ``DECL_REFERENCES_DECL``. Returns the number of edges added and
    records the external store in ``external_graph_refs``.
    """
    added = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        edge_kind = str(entry.get("edge_kind", ""))
        if not edge_kind.startswith(_KYTHE_REF_PREFIX):
            continue
        src = _kythe_identity(entry.get("source"))
        dst = _kythe_identity(entry.get("target"))
        if not src or not dst or src == dst:
            continue
        kind = "DECL_CALLS_DECL" if edge_kind.startswith(_KYTHE_CALL_PREFIX) else "DECL_REFERENCES_DECL"
        attrs = {"call_kind": "unknown", "resolution": "points_to"} if kind == "DECL_CALLS_DECL" else {}
        before = len(graph.edges)
        graph.add_edge(GraphEdge(
            src=_add_decl(graph, src, "kythe"), dst=_add_decl(graph, dst, "kythe"),
            kind=kind, provenance="kythe", confidence=CONF_REDUCED, attrs=attrs,
        ))
        added += len(graph.edges) - before
    _record_backend(graph, "kythe", ref, added)
    return added


def ingest_codeql_call_results(
    graph: SourceGraphSummary, results: dict[str, Any], *, ref: str = ""
) -> int:
    """Fold a CodeQL call-graph query result (BQRS→JSON) into *graph* (D5).

    Expects the standard ``{"#select": {"tuples": [[caller, callee], ...]}}``
    shape; each tuple element may be a bare string or an object with a
    ``label``. Rows become ``DECL_CALLS_DECL`` edges. Returns edges added.
    """
    select = results.get("#select") if isinstance(results, dict) else None
    tuples = select.get("tuples", []) if isinstance(select, dict) else []

    def _cell(value: Any) -> str:
        if isinstance(value, dict):
            return str(value.get("label", ""))
        return str(value) if value is not None else ""

    added = 0
    for row in tuples:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        caller, callee = _cell(row[0]), _cell(row[1])
        if not caller or not callee or caller == callee:
            continue
        before = len(graph.edges)
        graph.add_edge(GraphEdge(
            src=_add_decl(graph, caller, "codeql"), dst=_add_decl(graph, callee, "codeql"),
            kind="DECL_CALLS_DECL", provenance="codeql", confidence=CONF_REDUCED,
            attrs={"call_kind": "unknown", "resolution": "points_to"},
        ))
        added += len(graph.edges) - before
    _record_backend(graph, "codeql", ref, added)
    return added


def _record_backend(graph: SourceGraphSummary, backend: str, ref: str, edges: int) -> None:
    """Note the external graph store in ``external_graph_refs`` (ADR-031 D1/D7)."""
    graph.external_graph_refs.append({
        "backend": backend,
        "ref": ref,
        "edges_ingested": edges,
        "confidence": CONF_REDUCED,
    })
