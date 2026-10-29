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

"""Default-lane unit tests for ``extract/dwarf_abi_fact.py``.

DIEs are small fakes with the surface the reader uses: ``.tag``,
``.attributes`` (``name -> object with .value and .form``) and
``.iter_children()``; the compile unit maps a reference offset to its DIE.
The expected facts come from the DWARF standard's ``DW_ATE`` and tag values
written here as constants, not from the module's own tables.
"""

from __future__ import annotations

from typing import Any

import pytest

from abicheck.extract.dwarf_abi_fact import _fact as _resolve_fact, dwarf_type_abi_fact
from abicheck.model.abi_fundamental import AbiFundamental, FundamentalEncoding
from abicheck.model.fact import Fact


def dwarf_abi_fundamental(type_die: Any, cu: Any) -> Any:
    """The ABI fact of the DIE a slot's ``DW_AT_type`` names."""
    return _resolve_fact(type_die, cu, 0)


_S = FundamentalEncoding.SIGNED
_U = FundamentalEncoding.UNSIGNED
_B = FundamentalEncoding.BOOLEAN
_F = FundamentalEncoding.FLOAT

# DW_ATE values (DWARF 5, section 7.8).
_ATE_BOOLEAN = 0x02
_ATE_COMPLEX_FLOAT = 0x03
_ATE_FLOAT = 0x04
_ATE_SIGNED = 0x05
_ATE_SIGNED_CHAR = 0x06
_ATE_UNSIGNED = 0x07
_ATE_UNSIGNED_CHAR = 0x08
_ATE_IMAGINARY_FLOAT = 0x09
_ATE_SIGNED_FIXED = 0x0D
_ATE_UNSIGNED_FIXED = 0x0E
_ATE_DECIMAL_FLOAT = 0x0F
_ATE_UTF = 0x10

#: Hand-written LP64 x86-64 oracle: (name, DW_ATE, bytes, encoding).
_LP64_BASE_TYPES: tuple[tuple[str, int, int, FundamentalEncoding], ...] = (
    ("signed char", _ATE_SIGNED_CHAR, 1, _S),
    ("unsigned char", _ATE_UNSIGNED_CHAR, 1, _U),
    ("short", _ATE_SIGNED, 2, _S),
    ("unsigned short", _ATE_UNSIGNED, 2, _U),
    ("int", _ATE_SIGNED, 4, _S),
    ("unsigned int", _ATE_UNSIGNED, 4, _U),
    ("long", _ATE_SIGNED, 8, _S),
    ("unsigned long", _ATE_UNSIGNED, 8, _U),
    ("__int128", _ATE_SIGNED, 16, _S),
    ("unsigned __int128", _ATE_UNSIGNED, 16, _U),
    ("_Bool", _ATE_BOOLEAN, 1, _B),
    ("_Float16", _ATE_FLOAT, 2, _F),
    ("float", _ATE_FLOAT, 4, _F),
    ("double", _ATE_FLOAT, 8, _F),
    ("long double", _ATE_FLOAT, 16, _F),
    ("char16_t", _ATE_UTF, 2, _U),
    ("char32_t", _ATE_UTF, 4, _U),
)


class _Attr:
    def __init__(self, value: Any, form: str = "DW_FORM_data1") -> None:
        self.value = value
        self.form = form


class _Die:
    def __init__(
        self,
        tag: str,
        attrs: dict[str, Any] | None = None,
        children: tuple[_Die, ...] = (),
    ) -> None:
        self.tag = tag
        self.attributes = {
            name: value if isinstance(value, _Attr) else _Attr(value)
            for name, value in (attrs or {}).items()
        }
        self._children = children

    def iter_children(self) -> Any:
        return iter(self._children)


class _Cu:
    """A compile unit: a map from reference offset to DIE."""

    def __init__(self, dies: dict[int, _Die]) -> None:
        self.cu_offset = 0
        self._dies = dies

    def get_DIE_from_refaddr(self, offset: int) -> _Die | None:
        return self._dies.get(offset)


class _RaisingCu(_Cu):
    def get_DIE_from_refaddr(self, offset: int) -> _Die | None:
        raise KeyError(offset)


