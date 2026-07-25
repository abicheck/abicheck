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

"""Tests for structured graph impact/proof-path data (G31 Phase B B3, ADR-048)."""

from __future__ import annotations

from abicheck.buildsource.graph_impact import (
    _node_is_public,
    _path_occurrence_id,
    attach_impact_metadata,
    is_direct_path,
    select_preferred_graph_path,
    structured_proof_path,
)
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.checker_policy import ChangeKind
from abicheck.checker_types import Change


def _graph() -> SourceGraphSummary:
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://pub", kind="source_decl", label="pub"))
    g.add_node(GraphNode(id="decl://helper", kind="source_decl", label="helper"))
    g.add_node(
        GraphNode(id="type://Internal", kind="record_type", label="ns::Internal")
    )
    g.add_edge(
        GraphEdge(
            src="decl://pub",
            dst="decl://helper",
            kind="DECL_CALLS_DECL",
            confidence="high",
        )
    )
    g.add_edge(
        GraphEdge(
            src="decl://helper",
            dst="type://Internal",
            kind="DECL_HAS_TYPE",
            confidence="high",
            attrs={"role": "parameter"},
        )
    )
    return g.finalize()


def test_structured_proof_path_empty_for_empty_path() -> None:
    assert structured_proof_path(_graph(), []) == []


def test_structured_proof_path_alternates_node_edge_node() -> None:
    g = _graph()
    path = [e for e in g.edges]
    out = structured_proof_path(g, path)
    types = [entry["type"] for entry in out]
    assert types == ["node", "edge", "node", "edge", "node"]
    assert out[0]["id"] == "decl://pub"
    assert out[-1]["id"] == "type://Internal"
    assert out[1]["kind"] == "DECL_CALLS_DECL"
    assert out[3]["role"] == "parameter"


def test_is_direct_path() -> None:
    g = _graph()
    assert is_direct_path([]) is True
    assert is_direct_path([g.edges[0]]) is True
    assert is_direct_path(list(g.edges)) is False


def test_attach_impact_metadata_sets_fields_in_place() -> None:
    g = _graph()
    change = Change(
        kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
        symbol="pub",
        description="x",
    )
    attach_impact_metadata(
        change, affected_public_roots=["pub"], path=list(g.edges), graph=g
    )
    assert change.affected_public_roots == ["pub"]
    assert change.impact_proof_path is not None
    assert change.impact_proof_path[0]["id"] == "decl://pub"
    assert change.impact_is_direct is False


def test_attach_impact_metadata_direct_when_single_hop() -> None:
    g = _graph()
    change = Change(
        kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
        symbol="pub",
        description="x",
    )
    attach_impact_metadata(
        change, affected_public_roots=["pub"], path=[g.edges[0]], graph=g
    )
    assert change.impact_is_direct is True


def test_internal_dependency_finding_carries_structured_impact() -> None:
    """Integration: the real PUBLIC_API_INTERNAL_DEPENDENCY_ADDED producer
    attaches structured impact data, not just the prose proof-path string.
    """
    from abicheck.buildsource.source_graph_findings import diff_source_graph_findings

    old = SourceGraphSummary()
    old.add_node(GraphNode(id="target://t", kind="target", label="t"))
    old.add_node(
        GraphNode(
            id="decl://entry",
            kind="source_decl",
            label="entry",
            attrs={"visibility": "public_header"},
        )
    )
    old.add_node(GraphNode(id="header://pub.h", kind="header", label="pub.h"))
    old.add_edge(
        GraphEdge(src="header://pub.h", dst="decl://entry", kind="SOURCE_DECLARES")
    )
    old.finalize()

    new = SourceGraphSummary()
    new.add_node(GraphNode(id="target://t", kind="target", label="t"))
    new.add_node(
        GraphNode(
            id="decl://entry",
            kind="source_decl",
            label="entry",
            attrs={"visibility": "public_header"},
        )
    )
    new.add_node(GraphNode(id="header://pub.h", kind="header", label="pub.h"))
    new.add_node(
        GraphNode(
            id="decl://internal",
            kind="source_decl",
            label="internal",
            attrs={"visibility": "private_header"},
        )
    )
    new.add_edge(
        GraphEdge(src="header://pub.h", dst="decl://entry", kind="SOURCE_DECLARES")
    )
    new.add_edge(
        GraphEdge(src="decl://entry", dst="decl://internal", kind="DECL_CALLS_DECL")
    )
    new.extractor_passes["call_graph"] = True
    new.extractor_passes["type_graph"] = True
    old.extractor_passes["call_graph"] = True
    old.extractor_passes["type_graph"] = True
    new.finalize()

    findings = diff_source_graph_findings(old, new)
    internal_dep = [
        c for c in findings if c.kind == ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED
    ]
    assert len(internal_dep) == 1
    change = internal_dep[0]
    assert change.affected_public_roots == ["entry"]
    assert change.impact_proof_path is not None
    assert change.impact_is_direct is True


