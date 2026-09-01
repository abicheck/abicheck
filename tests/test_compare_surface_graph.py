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

"""``compare.surface_graph.build_public_surface_facts`` (ADR-063 Phase 3 D5)."""

from __future__ import annotations

from abicheck.compare.surface_graph import (
    EDGE_KIND_DECLARES,
    EDGE_KIND_EXPORTS,
    EDGE_KIND_REFERENCES,
    NODE_KIND_DECLARATION,
    NODE_KIND_HEADER,
    NODE_KIND_SYMBOL,
    NODE_KIND_TYPE,
    build_public_surface_facts,
)
from abicheck.model.declarations import Function, Variable
from abicheck.model.entities import RecordType, TypeField
from abicheck.model.snapshot import AbiSnapshot
from abicheck.model.source_graph import SourceGraphSummary


def _snapshot(**kwargs: object) -> AbiSnapshot:
    return AbiSnapshot(library="libfoo.so", version="1.0", **kwargs)  # type: ignore[arg-type]


class TestDeclarationNodes:
    def test_function_gets_a_declaration_node(self) -> None:
        fn = Function(
            name="ns::f", mangled="_ZN2ns1fEv", return_type="void", source_header="a.h"
        )
        snap = _snapshot(functions=[fn])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        decl_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_DECLARATION]
        assert len(decl_nodes) == 1
        assert decl_nodes[0].label == "ns::f"

    def test_variable_gets_a_declaration_node(self) -> None:
        var = Variable(
            name="ns::g", mangled="_ZN2ns1gE", type="int", source_header="a.h"
        )
        snap = _snapshot(variables=[var])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        decl_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_DECLARATION]
        assert len(decl_nodes) == 1
        assert decl_nodes[0].label == "ns::g"

    def test_two_distinct_functions_get_two_distinct_nodes(self) -> None:
        a = Function(name="f", mangled="_Z1fv", return_type="void")
        b = Function(name="g", mangled="_Z1gv", return_type="void")
        snap = _snapshot(functions=[a, b])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        decl_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_DECLARATION]
        assert len({n.id for n in decl_nodes}) == 2


class TestHeaderDeclaresEdges:
    def test_header_node_and_declares_edge(self) -> None:
        fn = Function(
            name="f", mangled="_Z1fv", return_type="void", source_header="pub.h"
        )
        snap = _snapshot(functions=[fn])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        header_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_HEADER]
        assert [n.label for n in header_nodes] == ["pub.h"]
        declares = [e for e in graph.edges if e.kind == EDGE_KIND_DECLARES]
        assert len(declares) == 1
        assert declares[0].src == header_nodes[0].id

    def test_no_source_header_means_no_declares_edge(self) -> None:
        fn = Function(name="f", mangled="_Z1fv", return_type="void", source_header=None)
        snap = _snapshot(functions=[fn])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        assert not [e for e in graph.edges if e.kind == EDGE_KIND_DECLARES]
        assert not [n for n in graph.nodes if n.kind == NODE_KIND_HEADER]