def _ref(offset: int) -> _Attr:
    return _Attr(offset, form="DW_FORM_ref4")


def _base(name_encoding: int, size: int) -> _Die:
    return _Die(
        "DW_TAG_base_type",
        {"DW_AT_encoding": name_encoding, "DW_AT_byte_size": size},
    )


def _wrap(tag: str, target: int) -> _Die:
    return _Die(tag, {"DW_AT_type": _ref(target)})


def _subrange(
    count: int | None = None, upper: int | None = None, lower: int | None = None
) -> _Die:
    attrs: dict[str, Any] = {}
    if count is not None:
        attrs["DW_AT_count"] = count
    if upper is not None:
        attrs["DW_AT_upper_bound"] = upper
    if lower is not None:
        attrs["DW_AT_lower_bound"] = lower
    return _Die("DW_TAG_subrange_type", attrs)


def _vector(element: int, *subranges: _Die, byte_size: int | None = None) -> _Die:
    attrs: dict[str, Any] = {"DW_AT_GNU_vector": True, "DW_AT_type": _ref(element)}
    if byte_size is not None:
        attrs["DW_AT_byte_size"] = byte_size
    return _Die("DW_TAG_array_type", attrs, children=subranges)


def _fact(encoding: FundamentalEncoding, size: int, lanes: int = 0) -> AbiFundamental:
    """The fact a scalar (or vector, aligned to its own size) must carry."""
    return AbiFundamental(encoding, size, size, lanes)


def _cu_with_base_types() -> tuple[_Cu, dict[str, int]]:
    """One DIE per LP64 base type, keyed by name; returns the CU and offsets."""
    dies: dict[int, _Die] = {}
    offsets: dict[str, int] = {}
    for index, (name, ate, size, _enc) in enumerate(_LP64_BASE_TYPES):
        offset = 0x100 + index * 0x10
        dies[offset] = _base(ate, size)
        offsets[name] = offset
    return _Cu(dies), offsets


# ---------------------------------------------------------------------------
# DW_TAG_base_type: encoding class and size
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "ate", "size", "encoding"),
    _LP64_BASE_TYPES,
    ids=[row[0] for row in _LP64_BASE_TYPES],
)
def test_base_type_carries_its_encoding_class_and_natural_alignment(
    name: str, ate: int, size: int, encoding: FundamentalEncoding
) -> None:
    cu = _Cu({})
    assert dwarf_abi_fundamental(_base(ate, size), cu) == _fact(encoding, size)


@pytest.mark.parametrize(
    "ate",
    [
        _ATE_COMPLEX_FLOAT,
        _ATE_IMAGINARY_FLOAT,
        _ATE_DECIMAL_FLOAT,
        _ATE_SIGNED_FIXED,
        _ATE_UNSIGNED_FIXED,
        0x00,
    ],
    ids=[
        "complex",
        "imaginary",
        "decimal",
        "signed-fixed",
        "unsigned-fixed",
        "unknown-zero",
    ],
)
def test_base_type_encodings_outside_the_fundamental_set_have_no_fact(ate: int) -> None:
    assert dwarf_abi_fundamental(_base(ate, 8), _Cu({})) is None


@pytest.mark.parametrize(
    "attrs",
    [
        {"DW_AT_byte_size": 4},
        {"DW_AT_encoding": _ATE_SIGNED},
        {"DW_AT_encoding": _ATE_SIGNED, "DW_AT_byte_size": 0},
        {"DW_AT_encoding": _ATE_SIGNED, "DW_AT_byte_size": -4},
    ],
    ids=["no-encoding", "no-size", "zero-size", "negative-size"],
)
def test_base_type_missing_or_non_positive_attributes_have_no_fact(
    attrs: dict[str, Any],
) -> None:
    assert dwarf_abi_fundamental(_Die("DW_TAG_base_type", attrs), _Cu({})) is None


def test_base_type_with_a_string_encoding_value_has_no_fact() -> None:
    """attr_int reads a non-integer value as 0: no encoding, so no fact."""
    die = _Die("DW_TAG_base_type", {"DW_AT_encoding": "signed", "DW_AT_byte_size": 4})
    assert dwarf_abi_fundamental(die, _Cu({})) is None


