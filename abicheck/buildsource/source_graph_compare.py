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

"""ADR-061 Phase 5 item 2: SourceGraphSummary structural comparison.

Split out of ``source_graph.py`` (comparison half — ADR-031 D8/D6 Phase 5
seed): :func:`diff_source_graph` computes the structural node/edge delta
between two :class:`~abicheck.model.source_graph.SourceGraphSummary`
instances, and :func:`localize_symbol` (ADR-031 D8 ``graph explain``) walks
a single graph to answer what produced and reaches an exported symbol.

Graph values live in ``abicheck.model.source_graph``; construction
(:func:`~abicheck.buildsource.source_graph_build.build_source_graph`) lives
in the sibling ``source_graph_build.py``/``source_graph_build_source_abi.py``.
``source_graph.py`` itself is now a thin backward-compatibility facade
re-exporting both halves plus the shared node/edge-classification
predicates in ``source_graph_query.py``.
"""

from __future__ import annotations

from typing import Any

from ..model.source_graph import GraphSummaryDiff, SourceGraphSummary, _symbol_node_id


def _label_map(graph: SourceGraphSummary) -> dict[str, str]:
    return {n.id: (n.label or n.id) for n in graph.nodes}


def _kind_map(graph: SourceGraphSummary) -> dict[str, str]:
    return {n.id: n.kind for n in graph.nodes}


def localize_symbol(graph: SourceGraphSummary, symbol: str) -> dict[str, Any]:
    """Localize an exported symbol through the graph (ADR-031 D8 `graph explain`).

    Given a (mangled) binary symbol, walk the graph to report what produced and
    reaches it: the exporting target(s), the source declaration(s) it maps to,
    the public header(s) that declare those decls, the ABI-relevant build
    option(s) that feed it, and the static callees of its declarations. Every
    fact is graph-derived (provenance/confidence live on the edges), so the
    result is explanatory, never an ABI verdict (ADR-031 D6).
    """
    labels = _label_map(graph)
    kinds = _kind_map(graph)
    sym_id = _symbol_node_id(symbol)
    found = graph.has_node(sym_id)

    targets = sorted(
        {
            e.src
            for e in graph.edges
            if e.kind == "BINARY_EXPORTS_SYMBOL" and e.dst == sym_id
        }
    )
    decls = sorted(
        {
            e.src
            for e in graph.edges
            if e.kind == "SOURCE_DECL_MAPS_TO_SYMBOL" and e.dst == sym_id
        }
    )
    options = sorted(
        {
            e.src
            for e in graph.edges
            if e.kind == "BUILD_OPTION_AFFECTS_SYMBOL" and e.dst == sym_id
        }
    )

    headers: set[str] = set()
    callees: set[str] = set()
    for decl in decls:
        headers |= {
            e.src for e in graph.edges if e.kind == "SOURCE_DECLARES" and e.dst == decl
        }
        callees |= {
            e.dst for e in graph.edges if e.kind == "DECL_CALLS_DECL" and e.src == decl
        }

    def names(ids: set[str] | list[str]) -> list[str]:
        return sorted(labels.get(i, i) for i in ids)

    return {
        "symbol": symbol,
        "found": found,
        "exported_by_targets": names(targets),
        "source_declarations": names(decls),
        "declared_in_headers": names(headers),
        "reached_by_build_options": names(options),
        "static_callees": names(callees),
        "header_kinds": {labels.get(h, h): kinds.get(h, "") for h in headers},
    }


def diff_source_graph(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> GraphSummaryDiff:
    """Compute the structural delta from *old* to *new* (Phase 5 seed).

    Edge comparison deliberately stays keyed on the coarse ``key()``
    (ADR-046 D1 — "existing callers... are unaffected"), not
    ``relation_key()``: when two role-distinct edges share a ``(src, dst,
    kind)`` (e.g. a function that both returns and takes the same private
    type), only one is a "representative" for this structural added/removed
    comparison — role-level diff granularity is not implemented here.
    """
    old_nodes = {n.id: n for n in old.nodes}
    new_nodes = {n.id: n for n in new.nodes}
    old_edges = {e.key(): e for e in old.edges}
    new_edges = {e.key(): e for e in new.edges}

    return GraphSummaryDiff(
        added_nodes=[new_nodes[i] for i in sorted(new_nodes.keys() - old_nodes.keys())],
        removed_nodes=[
            old_nodes[i] for i in sorted(old_nodes.keys() - new_nodes.keys())
        ],
        added_edges=[new_edges[k] for k in sorted(new_edges.keys() - old_edges.keys())],
        removed_edges=[
            old_edges[k] for k in sorted(old_edges.keys() - new_edges.keys())
        ],
    )
