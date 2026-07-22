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

"""Tests for ADR-046 D2 and D1 (G29 Phase 2, slices 1 and 3):

- D2 — the evidence-preserving node/edge merge: ``GraphFact``/``FactConflict``,
  order-independent ``resolved`` folding, conflict recording, and v1-pack
  read-compatibility.
- D1 — role-aware edge identity: ``GraphEdge.relation_key()``/
  ``edge_relation_key()``.
"""

from __future__ import annotations

from abicheck.buildsource.graph_facts import edge_relation_key
from abicheck.buildsource.source_graph import (
    CONF_HIGH,
    CONF_REDUCED,
    CONF_UNKNOWN,
    FactConflict,
    GraphEdge,
    GraphFact,
    GraphNode,
    SourceGraphSummary,
    fold_source_edges,
)


def _node(provenance: str, confidence: str, **attrs: object) -> GraphNode:
    return GraphNode(
        id="decl://foo",
        kind="source_decl",
        label="foo",
        provenance=provenance,
        confidence=confidence,
        attrs=dict(attrs),
    )


class TestSingleProducer:
    def test_single_registration_synthesizes_one_fact(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("build_evidence", CONF_HIGH, visibility="public_header"))
        (n,) = g.nodes
        assert n.attrs == {"visibility": "public_header"}
        assert n.resolved == {"visibility": "public_header"}
        assert n.conflicts == []
        assert [f.producer for f in n.facts] == ["build_evidence"]

    def test_re_registration_by_same_producer_is_idempotent(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("build_evidence", CONF_HIGH, visibility="public_header"))
        g.add_node(_node("build_evidence", CONF_HIGH, visibility="public_header"))
        (n,) = g.nodes
        assert len(n.facts) == 1
        assert n.attrs == {"visibility": "public_header"}