def test_plain_char_signedness_is_the_encoding_the_compiler_emitted() -> None:
    """DWARF states plain char's signedness through its own DW_ATE; the fact
    follows the emitted encoding, not any name."""
    signed = _base(_ATE_SIGNED_CHAR, 1)
    unsigned = _base(_ATE_UNSIGNED_CHAR, 1)
    assert dwarf_abi_fundamental(signed, _Cu({})) == _fact(_S, 1)
    assert dwarf_abi_fundamental(unsigned, _Cu({})) == _fact(_U, 1)


# ---------------------------------------------------------------------------
# Transparent wrappers and the depth bound
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tag",
    [
        "DW_TAG_typedef",
        "DW_TAG_const_type",
        "DW_TAG_volatile_type",
        "DW_TAG_restrict_type",
    ],
)
def test_typedef_and_qualifiers_are_transparent(tag: str) -> None:
    cu = _Cu({0x200: _base(_ATE_UNSIGNED, 8)})
    assert dwarf_abi_fundamental(_wrap(tag, 0x200), cu) == _fact(_U, 8)


def test_typedef_chain_and_qualifier_stack_resolve_to_the_base() -> None:
    cu = _Cu(
        {
            0x200: _wrap("DW_TAG_const_type", 0x210),
            0x210: _wrap("DW_TAG_typedef", 0x220),
            0x220: _wrap("DW_TAG_volatile_type", 0x230),
            0x230: _base(_ATE_SIGNED, 4),
        }
    )
    assert dwarf_abi_fundamental(cu._dies[0x200], cu) == _fact(_S, 4)


def test_wrapper_chain_at_the_depth_bound_still_resolves() -> None:
    """Sixteen wrappers in front of the base: the base is read at depth 16,
    which the bound admits."""
    dies = {
        0x1000 + i * 0x10: _wrap("DW_TAG_typedef", 0x1000 + (i + 1) * 0x10)
        for i in range(16)
    }
    dies[0x1000 + 16 * 0x10] = _base(_ATE_SIGNED, 2)
    cu = _Cu(dies)
    assert dwarf_abi_fundamental(dies[0x1000], cu) == _fact(_S, 2)


def test_wrapper_chain_past_the_depth_bound_has_no_fact() -> None:
    dies = {
        0x1000 + i * 0x10: _wrap("DW_TAG_typedef", 0x1000 + (i + 1) * 0x10)
        for i in range(17)
    }
    dies[0x1000 + 17 * 0x10] = _base(_ATE_SIGNED, 2)
    cu = _Cu(dies)
    assert dwarf_abi_fundamental(dies[0x1000], cu) is None


def test_typedef_cycle_is_cut_by_the_depth_bound() -> None:
    cu = _Cu({0x300: _wrap("DW_TAG_typedef", 0x300)})
    assert dwarf_abi_fundamental(cu._dies[0x300], cu) is None


def test_typedef_to_an_absent_die_has_no_fact() -> None:
    cu = _Cu({0x400: _wrap("DW_TAG_typedef", 0x999)})
    assert dwarf_abi_fundamental(cu._dies[0x400], cu) is None


def test_typedef_whose_reference_cannot_be_resolved_has_no_fact() -> None:
    """A reference lookup that raises is 'no fact', not a crash."""
    die = _wrap("DW_TAG_typedef", 0x500)
    assert dwarf_abi_fundamental(die, _RaisingCu({})) is None


def test_typedef_without_a_type_attribute_has_no_fact() -> None:
    assert dwarf_abi_fundamental(_Die("DW_TAG_typedef"), _Cu({})) is None


@pytest.mark.parametrize(
    "tag",
    [
        "DW_TAG_pointer_type",
        "DW_TAG_structure_type",
        "DW_TAG_class_type",
        "DW_TAG_subroutine_type",
        "DW_TAG_union_type",
    ],
)
def test_non_fundamental_shapes_have_no_fact(tag: str) -> None:
    cu = _Cu({0x600: _base(_ATE_SIGNED, 4)})
    assert (
        dwarf_abi_fundamental(
            _Die(tag, {"DW_AT_type": _ref(0x600), "DW_AT_byte_size": 8}), cu
        )
        is None
    )


