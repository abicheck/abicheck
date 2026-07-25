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

"""Tests for ADR-046 D4 (scoped implementation): ``EntityResolver`` and
``SourceGraphSummary.resolve_entities()``/``SOURCE_GRAPH_VERSION = 2``.
"""

from __future__ import annotations

from abicheck.buildsource.entity_resolver import EntityConflict, EntityResolver
from abicheck.buildsource.graph_facts import GraphNode
from abicheck.buildsource.source_graph import SOURCE_GRAPH_VERSION, SourceGraphSummary


def _node(node_id: str, **attrs: object) -> GraphNode:
    return GraphNode(id=node_id, kind="source_decl", label=node_id, attrs=dict(attrs))


class TestEntityResolverResolve:
    def test_resolves_usr_to_canonical_id(self) -> None:
        r = EntityResolver()
        canonical = r.resolve(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        assert canonical == "usr:c:@F@foo#"
        assert r.canonical_id_for("decl://foo") == "usr:c:@F@foo#"

    def test_falls_back_to_mangled_name_without_usr(self) -> None:
        r = EntityResolver()
        canonical = r.resolve(_node("decl://foo", mangled_name="_Z3foov", name="foo"))
        assert canonical == "mangled:_Z3foov"

    def test_falls_back_to_qualified_name_signature_without_mangling(self) -> None:
        r = EntityResolver()
        canonical = r.resolve(_node("decl://ns::foo", qualified_name="ns::foo"))
        assert canonical.startswith("sig:")

    def test_resolve_is_idempotent_per_node_id(self) -> None:
        r = EntityResolver()
        n = _node("decl://foo", usr="c:@F@foo#", name="foo")
        first = r.resolve(n)
        second = r.resolve(n)
        assert first == second
        assert len(r.aliases) == 1

    def test_two_v1_ids_sharing_a_usr_both_get_the_canonical_alias(self) -> None:
        # Identity fragmentation: two different v1 ids (e.g. one from a
        # header-only pass, one from a build-integrated pass) for what a USR
        # proves is the same real declaration.
        r = EntityResolver()
        c1 = r.resolve(_node("decl://foo_v1", usr="c:@F@foo#", name="foo"))
        c2 = r.resolve(_node("decl://foo_v2_variant", usr="c:@F@foo#", name="foo"))
        assert c1 == c2
        assert r.canonical_id_for("decl://foo_v1") == c1
        assert r.canonical_id_for("decl://foo_v2_variant") == c1

    def test_conflict_recorded_for_second_v1_id_sharing_a_canonical_identity(
        self,
    ) -> None:
        r = EntityResolver()
        r.resolve(_node("decl://a", usr="c:@F@foo#", name="foo"))
        r.resolve(_node("decl://b", usr="c:@F@foo#", name="foo"))
        assert len(r.conflicts) == 1
        conflict = r.conflicts[0]
        assert conflict.canonical_id == "usr:c:@F@foo#"
        assert conflict.node_ids == ("decl://a", "decl://b")
        # The first-seen v1 id stays the representative.
        assert r.v1_id_for("usr:c:@F@foo#") == "decl://a"

    def test_distinct_entities_never_conflict(self) -> None:
        r = EntityResolver()
        r.resolve(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        r.resolve(_node("decl://bar", usr="c:@F@bar#", name="bar"))
        assert r.conflicts == []


class TestEntityResolverSerialization:
    def test_round_trip(self) -> None:
        r = EntityResolver()
        r.resolve(_node("decl://a", usr="c:@F@foo#", name="foo"))
        r.resolve(_node("decl://b", usr="c:@F@foo#", name="foo"))
        d = r.to_dict()
        restored = EntityResolver.from_dict(d)
        assert restored.aliases == r.aliases
        assert [c.to_dict() for c in restored.conflicts] == [
            c.to_dict() for c in r.conflicts
        ]
        # The representative v1 id survives the round trip too.
        assert restored.v1_id_for("usr:c:@F@foo#") == "decl://a"

    def test_from_dict_defaults_on_empty_input(self) -> None:
        r = EntityResolver.from_dict({})
        assert r.aliases == {}
        assert r.conflicts == []

    def test_conflict_to_dict_from_dict_round_trip(self) -> None:
        c = EntityConflict("usr:c:@F@foo#", ("decl://a", "decl://b"))
        assert EntityConflict.from_dict(c.to_dict()) == c


class TestSourceGraphSummaryEntityResolution:
    def test_schema_version_is_2(self) -> None:
        assert SOURCE_GRAPH_VERSION == 2
        assert SourceGraphSummary().schema_version == 2

    def test_resolve_entities_is_opt_in(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        # Not called automatically by add_node/__post_init__/finalize.
        assert g.entity_resolver.aliases == {}
        g.finalize()
        assert g.entity_resolver.aliases == {}

    def test_resolve_entities_populates_aliases_for_every_node(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        g.add_node(_node("decl://bar", mangled_name="_Z3barv", name="bar"))
        g.resolve_entities()
        assert g.entity_resolver.canonical_id_for("decl://foo") == "usr:c:@F@foo#"
        assert g.entity_resolver.canonical_id_for("decl://bar") == "mangled:_Z3barv"

    def test_resolve_entities_is_safe_to_call_after_more_nodes_are_added(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        g.resolve_entities()
        g.add_node(_node("decl://bar", usr="c:@F@bar#", name="bar"))
        g.resolve_entities()
        assert set(g.entity_resolver.aliases) == {"decl://foo", "decl://bar"}

    def test_to_dict_omits_entity_resolver_when_unresolved(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        d = g.to_dict()
        assert "entity_resolver" not in d

    def test_to_dict_includes_entity_resolver_once_resolved(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        g.resolve_entities()
        d = g.to_dict()
        assert d["entity_resolver"]["aliases"]["decl://foo"] == "usr:c:@F@foo#"

    def test_round_trip_through_to_dict_from_dict(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        g.resolve_entities()
        restored = SourceGraphSummary.from_dict(g.to_dict())
        assert restored.entity_resolver.aliases == g.entity_resolver.aliases

    def test_v1_pack_with_no_entity_resolver_key_loads_and_matches(self) -> None:
        # A v1 pack (schema_version=1, no entity_resolver key at all) must
        # still load correctly with no forced re-collection -- the ADR-046
        # D4 backward-compatibility contract this scoped implementation
        # exists to satisfy.
        g = SourceGraphSummary()
        g.add_node(_node("decl://foo", usr="c:@F@foo#", name="foo"))
        v1_dict = g.to_dict()
        v1_dict["schema_version"] = 1
        v1_dict.pop("entity_resolver", None)

        restored = SourceGraphSummary.from_dict(v1_dict)
        assert restored.schema_version == 1
        assert restored.entity_resolver.aliases == {}
        assert restored.nodes[0].id == "decl://foo"
        # The graph is fully usable via its existing v1 ids -- resolve_entities()
        # still works on demand, same as any other summary.
        restored.resolve_entities()
        assert restored.entity_resolver.canonical_id_for("decl://foo") == "usr:c:@F@foo#"
