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

"""Harness H5 cell: the packed contract type graph equals the tuple form
(family F5, bug class perf.retained_per_member_state_kept_in_its_working_form).

ADR-062 A2.5 holds ``contract_evidence.TypeGraphSnapshot`` packed (a sorted
string table plus index pairs, compressed) so a release does not keep every
finished member's whole-surface graph as tuples of strings. The cell runs the
real ``compare --contract exports`` CLI on one member pair -- whose JSON report
serializes the persisted type graph, unlike a release report -- with the
packed class and with a patched-in reference that holds the graph as the
``sorted(set(...))`` tuples the class used to store, and requires the
canonical reports to be byte-equal. The reference counts its constructions,
so the reference run provably used it; the seeded mutants prove the report
sees a dropped edge or node.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from test_family_f5_optimization_reference import _assert_same, _release_dirs, _run

from abicheck.contract_evidence import TypeGraphSnapshot as Graph

_Mutate = Callable[[tuple[str, ...], tuple[tuple[str, str], ...]], tuple[Any, Any]]


def _identity(nodes: Any, edges: Any) -> tuple[Any, Any]:
    return nodes, edges


def _member_args(root: Path) -> tuple[str, ...]:
    old, new = _release_dirs(root)
    member = sorted(old.iterdir())[0].name
    return (str(old / member), str(new / member), "--contract", "exports")


def _tuple_reference_run(
    monkeypatch: pytest.MonkeyPatch, args: tuple[str, ...], mutate: _Mutate
) -> tuple[str, int]:
    """The report with the graph held as plain sorted tuples."""
    validate = Graph.__init__
    built = [0]

    def tuple_init(self: Any, nodes: Any = (), edges: Any = ()) -> None:
        validate(self, nodes, edges)  # same argument validation
        pairs = tuple(sorted({(e[0], e[1]) for e in edges}))
        held = mutate(tuple(sorted(set(nodes))), pairs)
        object.__setattr__(self, "_packed", held)
        built[0] += 1

    with monkeypatch.context() as mp:
        mp.setattr(Graph, "__init__", tuple_init)
        mp.setattr(Graph, "nodes", property(lambda self: self._packed[0]))
        mp.setattr(Graph, "edges", property(lambda self: self._packed[1]))
        report = _run(*args)
    return report, built[0]


def test_member_packed_type_graph_matches_tuple_reference(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    args = _member_args(tmp_path / "rel")
    packed = _run(*args)
    assert '"type_graph"' in packed and '"edges": [\n' in packed, packed[:500]
    reference, built = _tuple_reference_run(monkeypatch, args, _identity)
    assert built > 0, "the reference run never built a graph"
    _assert_same(packed, reference, "member.packed_type_graph")


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(lambda n, e: (n, e[1:]), id="drops-an-edge"),
        pytest.param(lambda n, e: (n[1:], e), id="drops-a-node"),
    ],
)
def test_mutant_lossy_graph_form_is_caught(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mutate: _Mutate
) -> None:
    args = _member_args(tmp_path / "rel")
    packed = _run(*args)
    mutant, built = _tuple_reference_run(monkeypatch, args, mutate)
    assert built > 0
    assert mutant != packed, "oracle missed a graph form that loses an entry"
