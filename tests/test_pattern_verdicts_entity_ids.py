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

"""``apply_pattern_verdicts``'s old/new ``public_entity_ids`` pair (ADR-063
Phase 3 D5) -- each side's own resolved set reaches its own matching
``build_surface_graph()`` call, never the other side's."""

from __future__ import annotations

import abicheck.pattern_verdicts as pattern_verdicts_module
from abicheck.checker_policy import EvidenceTier
from abicheck.model import AbiSnapshot
from abicheck.model.identity import entity_id_for_function
from abicheck.pattern_verdicts import apply_pattern_verdicts


def _snap(name: str) -> AbiSnapshot:
    return AbiSnapshot(library=name, version="1.0")


def test_each_side_receives_its_own_set_not_the_others(monkeypatch) -> None:
    old_ids = frozenset({entity_id_for_function((), "f")})
    new_ids = frozenset({entity_id_for_function((), "g")})
    calls: list[tuple[AbiSnapshot, frozenset | None]] = []

    real_build = pattern_verdicts_module.build_surface_graph

    def _spy(snap, *, public_entity_ids=None):
        calls.append((snap, public_entity_ids))
        return real_build(snap, public_entity_ids=public_entity_ids)

    monkeypatch.setattr(pattern_verdicts_module, "build_surface_graph", _spy)

    old = _snap("old")
    new = _snap("new")
    apply_pattern_verdicts(
        [],
        old,
        new,
        evidence_tier=EvidenceTier.HEADER_AWARE,
        old_public_entity_ids=old_ids,
        new_public_entity_ids=new_ids,
    )

    assert calls == [(old, old_ids), (new, new_ids)]


def test_none_pair_is_the_default_and_reaches_both_sides_as_none(monkeypatch) -> None:
    calls: list[frozenset | None] = []
    real_build = pattern_verdicts_module.build_surface_graph

    def _spy(snap, *, public_entity_ids=None):
        calls.append(public_entity_ids)
        return real_build(snap, public_entity_ids=public_entity_ids)

    monkeypatch.setattr(pattern_verdicts_module, "build_surface_graph", _spy)

    apply_pattern_verdicts(
        [], _snap("old"), _snap("new"), evidence_tier=EvidenceTier.HEADER_AWARE
    )
    assert calls == [None, None]


def test_disabled_never_calls_build_surface_graph(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        pattern_verdicts_module,
        "build_surface_graph",
        lambda *a, **kw: calls.append(1),
    )
    result = apply_pattern_verdicts(
        [],
        _snap("old"),
        _snap("new"),
        evidence_tier=EvidenceTier.HEADER_AWARE,
        enabled=False,
    )
    assert result == []
    assert calls == []