class TestNodeIsPublic:
    """CodeRabbit review: _node_is_public must read `resolved` before
    falling back to `attrs`, matching its sibling `_edge_is_overapprox`."""

    def test_none_node_is_not_public(self) -> None:
        assert _node_is_public(None) is False

    def test_falls_back_to_attrs_for_an_unprocessed_bare_node(self) -> None:
        # A GraphNode constructed directly, never passed through add_node/
        # SourceGraphSummary.__post_init__ (ensure_facts_and_resolve), has an
        # empty `resolved` by default even though `attrs` carries real data
        # -- the actual scenario the `resolved or attrs` fallback protects,
        # since a fully-processed node always has attrs synced to resolved.
        node = GraphNode(
            id="decl://pub", kind="source_decl", label="pub",
            attrs={"visibility": "public_header"},
        )
        assert node.resolved == {}
        assert _node_is_public(node) is True

    def test_prefers_resolved_over_attrs_when_both_present(self) -> None:
        node = GraphNode(
            id="decl://pub", kind="source_decl", label="pub",
            attrs={"visibility": "source"},
            resolved={"visibility": "public_header"},
        )
        assert _node_is_public(node) is True


class TestSelectPreferredGraphPath:
    """ADR-046 D6: tiered preference among structured GraphEdge paths."""

    def _node(self, node_id: str, label: str, visibility: str) -> GraphNode:
        return GraphNode(
            id=node_id, kind="source_decl", label=label, attrs={"visibility": visibility}
        )

    def test_empty_paths_returns_empty(self) -> None:
        assert select_preferred_graph_path(_graph(), []) == []

    def test_single_path_returned_unchanged_no_tier_computation(self) -> None:
        g = _graph()
        only = [g.edges[0]]
        assert select_preferred_graph_path(g, [only]) is only

    def test_exact_high_confidence_beats_overapprox(self) -> None:
        g = SourceGraphSummary(
            nodes=[
                self._node("decl://pub", "pub", "source"),
                self._node("decl://a", "a", "source"),
                self._node("decl://b", "b", "source"),
            ],
        )
        exact = GraphEdge(
            src="decl://pub", dst="decl://a", kind="DECL_CALLS_DECL", confidence="high"
        )
        overapprox = GraphEdge(
            src="decl://pub",
            dst="decl://b",
            kind="DECL_CALLS_DECL",
            confidence="high",
            attrs={"call_kind": "virtual", "resolution": "overapprox"},
        )
        SourceGraphSummary(edges=[exact, overapprox])  # resolve facts
        chosen = select_preferred_graph_path(g, [[overapprox], [exact]])
        assert chosen == [exact]

    def test_public_structural_beats_reduced_confidence_no_public_signal(self) -> None:
        g = SourceGraphSummary(
            nodes=[
                self._node("decl://pub", "pub", "public_header"),
                self._node("decl://pub_target", "t1", "public_header"),
                self._node("decl://priv_start", "priv", "source"),
                self._node("decl://priv_target", "t2", "source"),
            ],
        )
        public_structural = GraphEdge(
            src="decl://pub",
            dst="decl://pub_target",
            kind="DECL_CALLS_DECL",
            confidence="reduced",
        )
        no_signal = GraphEdge(
            src="decl://priv_start",
            dst="decl://priv_target",
            kind="DECL_CALLS_DECL",
            confidence="reduced",
        )
        SourceGraphSummary(edges=[public_structural, no_signal])
        chosen = select_preferred_graph_path(g, [[no_signal], [public_structural]])
        assert chosen == [public_structural]

    def test_multi_producer_confirmed_beats_single_producer_reduced(self) -> None:
        g = SourceGraphSummary(
            nodes=[
                self._node("decl://pub", "pub", "source"),
                self._node("decl://a", "a", "source"),
                self._node("decl://b", "b", "source"),
            ],
        )
        confirmed = GraphEdge(
            src="decl://pub",
            dst="decl://a",
            kind="DECL_CALLS_DECL",
            provenance="producer_1",
            confidence="reduced",
        )
        summary = SourceGraphSummary(edges=[confirmed])
        summary.add_edge(
            GraphEdge(
                src="decl://pub",
                dst="decl://a",
                kind="DECL_CALLS_DECL",
                provenance="producer_2",
                confidence="reduced",
            )
        )
        (confirmed,) = summary.edges  # merged into one multi-fact edge
        single = GraphEdge(
            src="decl://pub",
            dst="decl://b",
            kind="DECL_CALLS_DECL",
            provenance="producer_1",
            confidence="reduced",
        )
        SourceGraphSummary(edges=[single])
        chosen = select_preferred_graph_path(g, [[single], [confirmed]])
        assert chosen == [confirmed]

    def test_ties_within_a_tier_prefer_shorter_path(self) -> None:
        g = SourceGraphSummary(
            nodes=[
                self._node("decl://pub", "pub", "source"),
                self._node("decl://mid", "mid", "source"),
                self._node("decl://leaf", "leaf", "source"),
            ],
        )
        short = GraphEdge(
            src="decl://pub", dst="decl://mid", kind="DECL_CALLS_DECL", confidence="high"
        )
        long_first = GraphEdge(
            src="decl://pub", dst="decl://mid", kind="DECL_CALLS_DECL", confidence="high"
        )
        long_second = GraphEdge(
            src="decl://mid", dst="decl://leaf", kind="DECL_CALLS_DECL", confidence="high"
        )
        SourceGraphSummary(edges=[short, long_first, long_second])
        chosen = select_preferred_graph_path(g, [[long_first, long_second], [short]])
        assert chosen == [short]