class TestEvidencePreservingMerge:
    def test_second_producer_no_longer_silently_dropped(self) -> None:
        """The v1 bug D2 fixes: a second producer's facts used to vanish."""
        g = SourceGraphSummary()
        g.add_node(_node("build_evidence", CONF_REDUCED, visibility="public_header"))
        g.add_node(_node("clang-header-graph", CONF_HIGH, is_virtual=True))
        (n,) = g.nodes
        assert len(n.facts) == 2
        # Both keys survive the merge — neither producer's attrs vanished.
        assert n.resolved == {"visibility": "public_header", "is_virtual": True}
        assert n.attrs == n.resolved

    def test_higher_confidence_wins_a_shared_key(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("weak-producer", CONF_REDUCED, is_virtual=False))
        g.add_node(_node("strong-producer", CONF_HIGH, is_virtual=True))
        (n,) = g.nodes
        assert n.resolved["is_virtual"] is True
        assert n.confidence == CONF_HIGH
        assert n.provenance == "strong-producer"

    def test_merge_is_order_independent(self) -> None:
        """Same facts, opposite registration order -> identical resolved/conflicts."""
        forward = SourceGraphSummary()
        forward.add_node(_node("weak-producer", CONF_REDUCED, is_virtual=False))
        forward.add_node(_node("strong-producer", CONF_HIGH, is_virtual=True))

        backward = SourceGraphSummary()
        backward.add_node(_node("strong-producer", CONF_HIGH, is_virtual=True))
        backward.add_node(_node("weak-producer", CONF_REDUCED, is_virtual=False))

        (fwd_node,) = forward.nodes
        (bwd_node,) = backward.nodes
        assert fwd_node.resolved == bwd_node.resolved
        assert fwd_node.confidence == bwd_node.confidence
        assert fwd_node.provenance == bwd_node.provenance

    def test_equal_confidence_tie_broken_by_producer_name_not_arrival_order(
        self,
    ) -> None:
        first = SourceGraphSummary()
        first.add_node(_node("zzz-producer", CONF_HIGH, is_virtual=False))
        first.add_node(_node("aaa-producer", CONF_HIGH, is_virtual=True))

        second = SourceGraphSummary()
        second.add_node(_node("aaa-producer", CONF_HIGH, is_virtual=True))
        second.add_node(_node("zzz-producer", CONF_HIGH, is_virtual=False))

        (n1,) = first.nodes
        (n2,) = second.nodes
        # "aaa-producer" sorts first among equal-confidence facts either way.
        assert n1.resolved["is_virtual"] is True
        assert n2.resolved["is_virtual"] is True
        assert n1.provenance == n2.provenance == "aaa-producer"

    def test_same_producer_same_confidence_tie_broken_by_content_not_arrival(
        self,
    ) -> None:
        # Codex review on PR #620: two facts sharing (producer, confidence)
        # but differing attrs used to tie on the sort key, so Python's stable
        # sort fell back to arrival order -- whichever was registered/loaded
        # first silently won, breaking order-independence for the "same
        # producer refines its own output" case (e.g. an initial
        # registration and a later backfill from the same producer string).
        forward = SourceGraphSummary()
        forward.add_node(_node("p", CONF_HIGH, is_virtual=False))
        forward.add_node(_node("p", CONF_HIGH, is_virtual=True))

        backward = SourceGraphSummary()
        backward.add_node(_node("p", CONF_HIGH, is_virtual=True))
        backward.add_node(_node("p", CONF_HIGH, is_virtual=False))

        (fwd_node,) = forward.nodes
        (bwd_node,) = backward.nodes
        assert len(fwd_node.facts) == len(bwd_node.facts) == 2
        assert fwd_node.resolved == bwd_node.resolved

    def test_genuine_disagreement_is_recorded_as_conflict_not_dropped(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("producer-a", CONF_HIGH, is_virtual=True))
        g.add_node(_node("producer-b", CONF_HIGH, is_virtual=False))
        (n,) = g.nodes
        assert len(n.conflicts) == 1
        conflict = n.conflicts[0]
        assert conflict.key == "is_virtual"
        # producer-a sorts before producer-b -> producer-a's value wins.
        assert conflict.winning_producer == "producer-a"
        assert conflict.winning_value is True
        assert conflict.losing_producer == "producer-b"
        assert conflict.losing_value is False
        # The winning value is still visible in resolved/attrs — advisory,
        # not authoritative (ADR-028 D3), but not silently lost either.
        assert n.resolved["is_virtual"] is True

    def test_agreeing_facts_from_different_producers_are_not_a_conflict(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("producer-a", CONF_HIGH, is_virtual=True))
        g.add_node(_node("producer-b", CONF_HIGH, is_virtual=True))
        (n,) = g.nodes
        assert n.conflicts == []
        assert n.resolved["is_virtual"] is True

    def test_re_adding_an_already_multi_fact_node_preserves_all_its_facts(
        self,
    ) -> None:
        # Codex review on PR #620: add_node's duplicate branch used to call
        # register_fact with just the incoming node's own top-level
        # provenance/confidence/attrs -- fine for a bare single-producer
        # GraphNode(...), but an incoming node that already carries multiple
        # facts of its own (e.g. re-added from an already evidence-merged
        # graph) had its whole fact history collapsed into one flattened
        # fact, discarding the individual per-producer facts.
        g = SourceGraphSummary()
        g.add_node(_node("producer-a", CONF_HIGH, is_virtual=True))
        incoming = GraphNode(
            id="decl://foo",
            kind="source_decl",
            facts=[
                GraphFact(producer="producer-b", confidence=CONF_HIGH, attrs={"x": 1}),
                GraphFact(producer="producer-c", confidence=CONF_REDUCED, attrs={"y": 2}),
            ],
        )
        g.add_node(incoming)
        (n,) = g.nodes
        assert {f.producer for f in n.facts} == {"producer-a", "producer-b", "producer-c"}
        assert n.resolved == {"is_virtual": True, "x": 1, "y": 2}


