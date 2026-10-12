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

"""Graph identity reconciliation does not depend on input or edge order.

Reruns over identical inputs reported different identity-reconciliation RISK
counts (libonedal_core: 3 vs 21). A declaration redeclared in several headers
carries one ``SOURCE_DECLARES`` edge per header, and the reconciler kept only
the *last* one scanned as "the" declaring file -- so the same file set on both
sides classified as ``declaration_identity_unchanged`` (COMPATIBLE) or
``declaration_moved`` (RISK) depending on edge order. The greedy tiers also
paired by the caller's list order.

The oracle is order-free by construction: each case is reconciled under every
permutation (and, cross-process, under several ``PYTHONHASHSEED`` values) and
all results must equal one another; the declaring-file outcome is checked
against a set comparison computed here, not by the reconciler's helpers.
"""

from __future__ import annotations

import itertools
import json
import os
import random
import subprocess
import sys
from typing import Any

import pytest
from hypothesis import given, settings, strategies as st

from abicheck.buildsource.graph_reconcile import (
    diff_graph_reconciliation_findings,
    reconcile_added_removed,
)
from abicheck.buildsource.source_graph import GraphEdge, GraphNode, SourceGraphSummary
from abicheck.model.source_graph_query import decl_declaring_files

_FILES = ["a.h", "b.h", "c.h", "d/e.h"]


def _graph(nodes: list[GraphNode], edges: list[GraphEdge]) -> SourceGraphSummary:
    g = SourceGraphSummary()
    for n in nodes:
        g.add_node(n)
    for e in edges:
        g.add_edge(e)
    return g.finalize()


def _side(
    decls: list[tuple[str, str, list[str]]],
    *,
    edge_order_seed: int,
    def_file: str = "",
) -> tuple[SourceGraphSummary, list[GraphNode]]:
    """A graph declaring each ``(id, qualified name, files)`` in every one of
    its files, with the edges listed in a seed-shuffled order."""
    headers = {
        f: GraphNode(id=f"header://{f}", kind="header", label=f)
        for _, _, files in decls
        for f in files
    }
    nodes = [
        GraphNode(
            id=nid,
            kind="record_type",
            label=qn,
            attrs={
                "qualified_name": qn,
                **({"def_file": def_file} if def_file else {}),
            },
        )
        for nid, qn, _ in decls
    ]
    edges = [
        GraphEdge(src=f"header://{f}", dst=nid, kind="SOURCE_DECLARES")
        for nid, _, files in decls
        for f in files
    ]
    rng = random.Random(edge_order_seed)
    rng.shuffle(edges)
    node_list = [*headers.values(), *nodes]
    rng.shuffle(node_list)
    graph = _graph(node_list, edges)
    return graph, [n for n in graph.nodes if n.kind == "record_type"]


def _summary(old: SourceGraphSummary, new: SourceGraphSummary, removed, added):
    result = reconcile_added_removed(removed, added, old, new)
    return {
        "pairs": sorted(
            (p.old_node.id, p.new_node.id, p.match_kind, p.outcome)
            for p in result.reconciled
        ),
        "ambiguous_old": sorted(n.id for n in result.ambiguous_old),
        "ambiguous_new": sorted(n.id for n in result.ambiguous_new),
        "true_added": sorted(n.id for n in result.true_added),
        "true_removed": sorted(n.id for n in result.true_removed),
        "findings": sorted(
            (c.kind.value, c.symbol) for c in diff_graph_reconciliation_findings(result)
        ),
    }


_file_sets = st.lists(st.sampled_from(_FILES), min_size=1, max_size=4, unique=True)


@settings(max_examples=150, deadline=None)
@given(
    old_files=_file_sets,
    new_files=_file_sets,
    seeds=st.tuples(st.integers(), st.integers()),
)
def test_declaring_file_outcome_ignores_edge_order(
    old_files: list[str], new_files: list[str], seeds: tuple[int, int]
) -> None:
    old_decls = [("type://old_w", "ns::W", old_files)]
    new_decls = [("type://new_w", "ns::W", new_files)]
    baseline = None
    for s_old, s_new in [(0, 0), seeds, (seeds[1], seeds[0])]:
        old, removed = _side(old_decls, edge_order_seed=s_old)
        new, added = _side(new_decls, edge_order_seed=s_new)
        got = _summary(old, new, removed, added)
        if baseline is None:
            baseline = got
        assert got == baseline
    # Independent oracle: a shared declaring file is never a move.
    (pair,) = baseline["pairs"]  # type: ignore[index]
    if set(old_files) & set(new_files):
        assert pair[3] != "declaration_moved"
    else:
        assert pair[3] == "declaration_moved"