def test_absent_die_has_no_fact() -> None:
    assert dwarf_abi_fundamental(None, _Cu({})) is None


# ---------------------------------------------------------------------------
# DW_TAG_enumeration_type: the underlying integer, when sizes agree
# ---------------------------------------------------------------------------


def test_enumeration_takes_its_underlying_unsigned_integer() -> None:
    cu = _Cu({0x700: _base(_ATE_UNSIGNED, 4)})
    enum = _Die(
        "DW_TAG_enumeration_type", {"DW_AT_type": _ref(0x700), "DW_AT_byte_size": 4}
    )
    assert dwarf_abi_fundamental(enum, cu) == _fact(_U, 4)


def test_enumeration_whose_size_disagrees_with_its_underlying_integer_has_no_fact() -> (
    None
):
    cu = _Cu({0x700: _base(_ATE_SIGNED, 4)})
    enum = _Die(
        "DW_TAG_enumeration_type", {"DW_AT_type": _ref(0x700), "DW_AT_byte_size": 8}
    )
    assert dwarf_abi_fundamental(enum, cu) is None


def test_enumeration_without_a_byte_size_has_no_fact() -> None:
    cu = _Cu({0x700: _base(_ATE_SIGNED, 4)})
    enum = _Die("DW_TAG_enumeration_type", {"DW_AT_type": _ref(0x700)})
    assert dwarf_abi_fundamental(enum, cu) is None


def test_enumeration_without_an_underlying_type_has_no_fact() -> None:
    enum = _Die("DW_TAG_enumeration_type", {"DW_AT_byte_size": 4})
    assert dwarf_abi_fundamental(enum, _Cu({})) is None


def test_enumeration_over_a_non_scalar_underlying_type_has_no_fact() -> None:
    cu = _Cu({0x700: _Die("DW_TAG_structure_type", {"DW_AT_byte_size": 4})})
    enum = _Die(
        "DW_TAG_enumeration_type", {"DW_AT_type": _ref(0x700), "DW_AT_byte_size": 4}
    )
    assert dwarf_abi_fundamental(enum, cu) is None


def test_enumeration_over_a_vector_has_no_fact() -> None:
    cu = _Cu({0x700: _vector(0x710, _subrange(count=4)), 0x710: _base(_ATE_FLOAT, 4)})
    enum = _Die(
        "DW_TAG_enumeration_type", {"DW_AT_type": _ref(0x700), "DW_AT_byte_size": 16}
    )
    assert dwarf_abi_fundamental(enum, cu) is None


# ---------------------------------------------------------------------------
# DW_TAG_atomic_type: natural alignment for a scalar under 8 bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("inner_ate", "inner_size", "encoding"),
    [
        (_ATE_SIGNED, 4, _S),
        (_ATE_UNSIGNED_CHAR, 1, _U),
        (_ATE_FLOAT, 4, _F),
        (_ATE_BOOLEAN, 1, _B),
    ],
    ids=["int", "uchar", "float", "bool"],
)
def test_atomic_scalar_under_eight_bytes_is_naturally_aligned(
    inner_ate: int, inner_size: int, encoding: FundamentalEncoding
) -> None:
    cu = _Cu({0x800: _base(inner_ate, inner_size)})
    atomic = _Die("DW_TAG_atomic_type", {"DW_AT_type": _ref(0x800)})
    assert dwarf_abi_fundamental(atomic, cu) == _fact(encoding, inner_size)


@pytest.mark.parametrize(
    "inner",
    [
        pytest.param(_base(_ATE_FLOAT, 8), id="double"),
        pytest.param(_base(_ATE_FLOAT, 16), id="long-double"),
        pytest.param(_base(_ATE_SIGNED, 8), id="long"),
    ],
)
def test_atomic_eight_bytes_or_wider_is_not_read_as_natural(inner: _Die) -> None:
    """An 8-byte atomic can be over-aligned on some ABIs and DWARF does not
    state that here, so it stays no fact."""
    cu = _Cu({0x800: inner})
    atomic = _Die("DW_TAG_atomic_type", {"DW_AT_type": _ref(0x800)})
    assert dwarf_abi_fundamental(atomic, cu) is None


