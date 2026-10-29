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

"""A DWARF layout member carries the ABI fact of its type (schema v69).

Bug class ``evidence.depth_projection_diverges_from_shallow_dump``: a binary-
only dump reads each member's fundamental-type fact from its DIE, and the
depth projection rebuilds the same member from the persisted layout
(``DwarfMetadata.structs``). Without the fact on that layout, a retype between
ABI-identical types read as a break on the projected side only. This module
pins the three links without a compiler: the layout member, the projected
record field, and the stored document (a pre-v69 document reads it unset).
"""

from __future__ import annotations

import copy

import pytest

from abicheck.compare.debug_layout_view import debug_layout_declarations
from abicheck.model import AbiSnapshot
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.dwarf_facts import DwarfMetadata, FieldInfo, StructLayout
from abicheck.model.fact import Fact
from abicheck.serialization import snapshot_from_dict
from abicheck.storage.snapshot_encode import snapshot_to_dict

_SIGNED_INT = AbiFundamental(FundamentalEncoding.SIGNED, 4, 4)


def _snap_with_member(fact: AbiFundamental | None) -> AbiSnapshot:
    layout = StructLayout(
        name="S",
        byte_size=8,
        fields=[
            FieldInfo(
                name="value",
                type_name="int",
                byte_offset=0,
                byte_size=4,
                type_abi_fact=fact,
            ),
            FieldInfo(name="next", type_name="S *", byte_offset=8, byte_size=8),
        ],
    )
    return AbiSnapshot(
        library="libfoo.so",
        version="1.0",
        dwarf=DwarfMetadata(has_dwarf=True, structs={"S": layout}),
    )


def _member(snap: AbiSnapshot, name: str) -> FieldInfo:
    layout = snap.declarations.debug_layout.structs["S"]
    return next(f for f in layout.fields if f.name == name)


def test_projected_record_field_keeps_the_layout_member_fact() -> None:
    snap = _snap_with_member(_SIGNED_INT)
    (record,) = [t for t in debug_layout_declarations(snap)[0] if t.name == "S"]
    value = next(f for f in record.fields if f.name == "value")
    assert value.type_abi_fact == Fact.present(_SIGNED_INT)


def test_projected_record_field_without_a_fundamental_has_no_fact() -> None:
    snap = _snap_with_member(None)
    (record,) = [t for t in debug_layout_declarations(snap)[0] if t.name == "S"]
    value = next(f for f in record.fields if f.name == "value")
    assert value.type_abi_fact is None


@pytest.mark.parametrize(
    "fact",
    [
        _SIGNED_INT,
        AbiFundamental(FundamentalEncoding.FLOAT, 16, 16),
        AbiFundamental(FundamentalEncoding.UNSIGNED, 2, 2, 8),
    ],
    ids=["int32", "long-double-sized", "vector-of-8-uint16"],
)
def test_layout_member_fact_round_trips_through_the_document(
    fact: AbiFundamental,
) -> None:
    loaded = snapshot_from_dict(snapshot_to_dict(_snap_with_member(fact)))
    assert _member(loaded, "value").type_abi_fact == fact
    # A member DWARF states no fundamental for stays unset, as a binary-only
    # dump's pointer TypeField does.
    assert _member(loaded, "next").type_abi_fact is None


def test_pre_v69_document_reads_the_layout_member_fact_unset() -> None:
    doc = snapshot_to_dict(_snap_with_member(_SIGNED_INT))
    old = copy.deepcopy(doc)
    old["schema_version"] = 68
    for member in old["dwarf"]["structs"]["S"]["fields"]:
        member.pop("type_abi_fact", None)
    assert _member(snapshot_from_dict(old), "value").type_abi_fact is None
