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

"""Primitive-level contract of ``model.graph_entity_identity`` (invariant I1),
stated as properties per AGENTS.md "Primitive-level property tests".

The oracle is the generator: each case draws *entities* first (a ground-truth
integer per entity) and derives their spellings from it, so "same entity" and
"different entity" are known independently of the function under test.
"""

from __future__ import annotations

import random

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.model.graph_entity_identity import (
    UNRESOLVED_PREFIX,
    IdentityState,
    UnresolvedOccurrences,
    declaration_identity,
    identity_for_typedef,
    is_linker_name,
    type_identity,
    unresolved_identity,
)
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary

_ident = st.from_regex(r"[A-Za-z][A-Za-z0-9_]{0,8}", fullmatch=True)


def _itanium(entity: int) -> str:
    """A distinct, structurally real Itanium spelling per entity number."""
    name = f"f{entity}"
    return f"_Z{len(name)}{name}v"


@settings(max_examples=200, deadline=None)
@given(st.lists(st.integers(0, 50), min_size=1, max_size=30))
def test_same_linker_name_iff_same_node(entities: list[int]) -> None:
    ids = [declaration_identity(linker_name=_itanium(e)).node_id for e in entities]
    for a, ia in zip(entities, ids):
        for b, ib in zip(entities, ids):
            assert (a == b) == (ia == ib)


@settings(max_examples=200, deadline=None)
@given(st.integers(0, 10_000))
def test_macho_decoration_is_an_alias_of_one_node(entity: int) -> None:
    plain = declaration_identity(linker_name=_itanium(entity))
    decorated = declaration_identity(linker_name="_" + _itanium(entity))
    assert plain.node_id == decorated.node_id
    assert decorated.aliases == ("decl://_" + _itanium(entity),)
    assert plain.node_id not in decorated.aliases


@given(_ident)
def test_c_linkage_macho_decoration_is_an_alias(name: str) -> None:
    ident = declaration_identity(linker_name=f"_{name}", plain_name=name)
    assert (
        ident.node_id == declaration_identity(linker_name=name, plain_name=name).node_id
    )
    assert ident.aliases == (f"decl://_{name}",)


@pytest.mark.parametrize(
    "placeholder",
    [
        "__abicheck_ctor__ns::W(int)",
        "__abicheck_ctor__W()",
        "~ns::W",
        "~W",
        "f (int)",
        "",
    ],
)
def test_placeholders_are_not_linker_names(placeholder: str) -> None:
    assert not is_linker_name(placeholder)
    ident = declaration_identity(linker_name=placeholder)
    assert ident.state is IdentityState.UNRESOLVED
    assert ident.node_id.startswith(UNRESOLVED_PREFIX)


@pytest.mark.parametrize("real", ["_ZN2ns1fEi", "?f@@YAXH@Z", "c_fn", "_c_fn", "g"])
def test_real_linker_names_resolve(real: str) -> None:
    assert is_linker_name(real)
    assert declaration_identity(linker_name=real).resolved


_evidence = st.tuples(st.text(max_size=6), st.text(max_size=6), st.text(max_size=6))


@settings(max_examples=300, deadline=None)
@given(_evidence, _evidence)
def test_unresolved_distinct_evidence_never_collides(a: tuple, b: tuple) -> None:
    ia, ib = unresolved_identity("decl", *a), unresolved_identity("decl", *b)
    assert (a == b) == (ia.node_id == ib.node_id)


@settings(max_examples=200, deadline=None)
@given(_evidence, st.text(min_size=1, max_size=12), st.text(min_size=1, max_size=12))
def test_unresolved_never_collides_with_resolved(
    ev: tuple, linker: str, qname: str
) -> None:
    un = unresolved_identity("decl", *ev).node_id
    resolved = {
        declaration_identity(linker_name=linker).node_id,
        declaration_identity(qualified_name=qname, callable=False).node_id,
        type_identity(qname).node_id,
    }
    assert un not in resolved


@settings(max_examples=100, deadline=None)
@given(
    st.lists(st.integers(0, 3), min_size=1, max_size=12),
    st.randoms(use_true_random=False),
)
def test_identical_unresolved_evidence_is_kept_apart_order_independently(
    evidence_ids: list[int], rnd: random.Random
) -> None:
    def allocate(order: list[int]) -> list[str]:
        occ = UnresolvedOccurrences()
        return [
            occ.allocate(unresolved_identity("decl", str(e))).node_id for e in order
        ]

    ids = allocate(evidence_ids)
    assert len(set(ids)) == len(ids)  # n occurrences -> n nodes
    shuffled = list(evidence_ids)
    rnd.shuffle(shuffled)
    assert sorted(allocate(shuffled)) == sorted(ids)


def test_resolved_identities_pass_through_the_allocator() -> None:
    occ = UnresolvedOccurrences()
    ident = declaration_identity(linker_name="_Z1fv")
    assert occ.allocate(ident) == occ.allocate(ident) == ident


@settings(max_examples=100, deadline=None)
@given(st.lists(st.tuples(_ident, _ident), min_size=1, max_size=10))
def test_types_join_only_on_the_full_qualified_spelling(
    pairs: list[tuple[str, str]],
) -> None:
    """``a::X`` and ``b::X`` are two entities; so are ``ns::S`` and the
    inline-namespace spelling ``ns::v1::S`` (G15: no evidence here)."""
    names = [f"{ns}::{leaf}" for ns, leaf in pairs]
    ids = [type_identity(n).node_id for n in names]
    for a, ia in zip(names, ids):
        for b, ib in zip(names, ids):
            assert (a == b) == (ia == ib)
    assert type_identity("ns::S").node_id != type_identity("ns::v1::S").node_id


