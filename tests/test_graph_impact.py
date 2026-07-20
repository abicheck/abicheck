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
    attach_impact_metadata,
    is_direct_path,
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