class TestEdgeMerge:
    def test_edge_facts_merge_the_same_way_as_node_facts(self) -> None:
        g = SourceGraphSummary()
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="decl://b",
                kind="DECL_CALLS_DECL",
                provenance="call_graph",
                confidence=CONF_HIGH,
                attrs={"call_kind": "direct"},
            )
        )
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="decl://b",
                kind="DECL_CALLS_DECL",
                provenance="header_call_graph",
                confidence=CONF_REDUCED,
                attrs={"resolution": "exact"},
            )
        )
        (e,) = g.edges
        assert len(e.facts) == 2
        assert e.resolved == {"call_kind": "direct", "resolution": "exact"}
        assert e.conflicts == []

    def test_re_adding_an_already_multi_fact_edge_preserves_all_its_facts(
        self,
    ) -> None:
        # Edge analogue of the same-named node test above (Codex review).
        g = SourceGraphSummary()
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="decl://b",
                kind="DECL_CALLS_DECL",
                provenance="producer-a",
                confidence=CONF_HIGH,
                attrs={"x": 1},
            )
        )
        incoming = GraphEdge(
            src="decl://a",
            dst="decl://b",
            kind="DECL_CALLS_DECL",
            facts=[
                GraphFact(producer="producer-b", confidence=CONF_HIGH, attrs={"y": 2}),
                GraphFact(producer="producer-c", confidence=CONF_REDUCED, attrs={"z": 3}),
            ],
        )
        g.add_edge(incoming)
        (e,) = g.edges
        assert {f.producer for f in e.facts} == {"producer-a", "producer-b", "producer-c"}
        assert e.resolved == {"x": 1, "y": 2, "z": 3}

    def test_add_edge_dedups_true_duplicates_on_relation_key(self) -> None:
        # Same (src, dst, kind, role) -- role empty on both -- still merges
        # into one edge object, exactly like before ADR-046 D1.
        g = SourceGraphSummary()
        edge = GraphEdge(src="decl://a", dst="decl://b", kind="DECL_CALLS_DECL")
        g.add_edge(edge)
        g.add_edge(edge)
        assert len(g.edges) == 1
        assert g.has_node("decl://a") is False  # sanity: has_node is node-only

    def test_add_edge_preserves_role_distinct_edges_as_separate_objects(
        self,
    ) -> None:
        # Codex review on PR #620: a function that both returns and takes the
        # same private type produces two real, role-distinct DECL_HAS_TYPE
        # edges sharing (src, dst, kind). Deduping add_edge on the coarse
        # key() alone folded the second into the first's facts, so only one
        # role ever survived -- relation_key() could never actually expose
        # both (src, dst, kind, "return") and (src, dst, kind, "param") from
        # a graph built through add_edge, even though the capability existed.
        g = SourceGraphSummary()
        g.add_edge(
            GraphEdge(
                src="decl://f",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                provenance="type_graph",
                confidence=CONF_HIGH,
                attrs={"role": "return"},
            )
        )
        g.add_edge(
            GraphEdge(
                src="decl://f",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                provenance="type_graph",
                confidence=CONF_HIGH,
                attrs={"role": "param"},
            )
        )
        assert len(g.edges) == 2
        relation_keys = {e.relation_key() for e in g.edges}
        assert relation_keys == {
            ("decl://f", "type://T", "DECL_HAS_TYPE", "return"),
            ("decl://f", "type://T", "DECL_HAS_TYPE", "param"),
        }
        # Neither edge's own facts/resolved were contaminated by the other's.
        for e in g.edges:
            assert e.conflicts == []
            assert len(e.facts) == 1
        # The coarse key() still collapses both, as documented -- callers
        # that only need family-level (not role-level) precision still can.
        assert {e.key() for e in g.edges} == {("decl://f", "type://T", "DECL_HAS_TYPE")}

    def test_add_edge_resolves_role_only_in_facts_before_indexing(self) -> None:
        # Codex review on PR #620: relation_key() was computed before
        # ensure_facts_and_resolve() ran, so an edge whose role lives only in
        # `facts` (attrs still empty at construction time) got indexed under
        # the blank-role key instead of its true post-resolution one. Two
        # genuinely same-role edges built this way must still merge into one.
        g = SourceGraphSummary()
        g.add_edge(
            GraphEdge(
                src="decl://f",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                facts=[
                    GraphFact(
                        producer="type_graph", confidence=CONF_HIGH, attrs={"role": "param"}
                    )
                ],
            )
        )
        g.add_edge(
            GraphEdge(
                src="decl://f",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                facts=[
                    GraphFact(
                        producer="type_graph", confidence=CONF_HIGH, attrs={"role": "param"}
                    )
                ],
            )
        )
        assert len(g.edges) == 1
        (edge,) = g.edges
        assert edge.relation_key() == ("decl://f", "type://T", "DECL_HAS_TYPE", "param")

    def test_add_edge_resolves_role_only_in_facts_stays_role_distinct(self) -> None:
        # Same construction shape as above, but genuinely different roles --
        # must still stay two separate edges, not collapse onto the shared
        # blank-role key a pre-resolution relation_key() would have computed.
        g = SourceGraphSummary()
        g.add_edge(
            GraphEdge(
                src="decl://f",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                facts=[GraphFact(producer="type_graph", attrs={"role": "return"})],
            )
        )
        g.add_edge(
            GraphEdge(
                src="decl://f",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                facts=[GraphFact(producer="type_graph", attrs={"role": "param"})],
            )
        )
        assert len(g.edges) == 2
        assert {e.relation_key() for e in g.edges} == {
            ("decl://f", "type://T", "DECL_HAS_TYPE", "return"),
            ("decl://f", "type://T", "DECL_HAS_TYPE", "param"),
        }


