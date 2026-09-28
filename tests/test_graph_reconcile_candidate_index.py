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

"""The reconciler's candidate-restricted structural indices.

``reconcile_added_removed`` builds its structural-context and declaring-file
indices for the added/removed candidates only, not for every node of both
graphs (on a real header-depth graph: ~25 candidates of ~18k nodes). These
properties state why that is exact: a restricted entry equals the
whole-graph entry for the same id, and the whole reconciliation equals one
built over whole-graph indices -- the oracle being the helpers' own
unrestricted mode, which is the pre-restriction behaviour.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.buildsource import graph_reconcile as gr
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary

_KINDS = ["source_decl", "record_type", "enum_type", "typedef", "header", "macro"]
_EDGE_KINDS = ["SOURCE_DECLARES", "USES_TYPE", "HAS_FIELD"]
_NAMES = ["a", "b", "ns::a", "ns::b", "c"]


@st.composite
def _graph_parts(draw: st.DrawFn) -> tuple[list[GraphNode], list[GraphEdge]]:
    count = draw(st.integers(min_value=1, max_value=12))
    nodes = []
    for i in range(count):
        kind = draw(st.sampled_from(_KINDS))
        attrs: dict[str, str] = {}
        if draw(st.booleans()):
            attrs["qualified_name"] = draw(st.sampled_from(_NAMES))
        if draw(st.booleans()):
            attrs["def_file"] = draw(st.sampled_from(["/r/x.h", "/r/y.h"]))
        label = draw(st.sampled_from([*_NAMES, "/r/x.h", ""]))
        nodes.append(GraphNode(id=f"n{i}", kind=kind, label=label, attrs=attrs))
    # Endpoints may name a missing node ("gone"): its identity is "".
    ids = [n.id for n in nodes] + ["gone"]
    edges = []
    for _ in range(draw(st.integers(min_value=0, max_value=25))):
        attrs = {"role": draw(st.sampled_from(["field", "base"]))} if draw(st.booleans()) else {}
        edges.append(
            GraphEdge(
                src=draw(st.sampled_from(ids)),
                dst=draw(st.sampled_from(ids)),
                kind=draw(st.sampled_from(_EDGE_KINDS)),
                attrs=attrs,
            )
        )
    return nodes, edges


def _graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    return g.finalize()


@settings(max_examples=200, deadline=None)
@given(parts=_graph_parts(), data=st.data())
def test_restricted_indices_equal_whole_graph_entries(
    parts: tuple[list[GraphNode], list[GraphEdge]], data: st.DataObject
) -> None:
    graph = _graph(*parts)
    all_ids = [n.id for n in graph.nodes]
    # Includes an id absent from the graph, which must simply not appear.
    wanted = set(data.draw(st.lists(st.sampled_from([*all_ids, "absent"]))))

    full_ctx = gr._all_structural_contexts(graph)
    assert gr._all_structural_contexts(graph, wanted) == {
        k: v for k, v in full_ctx.items() if k in wanted
    }
    full_files = gr._declaring_files(graph)
    assert gr._declaring_files(graph, wanted) == {
        k: v for k, v in full_files.items() if k in wanted
    }


@settings(max_examples=200, deadline=None)
@given(old=_graph_parts(), new=_graph_parts(), data=st.data())
def test_reconciliation_equals_whole_graph_index_reference(
    old: tuple[list[GraphNode], list[GraphEdge]],
    new: tuple[list[GraphNode], list[GraphEdge]],
    data: st.DataObject,
) -> None:
    old_g, new_g = _graph(*old), _graph(*new)
    removed = data.draw(st.lists(st.sampled_from(old_g.nodes), unique_by=lambda n: n.id))
    added = data.draw(st.lists(st.sampled_from(new_g.nodes), unique_by=lambda n: n.id))

    restricted = gr.reconcile_added_removed(removed, added, old_g, new_g).to_dict()

    real_init = gr._Reconciler.__init__

    def whole_graph_init(self, old_graph, new_graph, old_ids=None, new_ids=None):  # type: ignore[no-untyped-def]
        real_init(self, old_graph, new_graph)

    with pytest.MonkeyPatch.context() as m:
        m.setattr(gr._Reconciler, "__init__", whole_graph_init)
        reference = gr.reconcile_added_removed(removed, added, old_g, new_g).to_dict()
    assert restricted == reference


def test_restriction_engages_and_resolves_only_adjacent_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restricted mode must actually be the one running: neighbor
    identities are resolved only for nodes adjacent to a requested id, and
    a changed request changes the answer's key set (the restriction is an
    input to the result, not ignored)."""
    nodes = [GraphNode(id=f"n{i}", kind="source_decl", label=f"f{i}") for i in range(50)]
    edges = [GraphEdge(src=f"n{i}", dst=f"n{i + 1}", kind="USES_TYPE") for i in range(49)]
    graph = _graph(nodes, edges)

    resolved: list[str] = []
    real = gr._neighbor_identity

    def spy(node: GraphNode) -> str:
        resolved.append(node.id)
        return real(node)

    monkeypatch.setattr(gr, "_neighbor_identity", spy)
    ctx = gr._all_structural_contexts(graph, {"n10"})
    assert set(ctx) == {"n10"}
    assert sorted(resolved) == ["n11", "n9"]
    assert ctx["n10"] == frozenset({("in", "USES_TYPE", "source_decl:f9"), ("out", "USES_TYPE", "source_decl:f11")})

    resolved.clear()
    assert set(gr._all_structural_contexts(graph, {"n20", "n30"})) == {"n20", "n30"}
    assert sorted(resolved) == ["n19", "n21", "n29", "n31"]

    resolved.clear()
    assert len(gr._all_structural_contexts(graph)) == 50
    assert len(resolved) == 50
