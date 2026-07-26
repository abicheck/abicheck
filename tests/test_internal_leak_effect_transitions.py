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

"""Tests for ADR-046 D5's ``effect_transitions`` (G29 Phase 2 follow-up).

Split out of ``test_internal_leak.py`` to keep that file under the
AI-readiness line-count cap (CLAUDE.md) — this is purely a file-size split,
not a different subsystem: it exercises the same ``CALL_GRAPH_TRAVERSAL_POLICY``/
``_consumer_compiled_reachability``/``compute_call_graph_leak_paths`` machinery
``test_internal_leak.py``'s own ``TestTraversalPolicy`` class covers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from abicheck.model import AbiSnapshot

if TYPE_CHECKING:
    from abicheck.buildsource.source_graph import GraphEdge, GraphNode


def _decl_node(node_id: str, label: str, visibility: str) -> GraphNode:
    from abicheck.buildsource.source_graph import GraphNode

    return GraphNode(
        id=node_id, kind="source_decl", label=label, attrs={"visibility": visibility}
    )


def _graph_snap(nodes: list[GraphNode], edges: list[GraphEdge]) -> AbiSnapshot:
    from abicheck.buildsource.pack import BuildSourcePack
    from abicheck.buildsource.source_graph import SourceGraphSummary

    graph = SourceGraphSummary(nodes=list(nodes), edges=list(edges))
    return AbiSnapshot(
        library="libtest.so",
        version="1.0",
        build_source=BuildSourcePack(root="", source_graph=graph),
    )


class TestEffectTransitions:
    """ADR-046 D5's ``effect_transitions``: a walk crossing a virtual /
    function-pointer call downgrades from "exact" to "overapprox", and every
    node reached transitively past it inherits that downgrade too."""

    def test_edge_effect_transition_none_for_direct_call(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import (
            CALL_GRAPH_TRAVERSAL_POLICY,
            _edge_effect_transition,
        )

        edge = GraphEdge(
            src="decl://a",
            dst="decl://b",
            kind="DECL_CALLS_DECL",
            attrs={"call_kind": "direct", "resolution": "exact"},
        )
        assert _edge_effect_transition(CALL_GRAPH_TRAVERSAL_POLICY, edge) is None

    def test_edge_effect_transition_overapprox_for_virtual_call(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge, SourceGraphSummary
        from abicheck.internal_leak import (
            CALL_GRAPH_TRAVERSAL_POLICY,
            _edge_effect_transition,
        )

        edge = GraphEdge(
            src="decl://a",
            dst="decl://b",
            kind="DECL_CALLS_DECL",
            attrs={"call_kind": "virtual", "resolution": "overapprox"},
        )
        SourceGraphSummary(edges=[edge])  # resolve facts into edge.resolved
        assert _edge_effect_transition(CALL_GRAPH_TRAVERSAL_POLICY, edge) == "overapprox"

    def test_reachability_marks_target_of_virtual_call_as_degraded(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge, SourceGraphSummary
        from abicheck.internal_leak import (
            CALL_GRAPH_TRAVERSAL_POLICY,
            _consumer_compiled_reachability,
        )

        graph = SourceGraphSummary(
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://direct", "ns::detail::direct", "source"),
                _decl_node("decl://virtual", "ns::detail::virtualCallee", "source"),
            ],
            edges=[
                GraphEdge(
                    src="decl://pub",
                    dst="decl://direct",
                    kind="DECL_CALLS_DECL",
                    attrs={"call_kind": "direct", "resolution": "exact"},
                ),
                GraphEdge(
                    src="decl://pub",
                    dst="decl://virtual",
                    kind="DECL_CALLS_DECL",
                    attrs={"call_kind": "virtual", "resolution": "overapprox"},
                ),
            ],
        )
        node_by_id = {n.id: n for n in graph.nodes}
        _, _, degraded = _consumer_compiled_reachability(
            graph, CALL_GRAPH_TRAVERSAL_POLICY, ["decl://pub"], node_by_id
        )["decl://pub"]
        assert degraded == frozenset({"decl://virtual"})

    def test_degraded_is_sticky_past_the_transition_edge(self) -> None:
        # pub -> (virtual) -> mid -> (direct) -> leaf: leaf is only reachable
        # through the virtual call, so it inherits the downgrade too, even
        # though the mid->leaf edge is itself a direct call.
        from abicheck.buildsource.source_graph import GraphEdge, SourceGraphSummary
        from abicheck.internal_leak import (
            CALL_GRAPH_TRAVERSAL_POLICY,
            _consumer_compiled_reachability,
        )

        graph = SourceGraphSummary(
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://mid", "ns::detail::mid", "source"),
                _decl_node("decl://leaf", "ns::detail::leaf", "source"),
            ],
            edges=[
                GraphEdge(
                    src="decl://pub",
                    dst="decl://mid",
                    kind="DECL_CALLS_DECL",
                    attrs={"call_kind": "virtual", "resolution": "overapprox"},
                ),
                GraphEdge(
                    src="decl://mid",
                    dst="decl://leaf",
                    kind="DECL_CALLS_DECL",
                    attrs={"call_kind": "direct", "resolution": "exact"},
                ),
            ],
        )
        node_by_id = {n.id: n for n in graph.nodes}
        _, _, degraded = _consumer_compiled_reachability(
            graph, CALL_GRAPH_TRAVERSAL_POLICY, ["decl://pub"], node_by_id
        )["decl://pub"]
        assert degraded == frozenset({"decl://mid", "decl://leaf"})

    def test_exact_route_overrides_an_earlier_discovered_degraded_route(
        self,
    ) -> None:
        """CodeRabbit review: a plain "first discovery wins" BFS would mark
        `target` permanently degraded because the direct pub->target virtual
        edge is visited before the longer pub->mid->target exact route --
        an ordering artifact, not a real precision limit. An exact route to
        a node must always win over a degraded one, regardless of which was
        discovered first or which is shorter (matches D6's own exact-beats-
        overapprox proof-path preference)."""
        from abicheck.buildsource.source_graph import GraphEdge, SourceGraphSummary
        from abicheck.internal_leak import (
            CALL_GRAPH_TRAVERSAL_POLICY,
            _consumer_compiled_reachability,
        )

        graph = SourceGraphSummary(
            nodes=[
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://mid", "ns::detail::mid", "source"),
                _decl_node("decl://target", "ns::detail::target", "source"),
            ],
            edges=[
                # Registered first, so a naive BFS visits it before the
                # pub->mid edge and marks `target` seen+degraded immediately.
                GraphEdge(
                    src="decl://pub",
                    dst="decl://target",
                    kind="DECL_CALLS_DECL",
                    attrs={"call_kind": "virtual", "resolution": "overapprox"},
                ),
                GraphEdge(
                    src="decl://pub",
                    dst="decl://mid",
                    kind="DECL_CALLS_DECL",
                    attrs={"call_kind": "direct", "resolution": "exact"},
                ),
                GraphEdge(
                    src="decl://mid",
                    dst="decl://target",
                    kind="DECL_CALLS_DECL",
                    attrs={"call_kind": "direct", "resolution": "exact"},
                ),
            ],
        )
        node_by_id = {n.id: n for n in graph.nodes}
        seen, came_from, degraded = _consumer_compiled_reachability(
            graph, CALL_GRAPH_TRAVERSAL_POLICY, ["decl://pub"], node_by_id
        )["decl://pub"]
        assert seen == frozenset({"decl://mid", "decl://target"})
        assert degraded == frozenset()
        assert came_from["decl://target"].src == "decl://mid"

    def test_default_policy_has_no_effect_transitions(self) -> None:
        from abicheck.internal_leak import TraversalPolicy

        policy = TraversalPolicy(
            allowed_edges=frozenset({"DECL_CALLS_DECL"}),
            stop_conditions=lambda node_id, node_by_id: False,
        )
        assert policy.effect_transitions == {}

    def test_compute_call_graph_leak_paths_flags_overapprox_path(self) -> None:
        from abicheck.buildsource.source_graph import GraphEdge
        from abicheck.internal_leak import compute_call_graph_leak_paths

        snap = _graph_snap(
            [
                _decl_node("decl://pub", "pubFn", "public_header"),
                _decl_node("decl://int", "ns::detail::helper", "source"),
            ],
            [
                GraphEdge(
                    src="decl://pub",
                    dst="decl://int",
                    kind="DECL_CALLS_DECL",
                    attrs={"call_kind": "virtual", "resolution": "overapprox"},
                )
            ],
        )
        (path,) = compute_call_graph_leak_paths(snap)["ns::detail::helper"]
        assert path.startswith("overapprox: ")