class TestConstructorSeededGraphs:
    """``SourceGraphSummary(nodes=[...], edges=[...])`` bypasses ``add_node``/
    ``add_edge`` (used throughout the test suite and by some builders) — the
    D2 facts/resolved fields must still be populated by ``__post_init__``."""

    def test_constructor_seeded_node_gets_synthesized_facts(self) -> None:
        node = GraphNode(
            id="decl://foo", kind="source_decl", attrs={"visibility": "public_header"}
        )
        g = SourceGraphSummary(nodes=[node])
        assert node.facts == [
            GraphFact(
                producer="",
                confidence=CONF_UNKNOWN,
                attrs={"visibility": "public_header"},
            )
        ]
        assert node.resolved == {"visibility": "public_header"}
        assert g.has_node("decl://foo")

    def test_constructor_seeded_edge_gets_synthesized_facts(self) -> None:
        edge = GraphEdge(src="decl://a", dst="decl://b", kind="DECL_CALLS_DECL")
        SourceGraphSummary(edges=[edge])
        assert len(edge.facts) == 1
        assert edge.resolved == {}

    def test_constructor_seeded_edge_index_uses_resolved_relation_key(self) -> None:
        # Codex review on PR #620: __post_init__ built _edge_keys/_edge_by_key
        # before resolving constructor-seeded edges, so an edge whose role
        # lives only in `facts` (not yet mirrored into `attrs`) was indexed
        # under the blank-role key instead of its true, post-resolution one.
        edge = GraphEdge(
            src="decl://f",
            dst="type://T",
            kind="DECL_HAS_TYPE",
            facts=[GraphFact(producer="type_graph", attrs={"role": "param"})],
        )
        g = SourceGraphSummary(edges=[edge])
        rkey = edge.relation_key()
        assert rkey == ("decl://f", "type://T", "DECL_HAS_TYPE", "param")
        assert rkey in g._edge_keys
        assert g._edge_by_key[rkey] is edge


