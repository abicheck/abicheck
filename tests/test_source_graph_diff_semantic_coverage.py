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

"""A semantic fact family one side never looked for is a coverage gap in the
structural graph diff, never a removal or an addition.

Header graphs stored before the header pass projected override, template and
callback facts carry none of them and no pass flag for them. Compared with a
fresh graph, every such fact would otherwise read as added (or, the other way
round, removed). The oracle here is each family's own pass flags and edge
kinds, stated per case -- not the diff's implementation.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.source_graph_compare import diff_source_graph
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.model.source_graph_coverage import SEMANTIC_FAMILIES

FAMILIES = {f.name: f for f in SEMANTIC_FAMILIES}


def _graph(*, flags: tuple[str, ...] = (), family=None, with_fact: bool = False):
    g = SourceGraphSummary()
    g.add_node(GraphNode(id="decl://a", kind="source_decl", provenance="header_ast_l2"))
    g.add_node(GraphNode(id="decl://b", kind="source_decl", provenance="header_ast_l2"))
    for name in flags:
        g.extractor_passes[name] = True
    if with_fact:
        kind = sorted(family.edge_kinds)[0]
        # A node only this family minted, plus one of its edges.
        g.add_node(
            GraphNode(
                id="decl://minted", kind="source_decl", provenance=family.provenance
            )
        )
        g.add_edge(
            GraphEdge(
                src="decl://a",
                dst="decl://minted",
                kind=kind,
                provenance=family.provenance,
            )
        )
        for node_kind in sorted(family.node_kinds):
            g.add_node(
                GraphNode(
                    id=f"{node_kind}://x", kind=node_kind, provenance=family.provenance
                )
            )
    return g


def _edges(diff, attr):
    return {(e.src, e.dst, e.kind) for e in getattr(diff, attr)}


def _nodes(diff, attr):
    return {n.id for n in getattr(diff, attr)}


@pytest.mark.parametrize("name", sorted(FAMILIES))
@pytest.mark.parametrize("direction", ["stored_old", "stored_new"])
def test_a_side_that_never_ran_the_family_is_a_gap_not_a_change(
    name, direction
) -> None:
    family = FAMILIES[name]
    stored = _graph(flags=("header_type_graph",), family=family)
    fresh = _graph(
        flags=("header_type_graph", family.header_pass), family=family, with_fact=True
    )
    old, new = (stored, fresh) if direction == "stored_old" else (fresh, stored)
    diff = diff_source_graph(old, new)
    assert not any(
        e.kind in family.edge_kinds for e in diff.added_edges + diff.removed_edges
    )
    assert "decl://minted" not in _nodes(diff, "added_nodes") | _nodes(
        diff, "removed_nodes"
    )
    assert not any(
        n.kind in family.node_kinds for n in diff.added_nodes + diff.removed_nodes
    )
    assert name in diff.coverage_gaps
    assert diff.to_dict()["coverage_gaps"] == diff.coverage_gaps


@pytest.mark.parametrize("name", sorted(FAMILIES))
@pytest.mark.parametrize(
    ("old_pass", "new_pass"),
    [("header", "header"), ("build", "header"), ("header", "build")],
)
def test_both_sides_looked_so_a_change_surfaces(name, old_pass, new_pass) -> None:
    family = FAMILIES[name]
    flag = {"header": family.header_pass, "build": family.build_pass}
    old = _graph(flags=(flag[old_pass],), family=family)
    new = _graph(flags=(flag[new_pass],), family=family, with_fact=True)
    diff = diff_source_graph(old, new)
    kind = sorted(family.edge_kinds)[0]
    assert ("decl://a", "decl://minted", kind) in _edges(diff, "added_edges")
    assert "decl://minted" in _nodes(diff, "added_nodes")
    assert name not in diff.coverage_gaps
    reverse = diff_source_graph(new, old)
    assert ("decl://a", "decl://minted", kind) in _edges(reverse, "removed_edges")


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_degraded_or_narrowed_runs_still_count_as_looked_for(name) -> None:
    family = FAMILIES[name]
    old = _graph(family=family)
    old.degraded_passes[family.header_pass] = True
    new = _graph(family=family, with_fact=True)
    new.narrowed_passes[family.build_pass] = True
    diff = diff_source_graph(old, new)
    assert any(e.kind in family.edge_kinds for e in diff.added_edges)
    assert name not in diff.coverage_gaps


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_two_unflagged_graphs_keep_the_plain_structural_diff(name) -> None:
    """Hand-built graphs with no pass flags at all on either side have never
    carried coverage; the structural diff stays exactly what it was."""
    family = FAMILIES[name]
    diff = diff_source_graph(
        _graph(family=family), _graph(family=family, with_fact=True)
    )
    assert any(e.kind in family.edge_kinds for e in diff.added_edges)
    assert diff.coverage_gaps == []


@pytest.mark.parametrize("name", sorted(FAMILIES))
def test_an_unflagged_legacy_side_counts_only_if_it_carries_the_family(name) -> None:
    family = FAMILIES[name]
    flagged = _graph(flags=(family.header_pass,), family=family, with_fact=True)
    legacy_without = _graph(family=family)
    assert name in diff_source_graph(legacy_without, flagged).coverage_gaps
    legacy_with = _graph(family=family, with_fact=True)
    assert name not in diff_source_graph(legacy_with, flagged).coverage_gaps


def test_unrelated_changes_still_surface_beside_a_gap() -> None:
    family = FAMILIES["template"]
    stored = _graph(flags=("header_type_graph",), family=family)
    fresh = _graph(
        flags=("header_type_graph", family.header_pass), family=family, with_fact=True
    )
    fresh.add_node(
        GraphNode(id="decl://c", kind="source_decl", provenance="header_ast_l2")
    )
    fresh.add_edge(GraphEdge(src="decl://a", dst="decl://c", kind="DECL_CALLS_DECL"))
    diff = diff_source_graph(stored, fresh)
    assert ("decl://a", "decl://c", "DECL_CALLS_DECL") in _edges(diff, "added_edges")
    assert "decl://c" in _nodes(diff, "added_nodes")
