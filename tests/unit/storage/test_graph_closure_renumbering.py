# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""The L5/surface graph's closure markers are renumbered with the flat
snapshot's (known gap "The L5 source graph's own node identities are never
renumbered alongside the flat snapshot's closure markers").

Generated over headers x closure coordinates, each closure either named by
the flat fields (and the graph) or by the graph only, written raw
(``(lambda at <path>:L:C)``) or stripped (``(lambda:h:L:C)``). The oracle is
computed here from the generated coordinates, not by the implementation: a
flat closure's ordinal is its position rank among the flat closures of its
``(marker, header)`` group; a graph-only closure's is the group's flat count
plus its rank among the graph-only ones. Checked on the dump path (a live
graph) and the load path (a stored graph decoded on first access), where
the lazily renumbered graph must equal the eager decode-then-renumber of the
same stored graph.
"""

from __future__ import annotations

import json
from typing import Any

from hypothesis import HealthCheck, given, settings, strategies as st

from abicheck.model import AbiSnapshot, Function
from abicheck.model.graph_facts import GraphEdge, GraphNode
from abicheck.model.lazy_graph import is_graph_decoded
from abicheck.model.source_graph import SourceGraphSummary
from abicheck.serialization import snapshot_from_dict, snapshot_to_json
from abicheck.storage.closure_graph_renumber import renumber_graph
from abicheck.storage.closure_identity import (
    graph_rewrite_factory,
    renumber_anonymous_closure_identities,
)

_HEADERS = ("a.h", "b.h")


@st.composite
def _cases(draw: st.DrawFn) -> dict[str, Any]:
    closures = []
    for header in _HEADERS:
        coords = draw(
            st.sets(
                st.tuples(st.integers(1, 40), st.integers(1, 9)), min_size=0, max_size=5
            )
        )
        for line, col in sorted(coords):
            closures.append(
                {
                    "header": header,
                    "line": line,
                    "col": col,
                    "flat": draw(st.booleans()),
                    "raw": draw(st.booleans()),
                }
            )
    if not closures:
        closures.append(
            {"header": "a.h", "line": 1, "col": 1, "flat": False, "raw": False}
        )
    return {"closures": closures}


def _spelling(c: dict[str, Any], *, raw: bool) -> str:
    if raw:
        return f"(lambda at /src/inc/{c['header']}:{c['line']}:{c['col']})"
    return f"(lambda:{c['header']}:{c['line']}:{c['col']})"


def _expected(closures: list[dict[str, Any]]) -> dict[tuple[str, int, int], int]:
    out: dict[tuple[str, int, int], int] = {}
    for header in _HEADERS:
        group = [c for c in closures if c["header"] == header]
        flat = sorted((c["line"], c["col"]) for c in group if c["flat"])
        only = sorted((c["line"], c["col"]) for c in group if not c["flat"])
        for rank, (line, col) in enumerate(flat, start=1):
            out[(header, line, col)] = rank
        for rank, (line, col) in enumerate(only, start=len(flat) + 1):
            out[(header, line, col)] = rank
    return out


def _snapshot(closures: list[dict[str, Any]]) -> AbiSnapshot:
    functions = [
        Function(
            name=f"f<{_spelling(c, raw=False)}>",
            mangled=f"_Z1f{i}",
            return_type="void",
        )
        for i, c in enumerate(closures)
        if c["flat"]
    ]
    nodes = [
        GraphNode(
            id=f"decl://f<{_spelling(c, raw=c['raw'])}>",
            kind="source_decl",
            label=f"f<{_spelling(c, raw=c['raw'])}>",
        )
        for c in closures
    ]
    edges = [
        GraphEdge(src=nodes[i].id, dst=nodes[i + 1].id, kind="decl_uses_type")
        for i in range(len(nodes) - 1)
    ]
    snap = AbiSnapshot(library="libc.so.1", version="1", functions=functions)
    snap.surface_graph = SourceGraphSummary(nodes=nodes, edges=edges)
    return snap


def _ordinal_ids(closures: list[dict[str, Any]]) -> set[str]:
    expected = _expected(closures)
    return {
        f"decl://f<(lambda:{c['header']}#{expected[(c['header'], c['line'], c['col'])]})>"
        for c in closures
    }


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_cases())
def test_dump_path_renumbers_the_graph_with_the_flat_fields(case: dict) -> None:
    closures = case["closures"]
    snap = renumber_anonymous_closure_identities(_snapshot(closures))
    expected = _expected(closures)
    assert {f.name for f in snap.declarations.functions} == {
        f"f<(lambda:{c['header']}#{expected[(c['header'], c['line'], c['col'])]})>"
        for c in closures
        if c["flat"]
    }
    graph = snap.surface_graph
    assert {n.id for n in graph.nodes} == _ordinal_ids(closures)
    node_ids = {n.id for n in graph.nodes}
    assert all(e.src in node_ids and e.dst in node_ids for e in graph.edges)


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(_cases())
def test_load_path_renumbers_on_first_decode_as_eager_does(case: dict) -> None:
    closures = case["closures"]
    # Store without renumbering, as a baseline written before this fix.
    snap = _snapshot(closures)
    document = json.loads(snapshot_to_json(snap))
    loaded = snapshot_from_dict(json.loads(json.dumps(document)))
    assert not is_graph_decoded(loaded, "surface_graph")
    lazy = loaded.surface_graph
    assert {n.id for n in lazy.nodes} == _ordinal_ids(closures)
    # Eager oracle: the same stored graph decoded with no renumbering, then
    # renumbered against the flat ordinals the oracle derives.
    expected = _expected(closures)
    flat_ordinals = {
        ("(lambda", c["header"], c["line"], c["col"]): expected[
            (c["header"], c["line"], c["col"])
        ]
        for c in closures
        if c["flat"]
    }
    eager_source = SourceGraphSummary.from_dict(snap.surface_graph.to_dict())
    eager = renumber_graph(eager_source, graph_rewrite_factory(flat_ordinals))
    assert lazy.to_dict() == eager.to_dict()