class TestReferencesEdges:
    def test_function_signature_references_a_declared_type(self) -> None:
        rec = RecordType(name="Widget", kind="struct", qualified_name="Widget")
        fn = Function(name="make", mangled="_Z4makev", return_type="Widget*")
        snap = _snapshot(functions=[fn], types=[rec])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        refs = [e for e in graph.edges if e.kind == EDGE_KIND_REFERENCES]
        assert len(refs) == 1
        type_node_ids = {n.id for n in graph.nodes if n.kind == NODE_KIND_TYPE}
        assert refs[0].dst in type_node_ids

    def test_record_field_references_another_record(self) -> None:
        inner = RecordType(name="Inner", kind="struct", qualified_name="Inner")
        outer = RecordType(
            name="Outer",
            kind="struct",
            qualified_name="Outer",
            fields=[TypeField(name="i", type="Inner")],
        )
        snap = _snapshot(types=[inner, outer])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        refs = [e for e in graph.edges if e.kind == EDGE_KIND_REFERENCES]
        assert len(refs) == 1

    def test_unresolvable_reference_produces_no_dangling_edge(self) -> None:
        # "std::string" names nothing declared in this snapshot -- must be
        # silently skipped, not turned into an edge pointing at a
        # never-registered node id.
        fn = Function(name="f", mangled="_Z1fv", return_type="std::string")
        snap = _snapshot(functions=[fn])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        assert not [e for e in graph.edges if e.kind == EDGE_KIND_REFERENCES]

    def test_self_reference_does_not_produce_a_self_edge(self) -> None:
        # A record whose own field happens to name itself indirectly via a
        # pointer (e.g. an intrusive list node) must not create src==dst.
        rec = RecordType(
            name="Node",
            kind="struct",
            qualified_name="Node",
            fields=[TypeField(name="next", type="Node*")],
        )
        snap = _snapshot(types=[rec])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        for e in graph.edges:
            assert e.src != e.dst

    def test_ambiguous_bare_name_resolves_to_neither_candidate(self) -> None:
        # ns1::Foo and ns2::Foo both register the bare key "Foo" -- a naive
        # first-wins index would silently pick whichever type happened to
        # iterate first, making an unqualified "Foo*" reference edge order-
        # dependent and possibly wrong (Codex review, PR #962). Mirroring
        # surface.py's own ambiguous_type_names convention: the bare key is
        # dropped entirely rather than resolved arbitrarily, so a signature
        # naming only "Foo" (never "ns1::Foo"/"ns2::Foo") produces no
        # references edge at all instead of a wrong one.
        foo1 = RecordType(name="Foo", kind="struct", qualified_name="ns1::Foo")
        foo2 = RecordType(name="Foo", kind="struct", qualified_name="ns2::Foo")
        fn = Function(name="make", mangled="_Z4makev", return_type="Foo*")
        snap = _snapshot(functions=[fn], types=[foo1, foo2])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        refs = [e for e in graph.edges if e.kind == EDGE_KIND_REFERENCES]
        assert refs == []

    def test_ambiguous_bare_name_does_not_affect_the_qualified_reference(self) -> None:
        # The exact same ambiguous pair, but the referencing signature names
        # the qualified spelling directly -- that must still resolve, since
        # only the bare key is ambiguous, not the qualified one.
        foo1 = RecordType(name="Foo", kind="struct", qualified_name="ns1::Foo")
        foo2 = RecordType(name="Foo", kind="struct", qualified_name="ns2::Foo")
        fn = Function(name="make", mangled="_Z4makev", return_type="ns1::Foo*")
        snap = _snapshot(functions=[fn], types=[foo1, foo2])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        refs = [e for e in graph.edges if e.kind == EDGE_KIND_REFERENCES]
        assert len(refs) == 1


class TestExportsEdges:
    def test_symbol_node_and_exports_edge(self) -> None:
        fn = Function(name="f", mangled="_Z1fv", return_type="void")
        snap = _snapshot(functions=[fn])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        symbol_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_SYMBOL]
        assert [n.label for n in symbol_nodes] == ["_Z1fv"]
        exports = [e for e in graph.edges if e.kind == EDGE_KIND_EXPORTS]
        assert len(exports) == 1
        assert exports[0].src == symbol_nodes[0].id

    def test_no_mangled_name_means_no_symbol_node(self) -> None:
        fn = Function(name="f", mangled="", return_type="void")
        snap = _snapshot(functions=[fn])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        assert not [n for n in graph.nodes if n.kind == NODE_KIND_SYMBOL]


class TestIdempotence:
    def test_calling_twice_on_the_same_graph_does_not_duplicate_nodes(self) -> None:
        fn = Function(
            name="f", mangled="_Z1fv", return_type="void", source_header="a.h"
        )
        snap = _snapshot(functions=[fn])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        node_count_after_first = len(graph.nodes)
        edge_count_after_first = len(graph.edges)
        build_public_surface_facts(snap, graph)
        assert len(graph.nodes) == node_count_after_first
        assert len(graph.edges) == edge_count_after_first

    def test_shares_a_graph_with_data_another_builder_already_wrote(self) -> None:
        from abicheck.model.graph_facts import GraphNode

        graph = SourceGraphSummary()
        graph.add_node(
            GraphNode(id="decl://preexisting", kind="declaration", label="pre")
        )
        fn = Function(name="f", mangled="_Z1fv", return_type="void")
        snap = _snapshot(functions=[fn])
        build_public_surface_facts(snap, graph)
        assert graph.has_node("decl://preexisting")
        assert len([n for n in graph.nodes if n.kind == NODE_KIND_DECLARATION]) == 2