class TestAttachImpactMetadataAlternatives:
    """ADR-046 D6: alternative_paths/discarded_path_count on attach_impact_metadata."""

    def test_no_alternatives_leaves_fields_unset(self) -> None:
        g = _graph()
        change = Change(
            kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            symbol="pub",
            description="x",
        )
        attach_impact_metadata(
            change, affected_public_roots=["pub"], path=[g.edges[0]], graph=g
        )
        assert change.impact_alternative_paths is None
        assert change.impact_discarded_path_count == 0

    def test_alternatives_recorded_and_excludes_primary(self) -> None:
        g = _graph()
        primary = [g.edges[0]]
        alt = [g.edges[1]]
        change = Change(
            kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            symbol="pub",
            description="x",
        )
        attach_impact_metadata(
            change,
            affected_public_roots=["pub"],
            path=primary,
            graph=g,
            alternative_paths=[primary, alt],
        )
        assert change.impact_alternative_paths is not None
        assert len(change.impact_alternative_paths) == 1
        assert change.impact_discarded_path_count == 0

    def test_alternatives_beyond_cap_are_counted_as_discarded(self) -> None:
        g = SourceGraphSummary(
            nodes=[
                GraphNode(id="decl://pub", kind="source_decl", label="pub"),
                *(
                    GraphNode(id=f"decl://t{i}", kind="source_decl", label=f"t{i}")
                    for i in range(5)
                ),
            ],
        )
        primary = [
            GraphEdge(src="decl://pub", dst="decl://t0", kind="DECL_CALLS_DECL")
        ]
        alts = [
            [GraphEdge(src="decl://pub", dst=f"decl://t{i}", kind="DECL_CALLS_DECL")]
            for i in range(1, 5)
        ]
        change = Change(
            kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            symbol="pub",
            description="x",
        )
        attach_impact_metadata(
            change,
            affected_public_roots=["pub"],
            path=primary,
            graph=g,
            alternative_paths=[primary, *alts],
        )
        assert change.impact_alternative_paths is not None
        assert len(change.impact_alternative_paths) == 3
        assert change.impact_discarded_path_count == 1