def test_overloads_need_a_signature_to_resolve() -> None:
    assert not declaration_identity(qualified_name="ns::f").resolved
    a = declaration_identity(qualified_name="ns::f", signature="sha256:1")
    b = declaration_identity(qualified_name="ns::f", signature="sha256:2")
    assert a.resolved and b.resolved and a.node_id != b.node_id
    assert declaration_identity(qualified_name="ns::k", callable=False).resolved


def test_typedef_idiom_is_one_node_and_tag_clash_is_two() -> None:
    record = type_identity("Foo").node_id
    assert identity_for_typedef("Foo", "struct Foo", ["Foo"]).node_id == record
    assert (
        identity_for_typedef("stat", "int", ["stat"]).node_id
        != type_identity("stat").node_id
    )
    assert (
        identity_for_typedef("Bar", "int", ["Foo"]).node_id
        == type_identity("Bar").node_id
    )


class TestGraphAliasTable:
    def test_alias_redirects_nodes_and_edges(self) -> None:
        g = SourceGraphSummary()
        g.add_identity_alias("decl://__Z1fv", "decl://_Z1fv")
        g.add_node(GraphNode(id="decl://__Z1fv", kind="source_decl", label="f"))
        g.add_node(GraphNode(id="type://T", kind="record_type", label="T"))
        g.add_edge(GraphEdge(src="decl://__Z1fv", dst="type://T", kind="DECL_HAS_TYPE"))
        assert [n.id for n in g.nodes] == ["decl://_Z1fv", "type://T"]
        assert g.edges[0].src == "decl://_Z1fv"
        assert g.has_node("decl://__Z1fv")

    def test_aliases_round_trip_and_feed_the_content_hash(self) -> None:
        g = SourceGraphSummary()
        g.add_identity_alias("decl://__Z1fv", "decl://_Z1fv")
        g.add_node(GraphNode(id="decl://_Z1fv", kind="source_decl", label="f"))
        back = SourceGraphSummary.from_dict(g.to_dict())
        assert back.identity_aliases == {"decl://__Z1fv": "decl://_Z1fv"}
        plain = SourceGraphSummary()
        plain.add_node(GraphNode(id="decl://_Z1fv", kind="source_decl", label="f"))
        assert plain.compute_graph_id() != g.compute_graph_id()

    def test_conflicting_or_late_alias_is_refused(self) -> None:
        g = SourceGraphSummary()
        g.add_identity_alias("decl://a", "decl://b")
        with pytest.raises(ValueError):
            g.add_identity_alias("decl://a", "decl://c")
        g.add_node(GraphNode(id="decl://x", kind="source_decl", label="x"))
        with pytest.raises(ValueError):
            g.add_identity_alias("decl://x", "decl://y")


class TestEdgeBranches:
    """Branches a normal dump rarely reaches, each with its own oracle."""

    def test_formatters_pass_an_unresolved_id_through(self) -> None:
        from abicheck.model.graph_identity import _decl_node_id, _type_node_id

        un = unresolved_identity("decl", "x").node_id
        assert _decl_node_id(un) == un
        assert _type_node_id(un) == un

    def test_nameless_record_and_enum_are_unresolved_and_distinct(self) -> None:
        from abicheck.model import EnumType, RecordType
        from abicheck.model.graph_entity_identity import (
            identity_for_enum,
            identity_for_record,
        )

        rec = identity_for_record(
            RecordType(name="", kind="struct", source_header="a.h")
        )
        en = identity_for_enum(EnumType(name="", source_header="b.h"))
        assert not rec.resolved and not en.resolved
        assert rec.node_id != en.node_id
        assert not type_identity("").resolved

    def test_register_identity_alias_refuses_a_taken_spelling(self) -> None:
        from abicheck.model.graph_entity_identity import register_identity_alias

        g = SourceGraphSummary()
        g.add_node(GraphNode(id="decl://_x", kind="source_decl", label="_x"))
        assert register_identity_alias(g, "decl://_x", "decl://x") is False
        assert register_identity_alias(g, "decl://__y", "decl://_y") is True
        assert g.resolve_node_id("decl://__y") == "decl://_y"

    def test_endpoint_key_round_trips_to_the_node_id(self) -> None:
        from abicheck.model.graph_entity_identity import endpoint_key
        from abicheck.model.graph_identity import _decl_node_id

        for ident in (
            declaration_identity(linker_name="_Z1fv"),
            unresolved_identity("decl", "q"),
        ):
            assert _decl_node_id(endpoint_key(ident)) == ident.node_id


def test_l4_conflicting_legacy_alias_joins_neither() -> None:
    """Two L4 entities sharing one legacy identity() spelling but distinct
    linker names: the shared spelling is ambiguous, so it aliases neither."""
    from abicheck.buildsource.source_abi import SourceEntity
    from abicheck.buildsource.source_graph_build_source_abi import (
        source_entity_decl_node_id,
    )

    g = SourceGraphSummary()
    a = SourceEntity(
        id="a",
        kind="function",
        qualified_name="f",
        signature_hash="sha256:1",
        names={"linker": "f_a"},
    )
    b = SourceEntity(
        id="b",
        kind="function",
        qualified_name="f",
        signature_hash="sha256:1",
        names={"linker": "f_b"},
    )
    assert source_entity_decl_node_id(g, a) == "decl://f_a"
    assert source_entity_decl_node_id(g, b) == "decl://f_b"
    assert "decl://f#sha256:1" not in g.identity_aliases
