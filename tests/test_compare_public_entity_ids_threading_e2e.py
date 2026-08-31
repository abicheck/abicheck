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

"""ADR-063 Phase 3 (D5), slice 13: the threaded path end to end.

``service.compare_snapshots(..., pattern_verdicts=True, surface_metrics=True)``
must actually deliver each side's own ``PublicSurfaceQuery().resolve()``
result all the way through ``checker.compare()`` to both the
``pattern_verdicts.py``/``diff_surface_metrics.py`` boundary (the
``old_public_entity_ids``/``new_public_entity_ids`` pair) and the
``surface_graph.py`` boundary (each side's own singular
``public_entity_ids`` argument to its matching ``build_surface_graph()``/
``compute_surface_metrics()`` call) -- confirmed by patching *both*
boundaries in the same run, since a regression could silently drop the
threading at either one while leaving the other intact.
"""

from __future__ import annotations

import abicheck.diff_surface_metrics as diff_surface_metrics_module
import abicheck.pattern_verdicts as pattern_verdicts_module
from abicheck.model import AbiSnapshot, Function, Visibility
from abicheck.model.identity import entity_id_for_function
from abicheck.policy.public_surface import PublicSurfaceQuery
from abicheck.service import compare_snapshots


def _snap(name: str) -> AbiSnapshot:
    eid = entity_id_for_function((), "f", mangled_name="_Z1fv")
    fn = Function(
        name="f",
        mangled="_Z1fv",
        return_type="void",
        params=[],
        visibility=Visibility.PUBLIC,
        entity_id=eid,
    )
    return AbiSnapshot(library=name, version="1.0", functions=[fn])


def test_resolved_pair_reaches_both_boundaries_through_compare_snapshots(
    monkeypatch,
) -> None:
    old = _snap("old")
    new = _snap("new")

    # Independently compute what compare_snapshots() must resolve and
    # thread through, so the assertions below check against a value this
    # test derived itself -- not the same code path being tested.
    expected_old_ids = PublicSurfaceQuery().resolve(old)
    expected_new_ids = PublicSurfaceQuery().resolve(new)
    assert (
        expected_old_ids and expected_new_ids
    )  # sanity: fixture resolves to something

    # Boundary (a): checker._apply_pattern_verdicts_step/_apply_surface_metrics
    # -> pattern_verdicts.apply_pattern_verdicts / diff_surface_metrics.diff_surface_metrics.
    pv_calls: list[tuple[object, object]] = []
    real_apply_pattern_verdicts = pattern_verdicts_module.apply_pattern_verdicts

    def _apply_pattern_verdicts_spy(*args, **kwargs):
        pv_calls.append(
            (kwargs.get("old_public_entity_ids"), kwargs.get("new_public_entity_ids"))
        )
        return real_apply_pattern_verdicts(*args, **kwargs)

    monkeypatch.setattr(
        pattern_verdicts_module, "apply_pattern_verdicts", _apply_pattern_verdicts_spy
    )

    dsm_calls: list[tuple[object, object]] = []
    real_diff_surface_metrics = diff_surface_metrics_module.diff_surface_metrics

    def _diff_surface_metrics_spy(*args, **kwargs):
        dsm_calls.append(
            (kwargs.get("old_public_entity_ids"), kwargs.get("new_public_entity_ids"))
        )
        return real_diff_surface_metrics(*args, **kwargs)

    monkeypatch.setattr(
        diff_surface_metrics_module, "diff_surface_metrics", _diff_surface_metrics_spy
    )

    # Boundary (b): pattern_verdicts.py/diff_surface_metrics.py's own module-
    # level imports of build_surface_graph/compute_surface_metrics -- each
    # side's own *singular* public_entity_ids argument to its matching call.
    bsg_calls: list[tuple[object, object]] = []
    real_build_surface_graph = pattern_verdicts_module.build_surface_graph

    def _build_surface_graph_spy(snap, *, public_entity_ids=None):
        bsg_calls.append((snap, public_entity_ids))
        return real_build_surface_graph(snap, public_entity_ids=public_entity_ids)

    monkeypatch.setattr(
        pattern_verdicts_module, "build_surface_graph", _build_surface_graph_spy
    )

    csm_calls: list[tuple[object, object]] = []
    real_compute_surface_metrics = diff_surface_metrics_module.compute_surface_metrics

    def _compute_surface_metrics_spy(snap, *, top_n=10, public_entity_ids=None):
        csm_calls.append((snap, public_entity_ids))
        return real_compute_surface_metrics(
            snap, top_n=top_n, public_entity_ids=public_entity_ids
        )

    monkeypatch.setattr(
        diff_surface_metrics_module,
        "compute_surface_metrics",
        _compute_surface_metrics_spy,
    )

    compare_snapshots(old, new, pattern_verdicts=True, surface_metrics=True)

    # Boundary (a): both steps received the resolved pair, never None/None.
    assert pv_calls == [(expected_old_ids, expected_new_ids)]
    assert dsm_calls == [(expected_old_ids, expected_new_ids)]

    # Boundary (b): each side's own build_surface_graph()/
    # compute_surface_metrics() call received its own matching singular id
    # set -- never the other side's, and never None.
    assert bsg_calls == [(old, expected_old_ids), (new, expected_new_ids)]
    assert csm_calls == [(old, expected_old_ids), (new, expected_new_ids)]


def test_pair_stays_none_when_pattern_verdicts_and_surface_metrics_both_off(
    monkeypatch,
) -> None:
    """The opt-in flags gate everything -- neither boundary is even reached
    when both features are off (the pre-Phase-3 default for every existing
    caller)."""
    old = _snap("old")
    new = _snap("new")

    calls: list[object] = []
    monkeypatch.setattr(
        pattern_verdicts_module,
        "apply_pattern_verdicts",
        lambda *a, **kw: calls.append(1),
    )
    monkeypatch.setattr(
        diff_surface_metrics_module,
        "diff_surface_metrics",
        lambda *a, **kw: calls.append(1),
    )

    compare_snapshots(old, new, pattern_verdicts=False, surface_metrics=False)

    assert calls == []
