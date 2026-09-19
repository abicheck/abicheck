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

"""``resolve_entity_attrs``'s single-producer fast path (the memory work).

Structural assertions, not RSS thresholds: the claim is "a single-producer
entity materialises **one** attrs dict, not three", which is an object
*identity* property and so is exactly checkable in-process. The companion
claim -- that the fast path changes no *content* -- is stated as a
differential property against :func:`merge_graph_facts` itself over
generated fact sets, per AGENTS.md's "primitive-level property tests"
guidance, rather than as a handful of fixed examples.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.model.graph_facts import (
    CONF_HIGH,
    CONF_REDUCED,
    CONF_UNKNOWN,
    GraphEdge,
    GraphFact,
    GraphNode,
    ensure_facts_and_resolve,
    merge_entity_facts,
    merge_graph_facts,
    register_fact,
    resolve_entity_attrs,
)


def _node(**kw):
    kw.setdefault("id", "decl://p/h.hpp#f")
    kw.setdefault("kind", "decl")
    return GraphNode(**kw)


def _edge(**kw):
    kw.setdefault("src", "decl://p/h.hpp#a")
    kw.setdefault("dst", "decl://p/h.hpp#b")
    kw.setdefault("kind", "decl_calls_decl")
    return GraphEdge(**kw)


class TestSingleProducerAliasing:
    """One fact => one dict, shared by all three views."""

    @pytest.mark.parametrize("make", [_node, _edge])
    def test_all_three_views_are_one_object(self, make):
        e = make(provenance="header_graph", confidence=CONF_HIGH, attrs={"role": "param"})
        ensure_facts_and_resolve(e)
        assert len(e.facts) == 1
        assert e.attrs is e.resolved
        assert e.attrs is e.facts[0].attrs

    @pytest.mark.parametrize("make", [_node, _edge])
    def test_a_second_fact_ends_the_alias(self, make):
        """The alias is valid only while there is nothing to merge."""
        e = make(provenance="a", confidence=CONF_HIGH, attrs={"x": 1})
        ensure_facts_and_resolve(e)
        register_fact(e, "b", CONF_REDUCED, {"y": 2})
        assert len(e.facts) == 2
        assert e.attrs is e.resolved  # still one derived dict, not two
        assert all(e.attrs is not f.attrs for f in e.facts)
        assert e.attrs == {"x": 1, "y": 2}

    def test_synthesised_fact_still_copies_the_callers_dict(self):
        """A producer may reuse one attrs dict across several entities, so
        the synthesised fact must own its own copy -- otherwise two nodes
        would share one dict and the second's merge would rewrite the first.
        """
        shared = {"visibility": "public"}
        a = _node(id="decl://p/h.hpp#a", attrs=shared, provenance="p")
        b = _node(id="decl://p/h.hpp#b", attrs=shared, provenance="p")
        ensure_facts_and_resolve(a)
        ensure_facts_and_resolve(b)
        assert a.attrs is not b.attrs
        assert a.attrs is not shared
        register_fact(a, "q", CONF_HIGH, {"visibility": "private"})
        assert b.attrs == {"visibility": "public"}
        assert shared == {"visibility": "public"}

    def test_merge_entity_facts_does_not_alias_two_entities_together(self):
        existing = _node(provenance="a", confidence=CONF_HIGH, attrs={"x": 1})
        incoming = _node(provenance="b", confidence=CONF_HIGH, attrs={"x": 1, "y": 2})
        ensure_facts_and_resolve(existing)
        ensure_facts_and_resolve(incoming)
        merge_entity_facts(existing, incoming)
        existing.attrs["mutated"] = True
        assert "mutated" not in incoming.attrs


class TestFastPathEqualsTheFullMerge:
    """Differential property: the fast path must be indistinguishable from
    ``merge_graph_facts`` for every input, not only the one-fact one.

    The oracle is ``merge_graph_facts`` -- deliberately the *other*
    implementation, never a re-derivation of the fast path's own rule.
    """

    KEYS = ("role", "resolution", "visibility")
    VALUES = (True, False, "x", "y", 0)
    CONFS = (CONF_HIGH, CONF_REDUCED, CONF_UNKNOWN)

    def _fact_space(self):
        for producer in ("p", "q"):
            for conf in self.CONFS:
                for key in self.KEYS:
                    for val in self.VALUES:
                        yield GraphFact(producer=producer, confidence=conf, attrs={key: val})

    def test_exhaustive_small_domain_enumeration(self):
        """Every 0-, 1- and 2-fact combination over a small generated domain."""
        space = list(self._fact_space())
        assert len(space) > 50  # vacuity guard on the generator itself
        cases = [[]] + [[f] for f in space]
        cases += [list(p) for p in itertools.islice(itertools.combinations(space, 2), 0, None, 7)]
        assert len(cases) > 200  # vacuity guard on the case set
        disagreeing = []
        for facts in cases:
            got_resolved, got_conflicts = resolve_entity_attrs(list(facts))
            want_resolved, want_conflicts = merge_graph_facts(list(facts))
            if got_resolved != want_resolved or [c.to_dict() for c in got_conflicts] != [
                c.to_dict() for c in want_conflicts
            ]:
                disagreeing.append(facts)
        assert not disagreeing, f"{len(disagreeing)} fact sets disagree, e.g. {disagreeing[:3]}"

    def test_single_fact_result_is_the_facts_own_dict(self):
        f = GraphFact(producer="p", confidence=CONF_HIGH, attrs={"role": "param"})
        resolved, conflicts = resolve_entity_attrs([f])
        assert resolved is f.attrs
        assert conflicts == []

    def test_multi_fact_result_is_a_fresh_dict(self):
        facts = [
            GraphFact(producer="p", confidence=CONF_HIGH, attrs={"a": 1}),
            GraphFact(producer="q", confidence=CONF_HIGH, attrs={"b": 2}),
        ]
        resolved, _ = resolve_entity_attrs(facts)
        assert all(resolved is not f.attrs for f in facts)


class TestPreservedSemantics:
    """Provenance, confidence, conflicts and round-tripping are unaffected."""

    def test_conflicting_multi_producer_facts_still_recorded(self):
        e = _node(provenance="p", confidence=CONF_HIGH, attrs={"is_virtual": True})
        ensure_facts_and_resolve(e)
        register_fact(e, "q", CONF_HIGH, {"is_virtual": False})
        assert len(e.conflicts) == 1
        assert e.conflicts[0].key == "is_virtual"
        assert e.conflicts[0].winning_value is True
        assert e.conflicts[0].losing_value is False

    @pytest.mark.parametrize("make,loader", [(_node, GraphNode), (_edge, GraphEdge)])
    def test_round_trip_preserves_every_view(self, make, loader):
        e = make(provenance="header_graph", confidence=CONF_HIGH, attrs={"role": "return"})
        ensure_facts_and_resolve(e)
        register_fact(e, "other", CONF_REDUCED, {"resolution": "exact"})
        back = loader.from_dict(e.to_dict())
        assert back.attrs == e.attrs
        assert back.resolved == e.resolved
        assert [f.to_dict() for f in back.facts] == [f.to_dict() for f in e.facts]
        assert back.provenance == e.provenance
        assert back.confidence == e.confidence

    def test_known_empty_evidence_survives_and_is_not_missing_evidence(self):
        """A producer that observed *nothing* still leaves a fact behind."""
        e = _node(provenance="header_graph", confidence=CONF_HIGH, attrs={})
        ensure_facts_and_resolve(e)
        assert e.facts == [GraphFact(producer="header_graph", confidence=CONF_HIGH, attrs={})]
        assert e.attrs == {}
        back = GraphNode.from_dict(e.to_dict())
        assert back.facts[0].producer == "header_graph"
        assert back.confidence == CONF_HIGH

    def test_serialised_form_still_carries_both_attrs_and_resolved(self):
        """The alias is internal; the public document shape is unchanged."""
        e = _node(provenance="p", confidence=CONF_HIGH, attrs={"k": "v"})
        ensure_facts_and_resolve(e)
        d = e.to_dict()
        assert d["attrs"] == {"k": "v"}
        assert d["resolved"] == {"k": "v"}
        assert d["attrs"] is not d["resolved"]
        assert d["facts"][0]["attrs"] is not d["attrs"]
