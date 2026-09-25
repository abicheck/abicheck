# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""A live-built graph shares repeated values without changing what it says."""

from __future__ import annotations

import json
import random

from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.source_graph import SourceGraphSummary

_VALUES = ["public", "private", "param", "return", 0, 1, True, False, None, 2.5]


def _graph(seed: int, share: bool) -> SourceGraphSummary:
    rng = random.Random(seed)
    g = SourceGraphSummary()
    if not share:
        g._shared = _NoShare()  # type: ignore[assignment]
    ids = [f"decl://f{i}" for i in range(30)]
    for nid in ids:
        attrs = {
            k: rng.choice(_VALUES)
            for k in rng.sample(["role", "visibility", "n"], rng.randint(0, 3))
        }
        g.add_node(
            GraphNode(
                id=nid,
                kind=rng.choice(["function", "record"]),
                attrs=attrs,
                provenance="p" + "x" * rng.randint(0, 1),
            )
        )
    for _ in range(80):
        attrs = {"resolution": rng.choice(_VALUES), "role": rng.choice(_VALUES)}
        if rng.random() < 0.1:
            attrs["occurrences"] = [rng.randint(0, 3)]  # non-scalar: never shared
        g.add_edge(
            GraphEdge(
                src=rng.choice(ids),
                dst=rng.choice(ids),
                kind=rng.choice(["calls", "uses"]),
                attrs=attrs,
            )
        )
    return g.finalize()


class _NoShare:
    def share(self, entity) -> None:
        return None


def test_sharing_never_changes_the_serialized_graph():
    for seed in range(25):
        shared, plain = _graph(seed, True), _graph(seed, False)
        assert json.dumps(shared.to_dict(), sort_keys=True) == json.dumps(
            plain.to_dict(), sort_keys=True
        )
        assert shared.graph_id == plain.graph_id


def test_equal_scalar_attrs_share_one_dict_and_bool_never_merges_with_int():
    a = GraphNode(id="decl://a", kind="function", attrs={"n": 1})
    b = GraphNode(id="decl://b", kind="function", attrs={"n": 1})
    c = GraphNode(id="decl://c", kind="function", attrs={"n": True})
    g = SourceGraphSummary()
    for n in (a, b, c):
        g.add_node(n)
    assert a.facts[0].attrs is b.facts[0].attrs
    assert c.facts[0].attrs is not a.facts[0].attrs
    assert c.facts[0].attrs["n"] is True and a.facts[0].attrs["n"] == 1
    # Single-fact alias kept: attrs/resolved are the fact's own dict.
    assert a.attrs is a.resolved is a.facts[0].attrs


def test_non_scalar_attrs_are_never_shared():
    g = SourceGraphSummary()
    x = GraphNode(id="decl://x", kind="function", attrs={"v": [1]})
    y = GraphNode(id="decl://y", kind="function", attrs={"v": [1]})
    g.add_node(x)
    g.add_node(y)
    assert x.facts[0].attrs is not y.facts[0].attrs
    x.facts[0].attrs["v"].append(2)
    assert y.facts[0].attrs["v"] == [1]
