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

"""``diff_surface_metrics``'s old/new ``public_entity_ids`` pair (ADR-063
Phase 3 D5) -- each side's own resolved set reaches its own matching
``compute_surface_metrics()`` call, never the other side's."""

from __future__ import annotations

import abicheck.diff_surface_metrics as diff_surface_metrics_module
from abicheck.diff_surface_metrics import diff_surface_metrics
from abicheck.model import AbiSnapshot
from abicheck.model.identity import entity_id_for_function


def _snap(name: str) -> AbiSnapshot:
    return AbiSnapshot(library=name, version="1.0")


def test_each_side_receives_its_own_set_not_the_others(monkeypatch) -> None:
    old_ids = frozenset({entity_id_for_function((), "f")})
    new_ids = frozenset({entity_id_for_function((), "g")})
    calls: list[tuple[AbiSnapshot, frozenset | None]] = []

    real_compute = diff_surface_metrics_module.compute_surface_metrics

    def _spy(snap, *, top_n=10, public_entity_ids=None):
        calls.append((snap, public_entity_ids))
        return real_compute(snap, top_n=top_n, public_entity_ids=public_entity_ids)

    monkeypatch.setattr(diff_surface_metrics_module, "compute_surface_metrics", _spy)

    old = _snap("old")
    new = _snap("new")
    diff_surface_metrics(
        old,
        new,
        old_public_entity_ids=old_ids,
        new_public_entity_ids=new_ids,
    )

    assert calls == [(old, old_ids), (new, new_ids)]


def test_none_pair_is_the_default_and_reaches_both_sides_as_none(monkeypatch) -> None:
    calls: list[frozenset | None] = []
    real_compute = diff_surface_metrics_module.compute_surface_metrics

    def _spy(snap, *, top_n=10, public_entity_ids=None):
        calls.append(public_entity_ids)
        return real_compute(snap, top_n=top_n, public_entity_ids=public_entity_ids)

    monkeypatch.setattr(diff_surface_metrics_module, "compute_surface_metrics", _spy)

    diff_surface_metrics(_snap("old"), _snap("new"))
    assert calls == [None, None]


def test_result_unchanged_when_pair_omitted(monkeypatch) -> None:
    old = _snap("old")
    new = _snap("new")
    baseline = diff_surface_metrics(old, new)
    explicit_none = diff_surface_metrics(
        old, new, old_public_entity_ids=None, new_public_entity_ids=None
    )
    assert baseline == explicit_none
