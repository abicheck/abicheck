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

"""``SurfaceGraphLike`` (ADR-063 Phase 3 D5) -- the structural protocol
``AbiSnapshot.surface_graph`` is typed against, with no ``buildsource``
import in ``model``."""

from __future__ import annotations

from abicheck.model.graph_facts import GraphEdge, GraphNode, SurfaceGraphLike
from abicheck.model.source_graph import SourceGraphSummary


class TestSourceGraphSummarySatisfiesProtocol:
    """``SourceGraphSummary`` is the one production implementation -- it
    must satisfy this protocol structurally, unchanged, with no base-class
    edit needed on that class itself."""

    def test_isinstance_against_empty_graph(self) -> None:
        assert isinstance(SourceGraphSummary(), SurfaceGraphLike)

    def test_isinstance_against_populated_graph(self) -> None:
        g = SourceGraphSummary()
        g.add_node(GraphNode(id="decl://x", kind="declaration", label="x"))
        assert isinstance(g, SurfaceGraphLike)

    def test_read_side_after_write(self) -> None:
        g: SurfaceGraphLike = SourceGraphSummary()
        g.add_node(GraphNode(id="decl://x", kind="declaration", label="x"))
        g.add_edge(GraphEdge(src="decl://x", dst="decl://y", kind="references"))
        assert g.has_node("decl://x")
        assert not g.has_node("decl://y")
        assert [n.id for n in g.nodes] == ["decl://x"]
        assert [(e.src, e.dst) for e in g.edges] == [("decl://x", "decl://y")]

    def test_a_caller_typed_against_the_protocol_only_needs_the_declared_surface(
        self,
    ) -> None:
        def build(graph: SurfaceGraphLike) -> None:
            graph.add_node(GraphNode(id="decl://a", kind="declaration", label="a"))

        g = SourceGraphSummary()
        build(g)
        assert g.has_node("decl://a")


class TestNonConformingObjectDoesNotSatisfyProtocol:
    def test_missing_methods(self) -> None:
        class NotAGraph:
            nodes: list[object] = []
            edges: list[object] = []

        assert not isinstance(NotAGraph(), SurfaceGraphLike)
