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

from collections.abc import Iterable
from typing import Any

from ..model.graph_facts import GraphEdge, GraphNode
from ..model.source_graph import SourceGraphSummary, _symbol_node_id
from ..model.source_graph_coverage import (
    SEMANTIC_FAMILIES,
    SemanticFamily,
    family_collected,
    graph_records_passes,
)
from ..model.source_graph_diff import GraphRelationChange, GraphSummaryDiff


def _label_map(graph: SourceGraphSummary) -> dict[str, str]:
    return {n.id: (n.label or n.id) for n in graph.nodes}


def _kind_map(graph: SourceGraphSummary) -> dict[str, str]:
    return {n.id: n.kind for n in graph.nodes}


def format_dependency_path(graph: SourceGraphSummary, path: list[GraphEdge]) -> str:
    """Render a :func:`_dependency_path` result as a human-readable chain.

    E.g. ``pub() --[DECL_CALLS_DECL]--> helper() --[DECL_HAS_TYPE]--> detail::Impl``.
    Returns ``""`` for an empty path (entry == target).
    """
    if not path:
        return ""
    labels = _label_map(graph)
    parts = [labels.get(path[0].src, path[0].src)]
    for e in path:
        parts.append(f"--[{e.kind}]--> {labels.get(e.dst, e.dst)}")
    return " ".join(parts)


def localize_symbol(graph: SourceGraphSummary, symbol: str) -> dict[str, Any]:
    """Localize an exported symbol through the graph (ADR-031 D8 `graph explain`).

    Given a (mangled) binary symbol, walk the graph to report what produced and
    reaches it: the exporting target(s), the static-archive member(s) that
    define it (``cache_dispatch.o`` in ``libinternal_dispatch.a``, from the
    archive pass's ``OBJECT_DEFINES_SYMBOL`` edges), the source
    declaration(s) it maps to, the public header(s) that declare those decls,
    the ABI-relevant build option(s) that feed it, and the static callees of
    its declarations. Every fact is graph-derived (provenance/confidence live
    on the edges), so the result is explanatory, never an ABI verdict
    (ADR-031 D6). An empty member list is an absence of evidence (the
    archive pass did not run, or the archive had no index), never "defined
    nowhere".
    """
    from ..model.source_graph_query import defining_members

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
        "defined_in_archive_members": [
            {"archive": archive, "member": member}
            for archive, member in defining_members(graph, symbol)
        ],
        "source_declarations": names(decls),
        "declared_in_headers": names(headers),
        "reached_by_build_options": names(options),
        "static_callees": names(callees),
        "header_kinds": {labels.get(h, h): kinds.get(h, "") for h in headers},
    }


def diff_edge_relations(
    old_edges: Iterable[GraphEdge], new_edges: Iterable[GraphEdge]
) -> tuple[list[GraphRelationChange], list[GraphRelationChange]]:
    """``(changed, role_unknown)``: every dependency (``GraphEdge.key()``)
    present on both sides whose role set differs (ADR-077 D4).

    *changed* holds the dependencies whose roles are comparable: both sides
    stamped roles, or both also carry role-less edges for it. *role_unknown*
    holds those where exactly one side carries a role-less edge -- the shape
    of a graph stored before its producer stamped roles, compared against a
    current one. That is a coverage gap, never a role change. Identical role
    sets produce no entry, so identical graphs diff empty.
    """
    old_roles = _roles_by_key(old_edges)
    new_roles = _roles_by_key(new_edges)
    changed: list[GraphRelationChange] = []
    unknown: list[GraphRelationChange] = []
    for key in sorted(old_roles.keys() & new_roles.keys()):
        before, after = old_roles[key], new_roles[key]
        if before == after:
            continue
        entry = GraphRelationChange(*key, tuple(sorted(before)), tuple(sorted(after)))
        if ("" in before) != ("" in after):
            unknown.append(entry)
        else:
            changed.append(entry)
    return changed, unknown


