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

"""Direct tests for the dependency-closure and proof-path helpers.

``abicheck.buildsource.dependency_paths`` was moved out of
``source_graph_findings.py``; these tests pin its own contract (reachability
from public entries, and one shortest proof path), independent of the diff
pipeline that consumes it.
"""

from __future__ import annotations

from abicheck.buildsource.dependency_paths import (
    _dependency_path,
    _dependency_reachability,
)
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary

_FIELD = frozenset({"TYPE_HAS_FIELD_TYPE"})


def _node(name: str, visibility: str) -> GraphNode:
    return GraphNode(
        id=f"type:{name}",
        kind="record_type",
        label=name,
        attrs={"qualified_name": name, "visibility": visibility},
    )


def _field_edge(src: str, dst: str) -> GraphEdge:
    return GraphEdge(
        src=f"type:{src}",
        dst=f"type:{dst}",
        kind="TYPE_HAS_FIELD_TYPE",
        attrs={"role": "field"},
    )


def _chain() -> SourceGraphSummary:
    """A (public) -> B (private) -> C (private), plus an unrelated private D."""
    g = SourceGraphSummary()
    for node in (
        _node("A", "public_header"),
        _node("B", "private_header"),
        _node("C", "private_header"),
        _node("D", "private_header"),
    ):
        g.add_node(node)
    g.add_edge(_field_edge("A", "B"))
    g.add_edge(_field_edge("B", "C"))
    return g.finalize()


def test_reachability_closes_over_dependency_edges_from_a_public_entry() -> None:
    reach = _dependency_reachability(_chain(), _FIELD)
    assert reach["type:A"] == frozenset({"type:B", "type:C"})
    assert "type:D" not in reach.get("type:A", frozenset())


def test_reachability_is_empty_without_edge_kinds() -> None:
    assert _dependency_reachability(_chain(), frozenset()) == {}


def test_proof_path_is_the_shortest_chain_of_edges() -> None:
    path = _dependency_path(_chain(), _FIELD, "type:A", "type:C")
    assert path is not None
    assert [(e.src, e.dst) for e in path] == [
        ("type:A", "type:B"),
        ("type:B", "type:C"),
    ]


def test_proof_path_is_none_when_the_target_is_unreachable() -> None:
    assert _dependency_path(_chain(), _FIELD, "type:A", "type:D") is None
    assert _dependency_path(_chain(), _FIELD, "type:C", "type:A") is None
