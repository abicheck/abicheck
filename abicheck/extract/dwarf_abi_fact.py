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

"""The ABI fact of a DWARF type DIE (schema v69, ``model/abi_fundamental.py``).

The compiler has already decided everything the fact records, and DWARF states
it directly: a ``DW_TAG_base_type``'s ``DW_AT_encoding`` is the encoding class
(``DW_ATE``) and its ``DW_AT_byte_size`` is the size. An enum passes its
underlying integer type, an ``_Atomic`` keeps its element's encoding, and a
``DW_AT_GNU_vector`` array is a vector whose lane count is its subrange count.
Typedefs and cv/restrict qualifiers are transparent.

Nothing here reads a type's name. A DIE that is not one of these shapes (a
struct, a pointer, an array that is not a vector, a complex or decimal float)
yields ``None``, which is "no fact": the comparison then keeps its name-based
verdict, as it did before schema v69.
"""

from __future__ import annotations

from typing import Any

from ..dwarf_utils import attr_int, resolve_type_die, vector_byte_size
from ..model.abi_fundamental import AbiFundamental, FundamentalEncoding
from ..model.fact import Fact

__all__ = ["dwarf_abi_fundamental", "dwarf_type_abi_fact"]

#: DW_ATE encodings a calling convention can distinguish, mapped to the
#: encoding class. ``DW_ATE_signed_char``/``unsigned_char`` carry the signedness
#: of a plain ``char`` on their target; ``DW_ATE_UTF`` (char8/16/32_t) is
#: unsigned. Complex, imaginary, decimal and fixed-point encodings are absent,
#: so they read as no fact.
_DW_ATE_ENCODING: dict[int, FundamentalEncoding] = {
    0x02: FundamentalEncoding.BOOLEAN,  # DW_ATE_boolean
    0x04: FundamentalEncoding.FLOAT,  # DW_ATE_float
    0x05: FundamentalEncoding.SIGNED,  # DW_ATE_signed
    0x06: FundamentalEncoding.SIGNED,  # DW_ATE_signed_char
    0x07: FundamentalEncoding.UNSIGNED,  # DW_ATE_unsigned
    0x08: FundamentalEncoding.UNSIGNED,  # DW_ATE_unsigned_char
    0x10: FundamentalEncoding.UNSIGNED,  # DW_ATE_UTF
}

#: Wrappers the fact sees through: they change no passing convention.
_TRANSPARENT_TAGS = frozenset(
    {
        "DW_TAG_typedef",
        "DW_TAG_const_type",
        "DW_TAG_volatile_type",
        "DW_TAG_restrict_type",
    }
)

#: A type chain deeper than this is not a fundamental this module reads.
_MAX_DEPTH = 16


def dwarf_abi_fundamental(type_die: Any, cu: Any) -> AbiFundamental | None:
    """The ABI fact of *type_die* (the DIE a slot's ``DW_AT_type`` names), or None."""
    return _fact(type_die, cu, 0)


def dwarf_type_abi_fact(die: Any, cu: Any) -> Fact[AbiFundamental] | None:
    """The ABI fact of the slot *die* declares (its ``DW_AT_type``), as the
    ``Fact`` a declaration stores: ``None`` when DWARF states no fundamental."""
    fact = _fact(resolve_type_die(die, cu), cu, 0)
    return None if fact is None else Fact.present(fact)


def _fact(die: Any, cu: Any, depth: int) -> AbiFundamental | None:
    if die is None or depth > _MAX_DEPTH:
        return None
    tag = die.tag
    if tag in _TRANSPARENT_TAGS:
        return _fact(resolve_type_die(die, cu), cu, depth + 1)
    if tag == "DW_TAG_base_type":
        return _base_fact(die)
    if tag == "DW_TAG_enumeration_type":
        return _enum_fact(die, cu, depth)
    if tag == "DW_TAG_atomic_type":
        return _atomic_fact(die, cu, depth)
    if tag == "DW_TAG_array_type" and "DW_AT_GNU_vector" in die.attributes:
        return _vector_fact(die, cu, depth)
    return None


def _base_fact(die: Any) -> AbiFundamental | None:
    encoding = _DW_ATE_ENCODING.get(attr_int(die, "DW_AT_encoding"))
    size = attr_int(die, "DW_AT_byte_size")
    if encoding is None or size <= 0:
        return None
    # A scalar's alignment is its size on every ABI this module records; the
    # one exception (a 4-byte-aligned 8-byte integer on i386) affects only a
    # field's layout, which the layout comparison reads separately.
    return AbiFundamental(encoding, size, size)


def _enum_fact(die: Any, cu: Any, depth: int) -> AbiFundamental | None:
    # An enumeration's DW_AT_type is its underlying integer type.
    under = _fact(resolve_type_die(die, cu), cu, depth + 1)
    if under is None or under.lanes or under.size != attr_int(die, "DW_AT_byte_size"):
        return None
    return under


def _atomic_fact(die: Any, cu: Any, depth: int) -> AbiFundamental | None:
    inner = _fact(resolve_type_die(die, cu), cu, depth + 1)
    # An _Atomic scalar is naturally aligned, so its alignment is its size. A
    # wider atomic can be over-aligned on some ABIs (an 8-byte _Atomic on i386),
    # which DWARF does not state here: that stays no fact.
    if inner is None or inner.lanes or inner.size >= 8:
        return None
    return AbiFundamental(inner.encoding, inner.size, inner.size)


def _vector_fact(die: Any, cu: Any, depth: int) -> AbiFundamental | None:
    element = _fact(resolve_type_die(die, cu), cu, depth + 1)
    if element is None or element.lanes:
        return None
    size = vector_byte_size(die, element.size)
    if not size or size % element.size:
        return None
    # A GCC/Clang vector is aligned to its own size (16 bytes for SSE, 32 for
    # AVX), so its alignment is recorded as its size.
    return AbiFundamental(element.encoding, size, size, size // element.size)