def test_same_file_set_listed_in_opposite_order_is_not_a_move() -> None:
    """The reported shape: identical redeclaration sets, reversed edges."""
    old, removed = _side([("type://o", "ns::W", ["a.h", "b.h"])], edge_order_seed=1)
    new_edges_rev = [("type://n", "ns::W", ["b.h", "a.h"])]
    new, added = _side(new_edges_rev, edge_order_seed=2)
    got = _summary(old, new, removed, added)
    assert [p[3] for p in got["pairs"]] == ["declaration_identity_unchanged"]


@pytest.mark.parametrize(
    ("old_def", "new_def"), [("a.h", "b.h"), ("b.h", "a.h"), ("a.h", "a.h")]
)
@pytest.mark.parametrize("files", [["a.h", "b.h"], ["b.h", "a.h"]])
def test_shared_declaring_header_beats_differing_def_file(
    old_def: str, new_def: str, files: list[str]
) -> None:
    """Each side's own def_file differs, but both declare the entity in a
    shared header set: shared declaring evidence means it did not move."""
    old, removed = _side(
        [("type://o", "ns::W", files)], edge_order_seed=1, def_file=old_def
    )
    new, added = _side(
        [("type://n", "ns::W", list(reversed(files)))],
        edge_order_seed=2,
        def_file=new_def,
    )
    got = _summary(old, new, removed, added)
    assert [p[3] for p in got["pairs"]] != ["declaration_moved"]
    assert len(got["pairs"]) == 1


def test_pairing_ignores_caller_list_order() -> None:
    """Several same-kind candidates: every permutation of both input lists
    yields one result."""
    old_decls = [
        ("type://o1", "ns::A", ["a.h"]),
        ("type://o2", "ns::A", ["b.h"]),
        ("type://o3", "ns::B", ["a.h", "c.h"]),
        ("type://o4", "ns::C", ["d/e.h"]),
    ]
    new_decls = [
        ("type://n1", "ns::A", ["b.h"]),
        ("type://n3", "ns::B", ["c.h", "a.h"]),
        ("type://n4", "ns::C", ["a.h"]),
    ]
    old, removed = _side(old_decls, edge_order_seed=3)
    new, added = _side(new_decls, edge_order_seed=4)
    results = {
        json.dumps(_summary(old, new, list(r), list(a)), sort_keys=True)
        for r in itertools.permutations(removed)
        for a in itertools.permutations(added)
    }
    assert len(results) == 1


def test_decl_declaring_files_ignores_edge_order() -> None:
    picks = set()
    for seed in range(8):
        graph, _ = _side(
            [("type://w", "ns::W", ["c.h", "a.h", "b.h"])], edge_order_seed=seed
        )
        picks.add(decl_declaring_files(graph)["type://w"])
    assert picks == {"a.h"}


_CHILD = r"""
import json, random, sys
sys.path.insert(0, sys.argv[1])
from test_graph_reconcile_order_invariance import _side, _summary
decls_old = [("type://o%d" % i, "ns::T%d" % i, sorted({"a.h", "b.h", "c.h", "d/e.h"} - {"abcd"[i % 4] + ".h"})) for i in range(12)]
decls_new = [("type://n%d" % i, "ns::T%d" % i, sorted({"a.h", "b.h", "c.h", "d/e.h"} - {"dcba"[i % 4] + ".h"}), ) for i in range(10)]
old, removed = _side(decls_old, edge_order_seed=int(sys.argv[2]))
new, added = _side(decls_new, edge_order_seed=int(sys.argv[2]) + 1)
print(json.dumps(_summary(old, new, removed, added), sort_keys=True))
"""


def _run(seed: str, order: int) -> Any:
    env = {**os.environ, "PYTHONHASHSEED": seed}
    out = subprocess.run(
        [sys.executable, "-c", _CHILD, os.path.dirname(__file__), str(order)],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def test_reconciliation_is_identical_under_every_hash_seed_and_order() -> None:
    runs = [_run(seed, order) for seed in ("0", "1", "7", "12345") for order in (0, 5)]
    assert runs[0]["pairs"], "fixture must reconcile something"
    for other in runs[1:]:
        assert other == runs[0]