def test_atomic_of_a_vector_or_a_struct_has_no_fact() -> None:
    cu = _Cu(
        {
            0x800: _vector(0x810, _subrange(count=4)),
            0x810: _base(_ATE_FLOAT, 4),
            0x820: _Die("DW_TAG_structure_type", {"DW_AT_byte_size": 4}),
        }
    )
    assert (
        dwarf_abi_fundamental(
            _Die("DW_TAG_atomic_type", {"DW_AT_type": _ref(0x800)}), cu
        )
        is None
    )
    assert (
        dwarf_abi_fundamental(
            _Die("DW_TAG_atomic_type", {"DW_AT_type": _ref(0x820)}), cu
        )
        is None
    )


def test_atomic_without_a_type_has_no_fact() -> None:
    assert dwarf_abi_fundamental(_Die("DW_TAG_atomic_type"), _Cu({})) is None


# ---------------------------------------------------------------------------
# DW_TAG_array_type with DW_AT_GNU_vector: lanes from the subrange
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("element_ate", "element_size", "subrange", "lanes", "encoding"),
    [
        (_ATE_FLOAT, 4, _subrange(count=4), 4, _F),
        (_ATE_SIGNED, 4, _subrange(count=2), 2, _S),
        (_ATE_SIGNED, 16, _subrange(count=2), 2, _S),
        (_ATE_UNSIGNED_CHAR, 1, _subrange(upper=7, lower=0), 8, _U),
        (_ATE_UNSIGNED_CHAR, 1, _subrange(upper=4, lower=1), 4, _U),
        (_ATE_FLOAT, 8, _subrange(upper=3), 4, _F),
    ],
    ids=[
        "float-count4",
        "int-count2",
        "int128-count2",
        "uchar-upper7",
        "uchar-lower1",
        "double-upper3",
    ],
)
def test_vector_size_is_element_size_times_lanes(
    element_ate: int,
    element_size: int,
    subrange: _Die,
    lanes: int,
    encoding: FundamentalEncoding,
) -> None:
    cu = _Cu({0x900: _vector(0x910, subrange), 0x910: _base(element_ate, element_size)})
    total = element_size * lanes
    assert dwarf_abi_fundamental(cu._dies[0x900], cu) == AbiFundamental(
        encoding, total, total, lanes
    )


def test_declared_byte_size_of_a_vector_wins_over_its_subrange() -> None:
    """A vector DIE that states its size is read at that size (32 bytes of
    doubles: four lanes), not from the subrange count."""
    cu = _Cu(
        {
            0x900: _vector(0x910, _subrange(count=2), byte_size=32),
            0x910: _base(_ATE_FLOAT, 8),
        }
    )
    assert dwarf_abi_fundamental(cu._dies[0x900], cu) == AbiFundamental(_F, 32, 32, 4)


@pytest.mark.parametrize(
    "die",
    [
        pytest.param(
            _Die(
                "DW_TAG_array_type",
                {"DW_AT_type": _ref(0x910)},
                children=(_subrange(count=4),),
            ),
            id="not-a-vector",
        ),
        pytest.param(_vector(0x910), id="no-subrange-no-size"),
        pytest.param(
            _vector(0x910, _Die("DW_TAG_subrange_type")), id="subrange-without-bounds"
        ),
        pytest.param(_vector(0x910, _subrange(count=0)), id="zero-count"),
        pytest.param(
            _vector(0x910, _subrange(count=4), byte_size=6),
            id="declared-size-not-whole-elements",
        ),
    ],
)
def test_array_that_is_not_a_usable_vector_has_no_fact(die: _Die) -> None:
    """Without a vector flag, or without a size or bounded count, or with a
    size that is not whole elements, the array has no fact."""
    cu = _Cu({0x910: _base(_ATE_FLOAT, 4)})
    assert dwarf_abi_fundamental(die, cu) is None