def _roles_by_key(edges: Iterable[GraphEdge]) -> dict[tuple[str, str, str], set[str]]:
    out: dict[tuple[str, str, str], set[str]] = {}
    for e in edges:
        out.setdefault(e.key(), set()).add(e.role)
    return out


def _family_looked_for(
    graph: SourceGraphSummary, family: SemanticFamily, flagged: bool
) -> bool:
    """Whether *graph* looked for *family*: its pass flag, or -- only on a
    graph that records no pass flags at all -- an edge of the family."""
    if family_collected(graph, family):
        return True
    return not flagged and any(e.kind in family.edge_kinds for e in graph.edges)


def _uncollected_families(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> tuple[SemanticFamily, ...]:
    """The semantic fact families (override, template, callback, virtual
    dispatch) either side never looked for.

    A header graph stored before the header pass projected those families
    carries neither their facts nor their flags; next to a fresh graph,
    every such fact would read as added, or the other way round removed. Two
    graphs that record no pass flags at all (hand-built) keep the plain
    structural diff they always had."""
    old_flagged = graph_records_passes(old)
    new_flagged = graph_records_passes(new)
    if not (old_flagged or new_flagged):
        return ()
    return tuple(
        f
        for f in SEMANTIC_FAMILIES
        if not (
            _family_looked_for(old, f, old_flagged)
            and _family_looked_for(new, f, new_flagged)
        )
    )


def _only_from(
    node: GraphNode, node_kinds: frozenset[str], provenances: frozenset[str]
) -> bool:
    if node.kind in node_kinds:
        return True
    producers = {f.producer for f in node.facts} or {node.provenance}
    return producers <= provenances


def diff_source_graph(
    old: SourceGraphSummary, new: SourceGraphSummary
) -> GraphSummaryDiff:
    """Compute the structural delta from *old* to *new* (Phase 5 seed).

    Nodes match on id. Edges match on their full identity
    (``GraphEdge.relation_key()``), reported in two granularities:
    ``added_edges``/``removed_edges`` keep ADR-046 D1's coarse ``key()``
    (a dependency that entered or left the graph), and a dependency kept
    with a different role set is a ``changed_edges`` entry (or
    ``role_unknown_edges`` when one side carries no role data) -- see
    :func:`diff_edge_relations`.

    A semantic fact family one side never looked for
    (:func:`_uncollected_families`) is left out of the delta -- its edges,
    role changes included, and the nodes only it produced -- and named in
    ``coverage_gaps`` instead: a coverage gap, never a removal or an addition.
    """
    gaps = _uncollected_families(old, new)
    edge_kinds = frozenset().union(*(f.edge_kinds for f in gaps))
    provenances = frozenset(f.provenance for f in gaps)
    node_kinds = frozenset().union(*(f.node_kinds for f in gaps))
    old_nodes = {n.id: n for n in old.nodes}
    new_nodes = {n.id: n for n in new.nodes}
    old_edges = {e.key(): e for e in old.edges if e.kind not in edge_kinds}
    new_edges = {e.key(): e for e in new.edges if e.kind not in edge_kinds}
    if gaps:
        # A node only an unlooked-for family produced exists on one side
        # for the same reason its edges do.
        for nodes in (old_nodes, new_nodes):
            for node_id in [
                i for i, n in nodes.items() if _only_from(n, node_kinds, provenances)
            ]:
                del nodes[node_id]
    changed, role_unknown = diff_edge_relations(
        (e for e in old.edges if e.kind not in edge_kinds),
        (e for e in new.edges if e.kind not in edge_kinds),
    )

    return GraphSummaryDiff(
        coverage_gaps=[f.name for f in gaps],
        added_nodes=[new_nodes[i] for i in sorted(new_nodes.keys() - old_nodes.keys())],
        removed_nodes=[
            old_nodes[i] for i in sorted(old_nodes.keys() - new_nodes.keys())
        ],
        added_edges=[new_edges[k] for k in sorted(new_edges.keys() - old_edges.keys())],
        removed_edges=[
            old_edges[k] for k in sorted(old_edges.keys() - new_edges.keys())
        ],
        changed_edges=changed,
        role_unknown_edges=role_unknown,
    )
