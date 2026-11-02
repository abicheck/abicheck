# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Edge branches of the graph closure renumbering and of merging package
carriers with an export's own blocked sections (ADR-062 D14/D18).

Each case names the input shape that takes the branch and asserts the
observable result against a value written out here, not one computed by the
code under test."""

from __future__ import annotations

from abicheck.model.graph_facts import GraphEdge, GraphFact, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.storage.closure_graph_renumber import renumber_graph
from abicheck.storage.closure_identity import graph_rewrite_factory
from abicheck.storage.wire_extensions import (
    WIRE_EXTENSIONS_KEY,
    WireExtensions,
    attach_package_carriers,
)

_RAW = "(lambda at /src/inc/a.h:3:5)"
_STRIPPED = "(lambda:a.h:3:5)"


def _graph(*nodes: GraphNode, edges: tuple[GraphEdge, ...] = ()) -> SourceGraphSummary:
    return SourceGraphSummary(nodes=list(nodes), edges=list(edges))


def test_marker_free_graph_is_returned_unchanged() -> None:
    graph = _graph(GraphNode(id="decl://f", kind="source_decl", label="f"))
    assert renumber_graph(graph, graph_rewrite_factory({})) is graph
    assert renumber_graph(None, graph_rewrite_factory({})) is None


def test_marker_only_in_an_edge_fact_is_still_renumbered() -> None:
    a = GraphNode(id="decl://a", kind="source_decl", label="a")
    b = GraphNode(id="decl://b", kind="source_decl", label="b")
    edge = GraphEdge(src=a.id, dst=b.id, kind="decl_uses_type")
    edge.facts = [GraphFact(producer="p", attrs={"t": f"f<{_STRIPPED}>"})]
    out = renumber_graph(_graph(a, b, edges=(edge,)), graph_rewrite_factory({}))
    assert out is not None
    (renumbered,) = out.edges
    assert renumbered.facts[0].attrs["t"] == "f<(lambda:a.h#1)>"


def test_hint_without_a_closure_marker_needs_no_rewrite() -> None:
    # "(lambda" is a hint, but no stripped or raw closure coordinate follows.
    node = GraphNode(id="decl://g<(lambda)>", kind="source_decl", label="g")
    graph = _graph(node)
    assert renumber_graph(graph, graph_rewrite_factory({})) is graph


def test_raw_spelling_is_stripped_and_numbered() -> None:
    node = GraphNode(id=f"decl://f<{_RAW}>", kind="source_decl", label="f")
    out = renumber_graph(_graph(node), graph_rewrite_factory({}))
    assert [n.id for n in out.nodes] == ["decl://f<(lambda:a.h#1)>"]


def test_package_carriers_merge_with_the_exports_blocked_sections() -> None:
    blocked = WireExtensions(
        passthrough_sections={"layout": {"x": 1}},
        passthrough_versions={"layout": 9},
    )
    document = {WIRE_EXTENSIONS_KEY: blocked}
    stored = WireExtensions(passthrough_sections={"vendor": {"y": 2}})
    attach_package_carriers(document, stored.to_wire(), "lib")
    merged = document[WIRE_EXTENSIONS_KEY]
    assert merged.passthrough_sections == {"vendor": {"y": 2}, "layout": {"x": 1}}
    assert merged.passthrough_versions["layout"] == 9