def test_declared_zero_size_is_absent_so_the_subrange_count_sizes_the_vector() -> None:
    """``DW_AT_byte_size`` of 0 is read as not stated: the count decides."""
    cu = _Cu({0x910: _base(_ATE_FLOAT, 4)})
    die = _vector(0x910, _subrange(count=4), byte_size=0)
    assert dwarf_abi_fundamental(die, cu) == AbiFundamental(_F, 16, 16, 4)


def test_odd_lane_count_is_a_valid_three_lane_vector() -> None:
    """A three-lane vector of floats is 12 bytes: whole elements, so a fact."""
    cu = _Cu({0x910: _base(_ATE_FLOAT, 4)})
    assert dwarf_abi_fundamental(
        _vector(0x910, _subrange(count=3)), cu
    ) == AbiFundamental(_F, 12, 12, 3)


def test_vector_of_a_vector_or_a_struct_element_has_no_fact() -> None:
    cu = _Cu(
        {
            0x910: _vector(0x920, _subrange(count=4)),
            0x920: _base(_ATE_FLOAT, 4),
            0x930: _Die("DW_TAG_structure_type", {"DW_AT_byte_size": 4}),
        }
    )
    assert dwarf_abi_fundamental(_vector(0x910, _subrange(count=2)), cu) is None
    assert dwarf_abi_fundamental(_vector(0x930, _subrange(count=2)), cu) is None


def test_vector_without_an_element_type_has_no_fact() -> None:
    die = _Die(
        "DW_TAG_array_type", {"DW_AT_GNU_vector": True}, children=(_subrange(count=4),)
    )
    assert dwarf_abi_fundamental(die, _Cu({})) is None


# ---------------------------------------------------------------------------
# dwarf_type_abi_fact: the Fact of a slot's declared type
# ---------------------------------------------------------------------------


def test_slot_fact_reads_the_type_its_dw_at_type_names() -> None:
    cu = _Cu({0xA00: _base(_ATE_SIGNED, 4)})
    variable = _Die("DW_TAG_variable", {"DW_AT_type": _ref(0xA00)})
    assert dwarf_type_abi_fact(variable, cu) == Fact.present(_fact(_S, 4))


def test_slot_fact_follows_a_typedef_to_its_fundamental() -> None:
    cu = _Cu({0xA00: _wrap("DW_TAG_typedef", 0xA10), 0xA10: _base(_ATE_UNSIGNED, 8)})
    parameter = _Die("DW_TAG_formal_parameter", {"DW_AT_type": _ref(0xA00)})
    assert dwarf_type_abi_fact(parameter, cu) == Fact.present(_fact(_U, 8))


def test_slot_without_a_type_reference_has_no_fact() -> None:
    assert dwarf_type_abi_fact(_Die("DW_TAG_variable"), _Cu({})) is None


def test_slot_of_a_struct_has_no_fact() -> None:
    cu = _Cu({0xA00: _Die("DW_TAG_structure_type", {"DW_AT_byte_size": 16})})
    variable = _Die("DW_TAG_variable", {"DW_AT_type": _ref(0xA00)})
    assert dwarf_type_abi_fact(variable, cu) is None


def test_slot_whose_type_reference_cannot_be_resolved_has_no_fact() -> None:
    variable = _Die("DW_TAG_variable", {"DW_AT_type": _ref(0xA00)})
    assert dwarf_type_abi_fact(variable, _RaisingCu({})) is None


# ---------------------------------------------------------------------------
# Whole-table check against the oracle, through a shared compile unit
# ---------------------------------------------------------------------------


def test_every_oracle_base_type_reads_its_encoding_through_a_slot() -> None:
    cu, offsets = _cu_with_base_types()
    for name, _ate, size, encoding in _LP64_BASE_TYPES:
        slot = _Die("DW_TAG_variable", {"DW_AT_type": _ref(offsets[name])})
        assert dwarf_type_abi_fact(slot, cu) == Fact.present(_fact(encoding, size)), (
            name
        )


def test_unspecified_type_die_has_no_fact_whatever_its_attributes() -> None:
    die = _Die(
        "DW_TAG_unspecified_type", {"DW_AT_byte_size": 4, "DW_AT_encoding": _ATE_SIGNED}
    )
    assert dwarf_abi_fundamental(die, _Cu({})) is None
