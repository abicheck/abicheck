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

ADR-041 P2 #4 extends coverage from call/reference edges to inheritance:
Kythe's `/kythe/edge/extends` (https://kythe.io/docs/schema/graph.html —
"record extends record", including its access-qualified `/public`/
`/protected`/`/private` variants) unambiguously matches ``TYPE_INHERITS`` 1:1,
so :func:`ingest_kythe_entries` maps it directly. CodeQL's raw
``{"#select": {"tuples": [...]}}`` shape carries no self-describing relation
kind at all (unlike Kythe's fixed `edge_kind` vocabulary) — which query
produced a given result file is knowledge only the caller has — so
:func:`ingest_codeql_extends_results` is a separate entry point, mirroring
:func:`ingest_codeql_call_results`'s existing one-function-per-relation
design rather than trying to guess a tuple set's meaning from its shape.
Kythe's `/kythe/edge/typed` (connecting a node to its type) is deliberately
NOT mapped here: distinguishing a ``DECL_HAS_TYPE`` (function return/param)
from a ``TYPE_HAS_FIELD_TYPE`` (record field) target requires correlating the
edge's source `VName` against separate Kythe *node* facts (`/kythe/node/kind`)
this simplified entries-list ingestion never reads — mapping every `typed`
edge to one fixed kind regardless of what the source node actually is would
be a real semantic error, not just an approximation, so it is left as a
documented, open gap rather than guessed at.
"""
from __future__ import annotations

from typing import Any

from .source_graph import (
    CONF_REDUCED,
    GraphEdge,
    GraphNode,
    SourceGraphSummary,
    _decl_node_id,
    _type_node_id,
)

#: Kythe edge-kind prefixes we care about, mapped to abicheck edge kinds.
_KYTHE_CALL_PREFIX = "/kythe/edge/ref/call"
_KYTHE_REF_PREFIX = "/kythe/edge/ref"
#: `/kythe/edge/extends` plus its access-qualified variants -- all mean
#: "record extends record" regardless of the base's access specifier, and
#: abicheck's own ``TYPE_INHERITS`` carries no access-specifier distinction
#: either (``type_graph.py``'s own ``VisitCXXRecordDecl``-equivalent walk
#: doesn't discriminate by base access), so all four collapse to one kind.
_KYTHE_EXTENDS_PREFIX = "/kythe/edge/extends"


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


def _add_type(graph: SourceGraphSummary, ident: str, provenance: str) -> str:
    """Same as :func:`_add_decl`, but on the ``type://`` id/``record_type``
    node-kind scheme ``type_graph.augment_graph_with_types`` uses for
    ``TYPE_INHERITS`` -- so a Kythe/CodeQL-ingested inheritance edge lands on
    the identical node a standalone clang type-graph replay would have
    created for the same record, rather than a disconnected ``decl://``
    duplicate (mirrors ``source_graph._source_edge_endpoint_ids``'s identical
    node-id mapping for the ADR-041 P1 #1 ``source_edges`` fold).
    """
    node_id = _type_node_id(ident)
    if not graph.has_node(node_id):
        graph.add_node(GraphNode(
            id=node_id, kind="record_type", label=ident,
            provenance=provenance, confidence=CONF_REDUCED,
        ))
    return node_id


def ingest_kythe_entries(
    graph: SourceGraphSummary, entries: list[dict[str, Any]], *, ref: str = ""
) -> int:
    """Fold a Kythe *entries* export into *graph* (ADR-031 D5).

    Each entry is a node/edge fact; entries whose ``edge_kind`` is a call
    (``/kythe/edge/ref/call``) become ``DECL_CALLS_DECL`` edges, other ``ref``
    edges become ``DECL_REFERENCES_DECL``, and an ``extends`` edge (ADR-041
    P2 #4; any access-qualified variant) becomes ``TYPE_INHERITS``. Returns
    the number of edges added and records the external store in
    ``external_graph_refs``.
    """
    added = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        edge_kind = str(entry.get("edge_kind", ""))
        is_ref = edge_kind.startswith(_KYTHE_REF_PREFIX)
        is_extends = edge_kind.startswith(_KYTHE_EXTENDS_PREFIX)
        if not is_ref and not is_extends:
            continue
        src = _kythe_identity(entry.get("source"))
        dst = _kythe_identity(entry.get("target"))
        if not src or not dst or src == dst:
            continue
        if is_extends:
            kind, attrs = "TYPE_INHERITS", {"role": "base"}
            src_id, dst_id = _add_type(graph, src, "kythe"), _add_type(graph, dst, "kythe")
        else:
            kind = "DECL_CALLS_DECL" if edge_kind.startswith(_KYTHE_CALL_PREFIX) else "DECL_REFERENCES_DECL"
            attrs = (
                {"call_kind": "unknown", "resolution": "points_to"}
                if kind == "DECL_CALLS_DECL"
                else {}
            )
            src_id, dst_id = _add_decl(graph, src, "kythe"), _add_decl(graph, dst, "kythe")
        before = len(graph.edges)
        graph.add_edge(GraphEdge(
            src=src_id, dst=dst_id,
            kind=kind, provenance="kythe", confidence=CONF_REDUCED, attrs=attrs,
        ))
        added += len(graph.edges) - before
    _record_backend(graph, "kythe", ref, added)
    return added


def _codeql_tuples(results: dict[str, Any]) -> list[Any]:
    """Extract the ``{"#select": {"tuples": [...]}}`` row list, defensively."""
    select = results.get("#select") if isinstance(results, dict) else None
    tuples = select.get("tuples", []) if isinstance(select, dict) else []
    return tuples if isinstance(tuples, list) else []


def _codeql_cell(value: Any) -> str:
    """One tuple cell: a bare string, or an object with a ``label``."""
    if isinstance(value, dict):
        return str(value.get("label", ""))
    return str(value) if value is not None else ""


def ingest_codeql_call_results(
    graph: SourceGraphSummary, results: dict[str, Any], *, ref: str = ""
) -> int:
    """Fold a CodeQL call-graph query result (BQRS→JSON) into *graph* (D5).

    Expects the standard ``{"#select": {"tuples": [[caller, callee], ...]}}``
    shape; each tuple element may be a bare string or an object with a
    ``label``. Rows become ``DECL_CALLS_DECL`` edges. Returns edges added.
    """
    added = 0
    for row in _codeql_tuples(results):
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        caller, callee = _codeql_cell(row[0]), _codeql_cell(row[1])
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


def ingest_codeql_extends_results(
    graph: SourceGraphSummary, results: dict[str, Any], *, ref: str = ""
) -> int:
    """Fold a CodeQL class-hierarchy query result into *graph* (ADR-041 P2 #4).

    Same ``{"#select": {"tuples": [[derived, base], ...]}}`` shape as
    :func:`ingest_codeql_call_results` — CodeQL's raw result JSON carries no
    self-describing relation kind, so which query produced a given export is
    knowledge only the caller has (e.g. a
    ``getASuperType()``/``extends``-style class-hierarchy query, as opposed
    to a call-graph query). Rows become ``TYPE_INHERITS`` edges on the same
    ``type://``/``record_type`` node scheme
    ``type_graph.augment_graph_with_types`` uses, so an ingested inheritance
    edge lands on the identical node a standalone clang type-graph replay
    would create for the same record. Returns edges added.
    """
    added = 0
    for row in _codeql_tuples(results):
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        derived, base = _codeql_cell(row[0]), _codeql_cell(row[1])
        if not derived or not base or derived == base:
            continue
        before = len(graph.edges)
        graph.add_edge(GraphEdge(
            src=_add_type(graph, derived, "codeql"), dst=_add_type(graph, base, "codeql"),
            kind="TYPE_INHERITS", provenance="codeql", confidence=CONF_REDUCED,
            attrs={"role": "base"},
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
