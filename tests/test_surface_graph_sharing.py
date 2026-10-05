# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Contract of ``surface_graph.shared_surface_graphs``.

``compare()`` opens the scope around ``--surface-metrics`` and
``--pattern-verdicts`` so each side's graph is built once for both stages.
The scope must (a) share only a graph for the very same snapshot object and
an equal public-id set, (b) change nothing outside a scope, and (c) release
everything when it closes -- the reason it, and not the comparison memo,
owns the graphs.
"""

from __future__ import annotations

import gc
import weakref

from abicheck import surface_graph as sg
from abicheck.checker import compare
from abicheck.model.declarations import Function, Visibility
from abicheck.model.snapshot import AbiSnapshot


def _snap(tag: str, count: int = 3) -> AbiSnapshot:
    return AbiSnapshot(
        library="lib",
        version=tag,
        functions=[
            Function(
                name=f"f{i}",
                mangled=f"_Z2f{i}v",
                return_type="int",
                visibility=Visibility.PUBLIC,
            )
            for i in range(count)
        ],
    )


def _counting(monkeypatch) -> list[int]:
    builds: list[int] = []
    real = sg._build_surface_graph

    def spy(snap, *, public_entity_ids):
        builds.append(id(snap))
        return real(snap, public_entity_ids=public_entity_ids)

    monkeypatch.setattr(sg, "_build_surface_graph", spy)
    return builds


def test_outside_a_scope_every_call_builds(monkeypatch) -> None:
    builds = _counting(monkeypatch)
    snap = _snap("1")
    first = sg.build_surface_graph(snap)
    second = sg.build_surface_graph(snap)
    assert len(builds) == 2
    assert first is not second
    assert first == second


def test_inside_a_scope_the_same_request_is_built_once(monkeypatch) -> None:
    builds = _counting(monkeypatch)
    snap = _snap("1")
    with sg.shared_surface_graphs():
        a = sg.build_surface_graph(snap, public_entity_ids=frozenset())
        b = sg.build_surface_graph(snap, public_entity_ids=frozenset())
    assert a is b
    assert len(builds) == 1


def test_a_different_snapshot_or_id_set_is_never_shared(monkeypatch) -> None:
    builds = _counting(monkeypatch)
    one, other = _snap("1"), _snap("1")  # equal content, distinct objects
    with sg.shared_surface_graphs():
        g_none = sg.build_surface_graph(one)
        g_empty = sg.build_surface_graph(one, public_entity_ids=frozenset())
        g_other = sg.build_surface_graph(other)
    assert len({id(g_none), id(g_empty), id(g_other)}) == 3
    assert len(builds) == 3
    assert g_other.snapshot is other


def test_the_scope_releases_its_graphs(monkeypatch) -> None:
    snap = _snap("1")
    with sg.shared_surface_graphs():
        ref = weakref.ref(sg.build_surface_graph(snap))
        gc.collect()
        assert ref() is not None  # held for the scope's duration
    gc.collect()
    assert ref() is None


def test_compare_builds_each_side_once_with_both_stages(monkeypatch) -> None:
    """The end-to-end claim: both opt-in stages, one build per side."""
    builds = _counting(monkeypatch)
    old, new = _snap("1"), _snap("2", count=4)
    result = compare(old, new, pattern_verdicts=True, surface_metrics=True)
    assert sorted(builds) == sorted([id(old), id(new)])
    # And the result is the one each stage produces on its own graphs.
    alone = compare(_snap("1"), new, pattern_verdicts=True, surface_metrics=True)
    assert [c.kind for c in result.changes] == [c.kind for c in alone.changes]
