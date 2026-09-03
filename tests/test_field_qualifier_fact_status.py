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

"""``TypeField`` const/volatile/mutable qualifier facts (ADR-063 Phase 5B).

``_check_field_qualifier_pair`` (``diff_types_field_facts.py``) previously
compared ``is_const``/``is_volatile``/``is_mutable`` as bare booleans, with
only a whole-snapshot ``header_cv_facts_reliable`` gate upstream -- no
per-field ``FactStatus`` check, unlike the already-migrated
``Param.is_va_list``. These tests pin the direct-``FactStatus`` gating this
phase adds, mirroring ``tests/test_g23_vtable_b2.py``'s
``TestReconstructionFactStatus`` and ``tests/test_diff_layout.py``'s
equivalent vtable-slice tests.
"""

from __future__ import annotations

from abicheck.checker import ChangeKind, compare
from abicheck.model import AbiSnapshot, Fact, RecordType, TypeField


def _snap(version: str, *, types: list[RecordType]) -> AbiSnapshot:
    return AbiSnapshot(library="lib.so", version=version, types=types)


def _rec(*fields: TypeField, name: str = "Cfg") -> RecordType:
    return RecordType(name=name, kind="struct", size_bits=32, fields=list(fields))


def _kinds(old: AbiSnapshot, new: AbiSnapshot) -> set[ChangeKind]:
    return {c.kind for c in compare(old, new).changes}


class TestFieldQualifierFactStatusGating:
    def test_uncollected_old_side_is_const_declines(self) -> None:
        # Old side's is_const_fact was never actually collected (e.g. a
        # mixed-producer/hybrid dump) -- must not read the resting `False`
        # as confirmed non-const.
        old = _snap(
            "1",
            types=[
                _rec(TypeField("val", "int", 0, is_const_fact=Fact.not_collected()))
            ],
        )
        new = _snap("2", types=[_rec(TypeField("val", "int", 0, is_const=True))])
        assert ChangeKind.FIELD_BECAME_CONST not in _kinds(old, new)

    def test_uncollected_old_side_is_volatile_declines(self) -> None:
        old = _snap(
            "1",
            types=[
                _rec(TypeField("val", "int", 0, is_volatile_fact=Fact.not_collected()))
            ],
        )
        new = _snap("2", types=[_rec(TypeField("val", "int", 0, is_volatile=True))])
        assert ChangeKind.FIELD_BECAME_VOLATILE not in _kinds(old, new)

    def test_uncollected_old_side_is_mutable_declines(self) -> None:
        old = _snap(
            "1",
            types=[
                _rec(TypeField("val", "int", 0, is_mutable_fact=Fact.not_collected()))
            ],
        )
        new = _snap("2", types=[_rec(TypeField("val", "int", 0, is_mutable=True))])
        assert ChangeKind.FIELD_BECAME_MUTABLE not in _kinds(old, new)

    def test_partial_empty_is_const_declines_too(self) -> None:
        # PARTIAL is "usable evidence" per Fact.is_present, but for a scalar
        # bool there's no "uncovered remainder" risk the way a list has --
        # unlike vtable_fact, is_const_fact is never emitted as PARTIAL by
        # any real producer. Still, compare_facts treats PARTIAL as
        # comparable (matching is_va_list's own established policy for a
        # per-parameter bool), so this is a comparable case, pinned here to
        # document the (currently unreachable) boundary explicitly.
        old = _snap(
            "1",
            types=[_rec(TypeField("val", "int", 0, is_const_fact=Fact.partial(False)))],
        )
        new = _snap("2", types=[_rec(TypeField("val", "int", 0, is_const=True))])
        assert ChangeKind.FIELD_BECAME_CONST in _kinds(old, new)

    def test_confirmed_present_still_fires(self) -> None:
        # Unaffected: an ordinary construction (no explicit *_fact given)
        # backfills to Fact.present(...) and behaves exactly as before.
        old = _snap("1", types=[_rec(TypeField("val", "int", 0, is_const=False))])
        new = _snap("2", types=[_rec(TypeField("val", "int", 0, is_const=True))])
        assert ChangeKind.FIELD_BECAME_CONST in _kinds(old, new)

    def test_each_qualifier_gated_independently(self) -> None:
        # An uncollected is_const_fact must not suppress a separately-
        # evidenced is_volatile transition on the same field pair.
        old = _snap(
            "1",
            types=[
                _rec(
                    TypeField(
                        "val",
                        "int",
                        0,
                        is_const_fact=Fact.not_collected(),
                        is_volatile=False,
                    )
                )
            ],
        )
        new = _snap(
            "2",
            types=[_rec(TypeField("val", "int", 0, is_const=True, is_volatile=True))],
        )
        kinds = _kinds(old, new)
        assert ChangeKind.FIELD_BECAME_CONST not in kinds
        assert ChangeKind.FIELD_BECAME_VOLATILE in kinds