class TestSerializationRoundTrip:
    def test_v2_pack_round_trips_facts_resolved_conflicts(self) -> None:
        g = SourceGraphSummary()
        g.add_node(_node("producer-a", CONF_HIGH, is_virtual=True))
        g.add_node(_node("producer-b", CONF_HIGH, is_virtual=False))
        g.finalize()
        d = g.to_dict()
        reloaded = SourceGraphSummary.from_dict(d)
        (orig,) = g.nodes
        (loaded,) = reloaded.nodes
        assert loaded.resolved == orig.resolved
        assert len(loaded.facts) == len(orig.facts) == 2
        assert len(loaded.conflicts) == len(orig.conflicts) == 1
        assert loaded.conflicts[0].key == orig.conflicts[0].key

    def test_v1_pack_with_no_facts_key_loads_and_synthesizes_one_fact(self) -> None:
        """A pack written before ADR-046 D2 has no "facts"/"resolved"/
        "conflicts" keys at all. A v2 reader must still load it, synthesizing
        the single fact its attrs/provenance/confidence already imply — no
        forced re-collection."""
        v1_node_dict = {
            "id": "decl://foo",
            "kind": "source_decl",
            "label": "foo",
            "attrs": {"visibility": "public_header"},
            "provenance": "build_evidence",
            "confidence": CONF_HIGH,
        }
        node = GraphNode.from_dict(v1_node_dict)
        assert node.resolved == {"visibility": "public_header"}
        assert len(node.facts) == 1
        assert node.facts[0].producer == "build_evidence"
        assert node.facts[0].confidence == CONF_HIGH
        assert node.conflicts == []
        # Original v1 fields are untouched.
        assert node.attrs == {"visibility": "public_header"}
        assert node.provenance == "build_evidence"
        assert node.confidence == CONF_HIGH

    def test_v1_pack_edge_with_no_facts_key_loads(self) -> None:
        v1_edge_dict = {
            "edge": "DECL_CALLS_DECL",
            "src": "decl://a",
            "dst": "decl://b",
            "provenance": "call_graph",
            "confidence": CONF_HIGH,
            "attrs": {"call_kind": "direct"},
        }
        edge = GraphEdge.from_dict(v1_edge_dict)
        assert edge.resolved == {"call_kind": "direct"}
        assert len(edge.facts) == 1
        assert edge.facts[0].producer == "call_graph"

    def test_fact_and_conflict_to_dict_from_dict_round_trip(self) -> None:
        fact = GraphFact(producer="p", confidence=CONF_HIGH, attrs={"k": "v"})
        assert GraphFact.from_dict(fact.to_dict()) == fact
        conflict = FactConflict(
            key="is_virtual",
            winning_value=True,
            winning_producer="a",
            losing_value=False,
            losing_producer="b",
        )
        assert FactConflict.from_dict(conflict.to_dict()) == conflict


class TestPostRegistrationBackfillSurvivesRoundTrip:
    """Regression (Codex review on PR #620): a producer that backfills an
    already-registered node's attrs (``fold_source_edges``'s/
    ``augment_graph_with_types``'s ``defined_in_project``/``def_file``
    marker) must go through ``register_fact``, not a direct
    ``existing.attrs[...] = ...`` mutation — a direct mutation is invisible
    to ``facts``/``resolved``, so ``ensure_facts_and_resolve`` silently drops
    it on the next ``to_dict()``/``from_dict()`` round-trip (a persisted pack
    reload)."""

    def test_fold_source_edges_backfill_survives_round_trip(self) -> None:
        g = SourceGraphSummary()
        g.add_node(GraphNode(id="decl://b", kind="source_decl", provenance="earlier"))
        fold_source_edges(
            g,
            [
                {
                    "edge": "DECL_CALLS_DECL",
                    "src": "a",
                    "dst": "b",
                    "attrs": {"dst_file": "src/detail/helper.h"},
                }
            ],
            frozenset({"src/detail/helper.h"}),
        )
        before = next(n for n in g.nodes if n.id == "decl://b")
        assert before.attrs.get("defined_in_project") is True
        assert before.attrs.get("def_file") == "src/detail/helper.h"

        reloaded = SourceGraphSummary.from_dict(g.to_dict())
        after = next(n for n in reloaded.nodes if n.id == "decl://b")
        assert after.attrs.get("defined_in_project") is True
        assert after.attrs.get("def_file") == "src/detail/helper.h"


