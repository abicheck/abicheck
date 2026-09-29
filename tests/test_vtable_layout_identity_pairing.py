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

"""ADR-063 2B: the vtable-layout detector pairs records by canonical identity.

It used to key both sides by bare ``RecordType.name``, so two header-mode
classes sharing a leaf spelling (``ns1::Impl``/``ns2::Impl``, both
``name="Impl"``) overwrote each other -- last listed wins -- and the detector
compared whichever survived on each side. The contract, stated over every
listing order rather than one:

* an unchanged pair of same-leaf classes yields no finding, in any order on
  either side (the oracle: nothing changed, so nothing may be reported);
* a real virtual-base reorder in exactly one of them is reported exactly
  once, in every order (negative control: the pairing still finds real
  changes);
* a namespace move is not a pair (``diff_types``' own ``lookup_matched_type``
  rule), so no layout finding is fabricated across it.
"""

from __future__ import annotations

import itertools

import pytest

from abicheck.diff_vtable_layout import _diff_vtable_layout
from abicheck.model import AbiSnapshot, RecordType
from abicheck.model.change_catalog.kinds import ChangeKind


def _rec(ns: str, name: str, vbases: list[str]) -> RecordType:
    return RecordType(
        name=name,
        qualified_name=f"{ns}::{name}",
        kind="class",
        size_bits=64,
        vtable=["_ZN1X1fEv"],
        virtual_bases=vbases,
    )


def _bases() -> list[RecordType]:
    return [
        RecordType(name=b, qualified_name=b, kind="class", size_bits=64, vtable=["v"])
        for b in ("X", "Y", "Z")
    ]


def _snap(records: list[RecordType]) -> AbiSnapshot:
    return AbiSnapshot(library="lib.so", version="1", types=[*_bases(), *records])


def _findings(old: list[RecordType], new: list[RecordType]) -> list[tuple]:
    return sorted(
        (c.kind.value, c.symbol, c.old_value, c.new_value)
        for c in _diff_vtable_layout(_snap(old), _snap(new))
    )


def _unchanged() -> list[RecordType]:
    return [_rec("ns1", "Impl", ["X", "Y"]), _rec("ns2", "Impl", ["Y", "Z"])]


@pytest.mark.parametrize("old_order", list(itertools.permutations(range(2))))
@pytest.mark.parametrize("new_order", list(itertools.permutations(range(2))))
def test_same_leaf_classes_never_cross_pair(old_order, new_order):
    old = [_unchanged()[i] for i in old_order]
    new = [_unchanged()[i] for i in new_order]
    assert _findings(old, new) == []


@pytest.mark.parametrize("old_order", list(itertools.permutations(range(2))))
@pytest.mark.parametrize("new_order", list(itertools.permutations(range(2))))
def test_a_real_reorder_is_reported_once_in_every_order(old_order, new_order):
    old_recs = _unchanged()
    new_recs = [_rec("ns1", "Impl", ["Y", "X"]), _rec("ns2", "Impl", ["Y", "Z"])]
    got = _findings([old_recs[i] for i in old_order], [new_recs[i] for i in new_order])
    assert got == [
        (ChangeKind.VIRTUAL_BASE_OFFSET_CHANGED.value, "Impl", "X, Y", "Y, X")
    ]


def test_a_namespace_move_is_not_paired():
    old = [_rec("tbb::d1", "graph", ["X", "Y"])]
    new = [_rec("tbb::d2", "graph", ["Y", "X"])]
    assert _findings(old, new) == []
