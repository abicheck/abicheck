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

"""Struct returns on the ``call`` axis: SysV classification and member layouts.

The oracle for the pair verdicts is execution, not this module's helpers:
``formal/struct_ret_fragment_observed.json`` records which of the 1190
member-layout changes of ``formal/experiments/struct_ret_fragment.py`` an old
caller survived when run against the new library built by gcc and by clang.
The classification is checked against that script's own psABI classifier.
The Lean model's equivalence proof (``StructRetFragment.lean``) covers the
model's predicates, not this implementation; these tests check the
implementation against execution.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from abicheck.compare.call_interface import (
    AxisStatus,
    CallSignature,
    assess_call_interface,
)
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.call_interface import (
    SYSV_AMD64,
    ArgClass,
    Member,
    RetShape,
    ret_image,
)
from abicheck.model.entities import RecordType, TypeField
from abicheck.model.fact import Fact
from abicheck.model.sysv_aggregate import StructReturn, classify_record

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "struct_ret_fragment", _ROOT / "formal" / "experiments" / "struct_ret_fragment.py"
)
assert _spec is not None and _spec.loader is not None
frag = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(frag)
_OBSERVED = json.loads(
    (_ROOT / "formal" / "struct_ret_fragment_observed.json").read_text(encoding="utf-8")
)

_FACTS = {
    "int": AbiFundamental(FundamentalEncoding.SIGNED, 4, 4),
    "unsigned int": AbiFundamental(FundamentalEncoding.UNSIGNED, 4, 4),
    "long": AbiFundamental(FundamentalEncoding.SIGNED, 8, 8),
    "double": AbiFundamental(FundamentalEncoding.FLOAT, 8, 8),
    "float": AbiFundamental(FundamentalEncoding.FLOAT, 4, 4),
    "long double": AbiFundamental(FundamentalEncoding.FLOAT, 16, 16),
    "char": AbiFundamental(FundamentalEncoding.SIGNED, 1, 1),
}


def _field(name: str, ty: str, offset: int, bits: int | None = None) -> TypeField:
    fact = _FACTS.get(ty)
    return TypeField(
        name,
        ty,
        offset_bits=offset,
        is_bitfield=bits is not None,
        bitfield_bits=bits,
        type_abi_fact=None if fact is None else Fact.present(fact),
    )


def _record(
    name: str, fields: list[TypeField], size: int, align: int, **kw: object
) -> RecordType:
    return RecordType(
        name, "struct", size_bits=size, alignment_bits=align, fields=fields, **kw
    )


def _from_seq(seq: str) -> RecordType:
    members, size = frag.layout(seq)
    align = max(frag.TYPES[t][1] for t in seq)
    return _record(
        seq, [_field(n, c, o * 8) for n, c, o in members], size * 8, align * 8
    )


def _classify(
    rec: RecordType, *others: RecordType, c: bool = True, proved: bool = True
) -> StructReturn | None:
    by_name = {r.name: r for r in (rec, *others)}
    return classify_record(
        rec,
        lambda s: by_name.get(s.removeprefix("struct ")),
        c_language=c,
        trivial_for_calls_proved=proved,
    )


def _sig(ret: StructReturn) -> CallSignature:
    return CallSignature(
        (), ret.shape, False, "", ret_members=ret.members, ret_size=ret.size
    )


@pytest.mark.parametrize("seq", frag.structs())
def test_classification_matches_the_psabi_rules(seq: str) -> None:
    got = _classify(_from_seq(seq))
    assert got is not None and got.shape.value == frag.shape(seq)


def test_every_member_layout_change_matches_execution() -> None:
    intact = {tuple(p) for p in _OBSERVED["runs"]["gcc/oracle"]["intact_pairs"]}
    assert intact == {
        tuple(p) for p in _OBSERVED["runs"]["clang/oracle"]["intact_pairs"]
    }
    sigs = {s: _classify(_from_seq(s)) for s in frag.structs()}
    wrong = []
    for old, new in frag.space():
        o, n = sigs[old], sigs[new]
        assert o is not None and n is not None
        got = assess_call_interface(_sig(o), _sig(n), SYSV_AMD64).status
        want = AxisStatus.PRESERVED if (old, new) in intact else AxisStatus.BROKEN
        if got is not want:
            wrong.append((old, new, got, want))
    assert len(frag.space()) == _OBSERVED["cases"] == 1190
    assert wrong == []


def test_same_class_members_are_compared() -> None:
    two_long = _classify(_from_seq("ll"))
    int_int_long = _classify(_from_seq("iil"))
    assert two_long is not None and int_int_long is not None
    assert two_long.shape is int_int_long.shape is RetShape.INT2
    axis = assess_call_interface(_sig(two_long), _sig(int_int_long), SYSV_AMD64)
    assert axis.status is AxisStatus.BROKEN and "rax bit 0" in axis.reason


def test_a_memory_result_that_grows_overflows_the_old_buffer() -> None:
    old, new = _classify(_from_seq("lll")), _classify(_from_seq("llll"))
    assert old is not None and new is not None
    axis = assess_call_interface(_sig(old), _sig(new), SYSV_AMD64)
    assert axis.status is AxisStatus.BROKEN and "24 to 32 bytes" in axis.reason


@pytest.mark.parametrize(
    ("old_shape", "new_shape", "want"),
    [
        # The new result covers every register the old caller reads.
        (RetShape.INT1, RetShape.INT2, AxisStatus.UNKNOWN),
        (RetShape.SSE1, RetShape.SSE2, AxisStatus.UNKNOWN),
        (RetShape.INT2, RetShape.INT2, AxisStatus.UNKNOWN),
        (RetShape.MEMORY, RetShape.MEMORY, AxisStatus.UNKNOWN),
        # The old caller reads rdx / xmm1, which the new function never sets.
        (RetShape.INT2, RetShape.INT1, AxisStatus.BROKEN),
        (RetShape.SSE2, RetShape.SSE1, AxisStatus.BROKEN),
        (RetShape.INT1, RetShape.SSE2, AxisStatus.BROKEN),
        (RetShape.INT2, RetShape.MEMORY, AxisStatus.BROKEN),
        (RetShape.MEMORY, RetShape.INT1, AxisStatus.BROKEN),
    ],
)
def test_shape_only_struct_returns(
    old_shape: RetShape, new_shape: RetShape, want: AxisStatus
) -> None:
    old = CallSignature((), old_shape, False)
    new = CallSignature((), new_shape, False)
    assert assess_call_interface(old, new, SYSV_AMD64).status is want


@pytest.mark.parametrize(
    ("old_size", "new_size", "want"),
    [
        (192, 256, AxisStatus.BROKEN),
        (192, 1024, AxisStatus.BROKEN),
        (256, 256, AxisStatus.UNKNOWN),
        (256, 192, AxisStatus.UNKNOWN),
        (None, 256, AxisStatus.UNKNOWN),
    ],
)
def test_memory_growth_breaks_without_member_layouts(
    old_size: int | None, new_size: int | None, want: AxisStatus
) -> None:
    old = CallSignature((), RetShape.MEMORY, False, ret_size=old_size)
    new = CallSignature((), RetShape.MEMORY, False, ret_size=new_size)
    axis = assess_call_interface(old, new, SYSV_AMD64)
    assert axis.status is want
    if want is AxisStatus.BROKEN:
        assert "grew" in axis.reason


def test_a_scalar_never_matches_a_struct_member() -> None:
    assert ret_image(RetShape.I64, None) != ret_image(
        RetShape.INT1, (Member(0, 64, ArgClass.INTEGER),)
    )


def test_nested_records_pointer_arrays_and_bitfields_are_flattened() -> None:
    inner = _record(
        "in", [_field("x", "int", 0), _field("y", "unsigned int", 32)], 64, 32
    )
    outer = _record(
        "out",
        [
            _field("a", "struct in", 0),
            _field("p", "double *[1]", 64),
            _field("b", "unsigned int", 128, bits=3),
            _field("c", "unsigned int", 131, bits=5),
        ],
        192,
        64,
    )
    got = _classify(outer, inner)
    assert got is not None and got.shape is RetShape.MEMORY
    assert got.members == (
        Member(0, 32, ArgClass.INTEGER),
        Member(32, 32, ArgClass.INTEGER),
        Member(64, 64, ArgClass.INTEGER),
        Member(128, 3, ArgClass.INTEGER),
        Member(131, 5, ArgClass.INTEGER),
    )


@pytest.mark.parametrize(
    ("fields", "size", "align", "kw"),
    [
        ([("a", "float", 0), ("b", "float", 32)], 64, 32, {}),  # two floats share xmm0
        ([("a", "long double", 0)], 128, 128, {}),  # X87
        ([("a", "int", 0), ("b", "double", 64)], 128, 64, {}),  # rax:xmm0
        ([("a", "char[3]", 0)], 24, 8, {}),  # fundamental array: no element fact
        ([("a", "char", 0), ("b", "long", 8)], 72, 8, {}),  # packed
        ([("a", "long", 0)], 64, 64, {"is_union": True}),
        ([("a", "struct missing", 0)], 64, 64, {}),
        ([("a", "long", 0)], 256, 256, {}),  # over-aligned
    ],
)
def test_outside_the_domain_is_none(
    fields: list[tuple[str, str, int]], size: int, align: int, kw: dict[str, object]
) -> None:
    rec = _record("r", [_field(*f) for f in fields], size, align, **kw)
    assert _classify(rec) is None


@pytest.mark.parametrize(
    ("trivial", "proved", "classified"),
    [
        (True, True, True),
        # clang/gcc's trait is also true with every copy/move ctor deleted
        # (returned in memory under Itanium), so it does not prove triviality.
        (True, False, False),
        (False, True, False),
        (False, False, False),
        (None, True, False),
        (None, False, False),
    ],
)
def test_cxx_records_need_proved_triviality(
    trivial: bool | None, proved: bool, classified: bool
) -> None:
    rec = _record("r", [_field("a", "long", 0)], 64, 64, is_trivially_copyable=trivial)
    assert (_classify(rec, c=False, proved=proved) is not None) is classified
    assert _classify(rec, c=True, proved=proved) is not None