class TestRelationKey:
    """ADR-046 D1: role-aware edge identity."""

    def test_relation_key_adds_role_to_coarse_key(self) -> None:
        edge = GraphEdge(
            src="decl://a",
            dst="type://T",
            kind="DECL_HAS_TYPE",
            provenance="type_graph",
            confidence=CONF_HIGH,
            attrs={"role": "return"},
        )
        assert edge.key() == ("decl://a", "type://T", "DECL_HAS_TYPE")
        assert edge.relation_key() == (
            "decl://a",
            "type://T",
            "DECL_HAS_TYPE",
            "return",
        )

    def test_relation_key_distinguishes_edges_that_collapse_on_key(self) -> None:
        # Two structurally different dependencies sharing (src, dst, kind) --
        # a type used as a return type on one edge, a param type on another.
        return_edge = GraphEdge(
            src="decl://f",
            dst="type://T",
            kind="DECL_HAS_TYPE",
            attrs={"role": "return"},
        )
        param_edge = GraphEdge(
            src="decl://f",
            dst="type://T",
            kind="DECL_HAS_TYPE",
            attrs={"role": "param"},
        )
        assert return_edge.key() == param_edge.key()
        assert return_edge.relation_key() != param_edge.relation_key()

    def test_relation_key_defaults_to_empty_role_when_absent(self) -> None:
        edge = GraphEdge(src="decl://a", dst="decl://b", kind="DECL_CALLS_DECL")
        assert edge.relation_key() == ("decl://a", "decl://b", "DECL_CALLS_DECL", "")

    def test_different_roles_never_merge_even_at_higher_confidence(self) -> None:
        # Role is part of the dedup key (add_edge keys on relation_key(), not
        # key() -- ADR-046 D1, Codex review on PR #620): two facts naming
        # different roles are two distinct relations, full stop -- there is
        # no "higher-confidence role wins" merge to test, because they never
        # collapse onto one edge object regardless of confidence.
        g = SourceGraphSummary()
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                provenance="weak",
                confidence=CONF_REDUCED,
                attrs={"role": "param"},
            )
        )
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                provenance="strong",
                confidence=CONF_HIGH,
                attrs={"role": "return"},
            )
        )
        assert len(g.edges) == 2
        assert {e.relation_key()[-1] for e in g.edges} == {"param", "return"}

    def test_relation_key_reads_role_from_resolved_after_same_role_merge(
        self,
    ) -> None:
        # Two facts that agree on role (so they DO merge into one edge) but
        # differ on another attr -- relation_key() must reflect the merged
        # resolved view, not whichever fact registered first.
        g = SourceGraphSummary()
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                provenance="weak",
                confidence=CONF_REDUCED,
                attrs={"role": "return", "resolution": "unresolved"},
            )
        )
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                provenance="strong",
                confidence=CONF_HIGH,
                attrs={"role": "return", "resolution": "exact"},
            )
        )
        (edge,) = g.edges
        assert edge.relation_key() == ("decl://a", "type://T", "DECL_HAS_TYPE", "return")
        assert edge.resolved["resolution"] == "exact"

    def test_edge_relation_key_function_matches_method(self) -> None:
        g = SourceGraphSummary()
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="type://T",
                kind="TYPE_HAS_FIELD_TYPE",
                attrs={"role": "field"},
            )
        )
        (edge,) = g.edges
        assert edge.resolved  # registered: resolved is populated, not empty
        assert edge.relation_key() == edge_relation_key(
            edge.src, edge.dst, edge.kind, edge.resolved
        )


class TestGraphIdRoleAware:
    """compute_graph_id() must hash on relation_key(), not the coarse key()
    (Codex review, follow-up to the add_edge dedup-granularity fix): once
    add_edge started allowing role-distinct edges to coexist, a role-only
    difference between two graphs is real content the graph_id must not
    collide on."""

    def _single_edge_graph(self, role: str) -> SourceGraphSummary:
        g = SourceGraphSummary(
            nodes=[
                GraphNode(id="decl://a", kind="source_decl"),
                GraphNode(id="type://T", kind="type"),
            ],
        )
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="type://T",
                kind="DECL_HAS_TYPE",
                attrs={"role": role},
            )
        )
        return g

    def test_role_only_difference_changes_graph_id(self) -> None:
        return_graph = self._single_edge_graph("return")
        param_graph = self._single_edge_graph("param")
        assert return_graph.compute_graph_id() != param_graph.compute_graph_id()

    def test_same_role_is_stable(self) -> None:
        a = self._single_edge_graph("return")
        b = self._single_edge_graph("return")
        assert a.compute_graph_id() == b.compute_graph_id()