class TestApproximateFallbackWhenEntityIdUnpopulated:
    """Every declaration constructed above has ``entity_id=None`` (the test
    fixtures never set it) -- this whole test module is therefore already
    exercising the approximate-fallback path exclusively; this class just
    makes that coverage explicit and pins the collision-avoidance property
    the fallback itself promises."""

    def test_two_same_leaf_names_in_different_namespaces_do_not_collide(self) -> None:
        a = Function(name="ns_a::f", mangled="_ZN4ns_a1fEv", return_type="void")
        b = Function(name="ns_b::f", mangled="_ZN4ns_b1fEv", return_type="void")
        snap = _snapshot(functions=[a, b])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        decl_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_DECLARATION]
        assert len({n.id for n in decl_nodes}) == 2

    def test_a_function_and_a_record_sharing_one_bare_name_do_not_collide(
        self,
    ) -> None:
        # Legal C: `struct stat { ... };` alongside a function `int stat(...)`.
        # Both share the qualified name "stat"; without a per-kind namespace
        # in the approximate-fallback id, both would resolve to the same
        # node id and silently merge into one (Codex review, PR #962).
        fn = Function(name="stat", mangled="stat", return_type="int")
        rec = RecordType(name="stat", kind="struct", qualified_name="stat")
        snap = _snapshot(functions=[fn], types=[rec])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        decl_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_DECLARATION]
        type_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_TYPE]
        assert len(decl_nodes) == 1
        assert len(type_nodes) == 1
        assert decl_nodes[0].id != type_nodes[0].id


class TestReferencedIdentifiersAttr:
    """``referenced_identifiers`` (ADR-063 Phase 3's follow-up traversal
    migration): the union of every type-identifier string a declaration/
    type's own signature/fields/bases/target names, cached on the node so
    ``policy.public_surface`` can read it instead of re-parsing the same
    strings a second time."""

    def test_function_node_carries_its_own_referenced_identifiers(self) -> None:
        fn = Function(
            name="make", mangled="_Z4makev", return_type="Widget*", params=[]
        )
        snap = _snapshot(functions=[fn])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        decl_node = next(n for n in graph.nodes if n.kind == NODE_KIND_DECLARATION)
        assert "Widget" in decl_node.attrs["referenced_identifiers"]

    def test_record_node_unions_field_and_base_identifiers(self) -> None:
        rec = RecordType(
            name="Outer",
            kind="struct",
            qualified_name="Outer",
            fields=[TypeField(name="i", type="Inner")],
            bases=["Base"],
        )
        snap = _snapshot(types=[rec])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        type_node = next(n for n in graph.nodes if n.kind == NODE_KIND_TYPE)
        assert {"Inner", "Base"} <= set(type_node.attrs["referenced_identifiers"])

    def test_colliding_approximate_declarations_union_rather_than_drop(self) -> None:
        # Two overloads sharing one demangled name with no resolved
        # entity_id collide onto the same approximate declaration node id
        # (this module's own documented fallback). Each references a
        # different, otherwise-unrelated type; the merged node's
        # referenced_identifiers must carry BOTH, not silently keep only
        # whichever registration's fact won the generic cross-producer
        # merge tie-break (the anti-hiding regression this precomputed
        # union step exists to prevent).
        overload_a = Function(name="f", mangled="_Z1fi", return_type="Alpha*")
        overload_b = Function(name="f", mangled="_Z1fd", return_type="Beta*")
        snap = _snapshot(functions=[overload_a, overload_b])
        graph = SourceGraphSummary()
        build_public_surface_facts(snap, graph)
        decl_nodes = [n for n in graph.nodes if n.kind == NODE_KIND_DECLARATION]
        assert len(decl_nodes) == 1  # confirms the collision actually occurred
        assert {"Alpha", "Beta"} <= set(decl_nodes[0].attrs["referenced_identifiers"])