class TestPathOccurrenceId:
    """ADR-052's stable ``occurrence_id`` follow-up, built on ADR-046 D1."""

    def test_no_occurrence_attrs_returns_none(self) -> None:
        g = _graph()
        assert _path_occurrence_id(list(g.edges)) is None

    def test_edge_with_occurrence_attrs_yields_a_stable_id(self) -> None:
        edge = GraphEdge(
            src="decl://pub",
            dst="decl://helper",
            kind="DECL_CALLS_DECL",
            attrs={"callsite_id": "cs1"},
        )
        SourceGraphSummary(edges=[edge])  # resolve facts -> populates occurrences
        assert edge.occurrences
        first = _path_occurrence_id([edge])
        second = _path_occurrence_id([edge])
        assert first is not None
        assert first == second

    def test_different_occurrences_yield_different_ids(self) -> None:
        edge_a = GraphEdge(
            src="decl://pub",
            dst="decl://helper",
            kind="DECL_CALLS_DECL",
            attrs={"callsite_id": "cs1"},
        )
        edge_b = GraphEdge(
            src="decl://pub",
            dst="decl://helper",
            kind="DECL_CALLS_DECL",
            attrs={"callsite_id": "cs2"},
        )
        SourceGraphSummary(edges=[edge_a, edge_b])
        assert _path_occurrence_id([edge_a]) != _path_occurrence_id([edge_b])

    def test_attach_impact_metadata_sets_none_by_default(self) -> None:
        g = _graph()
        change = Change(
            kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            symbol="pub",
            description="x",
        )
        attach_impact_metadata(
            change, affected_public_roots=["pub"], path=list(g.edges), graph=g
        )
        assert change.impact_occurrence_id is None

    def test_attach_impact_metadata_sets_id_when_edge_has_occurrence_attrs(self) -> None:
        edge = GraphEdge(
            src="decl://pub",
            dst="decl://helper",
            kind="DECL_CALLS_DECL",
            attrs={"callsite_id": "cs1"},
        )
        g = SourceGraphSummary(edges=[edge])
        change = Change(
            kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            symbol="pub",
            description="x",
        )
        attach_impact_metadata(
            change, affected_public_roots=["pub"], path=[edge], graph=g
        )
        assert change.impact_occurrence_id is not None

    def test_engine_carries_occurrence_id_into_graph_proof_path(self) -> None:
        from abicheck.impact.engine import assess_change

        edge = GraphEdge(
            src="decl://pub",
            dst="decl://helper",
            kind="DECL_CALLS_DECL",
            attrs={"callsite_id": "cs1"},
        )
        g = SourceGraphSummary(edges=[edge])
        change = Change(
            kind=ChangeKind.PUBLIC_API_INTERNAL_DEPENDENCY_ADDED,
            symbol="pub",
            description="x",
        )
        attach_impact_metadata(
            change, affected_public_roots=["pub"], path=[edge], graph=g
        )
        assessment = assess_change(change)
        assert assessment.proof_path is not None
        assert assessment.proof_path.occurrence_id == change.impact_occurrence_id
        assert assessment.proof_path.occurrence_id is not None
