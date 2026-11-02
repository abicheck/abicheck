# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Edge branches of the graph closure renumbering and of merging package
carriers with an export's own blocked sections (ADR-062 D14/D18).

Each case names the input shape that takes the branch and asserts the
observable result against a value written out here, not one computed by the
code under test."""

from __future__ import annotations

import pytest

from abicheck.errors import SnapshotError
from abicheck.model import AbiSnapshot, Function
from abicheck.model.bundle_facts import BundleFacts
from abicheck.model.graph_facts import GraphEdge, GraphFact, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.serialization import SCHEMA_VERSION, bundle_facts_to_dict
from abicheck.storage.closure_graph_renumber import renumber_graph
from abicheck.storage.closure_identity import graph_rewrite_factory
from abicheck.storage.import_bundle_facts import import_bundle_facts
from abicheck.storage.package import InMemoryObjectStore, ObjectRef
from abicheck.storage.wire_extensions import (
    CARRIERS_OBJECT_KIND,
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
    # The winning fact hides the marker from the resolved attrs; only the
    # losing fact retains it.
    edge.facts = [
        GraphFact(producer="a", attrs={"t": "plain"}, confidence="high"),
        GraphFact(producer="b", attrs={"t": f"f<{_STRIPPED}>"}, confidence="low"),
    ]
    graph = _graph(a, b, edges=(edge,))
    assert "lambda" not in str(graph.edges[0].attrs)
    out = renumber_graph(graph, graph_rewrite_factory({}))
    assert out is not graph
    (renumbered,) = out.edges
    losing = next(f for f in renumbered.facts if f.producer == "b")
    assert losing.attrs["t"] == "f<(lambda:a.h#1)>"


def test_hint_without_a_closure_marker_needs_no_rewrite() -> None:
    # "(lambda" is a hint, but no stripped or raw closure coordinate follows.
    node = GraphNode(id="decl://g<(lambda)>", kind="source_decl", label="g")
    graph = _graph(node)
    assert renumber_graph(graph, graph_rewrite_factory({})) is graph


def test_raw_spelling_is_stripped_and_numbered() -> None:
    node = GraphNode(id=f"decl://f<{_RAW}>", kind="source_decl", label="f")
    out = renumber_graph(_graph(node), graph_rewrite_factory({}))
    assert [n.id for n in out.nodes] == ["decl://f<(lambda:a.h#1)>"]


class _Store:
    def __init__(self, raw: object) -> None:
        self.raw = raw

    def get(self, digest: str) -> object:
        return self.raw


def test_package_carriers_merge_with_the_exports_blocked_sections() -> None:
    blocked = WireExtensions(
        passthrough_sections={"layout": {"x": 1}},
        passthrough_versions={"layout": 9},
    )
    document = {WIRE_EXTENSIONS_KEY: blocked}
    stored = WireExtensions(passthrough_sections={"vendor": {"y": 2}})
    ref = ObjectRef(kind=CARRIERS_OBJECT_KIND, digest="sha256:" + "0" * 64)
    attach_package_carriers(document, _Store(stored.to_wire()), ref, "lib")
    merged = document[WIRE_EXTENSIONS_KEY]
    assert merged.passthrough_sections == {"vendor": {"y": 2}, "layout": {"x": 1}}
    assert merged.passthrough_versions["layout"] == 9


def test_a_carrier_reference_of_another_kind_is_refused() -> None:
    raw = WireExtensions(passthrough_sections={"vendor": {"y": 2}}).to_wire()
    document: dict = {}
    with pytest.raises(SnapshotError, match="carrier reference of kind 'section'"):
        attach_package_carriers(
            document,
            _Store(raw),
            ObjectRef(kind="section", digest="sha256:" + "0" * 64),
            "lib",
        )
    assert WIRE_EXTENSIONS_KEY not in document


def test_carriers_are_charged_before_they_are_decoded() -> None:
    raw = WireExtensions(passthrough_sections={"vendor": {"y": 2}}).to_wire()
    document: dict = {}
    seen: list[object] = []

    def refuse(carriers: object) -> None:
        seen.append(carriers)
        raise ValueError("over budget")

    ref = ObjectRef(kind=CARRIERS_OBJECT_KIND, digest="sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="over budget"):
        attach_package_carriers(document, _Store(raw), ref, "lib", refuse)
    assert seen == [raw]
    assert WIRE_EXTENSIONS_KEY not in document


def test_carriers_for_a_name_that_is_no_member_are_refused() -> None:
    snap = AbiSnapshot(
        library="liba.so.1",
        version="1",
        functions=[Function(name="f", mangled="f", return_type="int")],
    )
    doc = bundle_facts_to_dict(BundleFacts(per_library_snapshots={"liba.so.1": snap}))
    store = InMemoryObjectStore()
    with pytest.raises(
        ValueError, match=r"carriers name no bundle member: \['libb.so.1'\]"
    ):
        import_bundle_facts(
            doc,
            store=store,
            max_known_schema_version=SCHEMA_VERSION,
            carriers={"libb.so.1": WireExtensions()},
        )
    # Refused before any member was written.
    assert store._objects == {}
