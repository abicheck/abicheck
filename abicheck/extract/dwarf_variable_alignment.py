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

"""A DWARF variable's declared alignment, including the default one.

DWARF 5 (§2.24) gives a declaration ``DW_AT_alignment`` only when its
alignment is not the default for its type: ``int v __attribute__((aligned(64)))``
carries ``DW_AT_alignment 64`` and ``int v`` carries nothing. So in a DWARF 5
unit an absent attribute *states* the default -- the type's own alignment --
and is not unknown. Reading it as unknown made a raised alignment invisible
(``int v`` -> ``int v __attribute__((aligned(64)))`` compared ``None`` with
``512`` and reported nothing), although consumers that copy-relocate ``v``
place it at the old alignment while the library may now assume 64.

The default is recorded only where DWARF states it unambiguously:

* a typedef or qualifier on the way carries its own ``DW_AT_alignment``
  (``typedef int al16 __attribute__((aligned(16)))``): that is the type's
  alignment;
* a base type, an enum over one, an ``_Atomic`` scalar or a vector
  (``dwarf_abi_fact``), whose alignment is its size -- but only up to the
  unit's address size: the i386 ABI aligns an 8-byte ``double``/``long long``
  to 4 while compilers place a global at 8, so beyond the address size the
  declared default is not a single number;
* an array, whose alignment is its element's.

A record without ``DW_AT_alignment`` has a natural alignment DWARF does not
state, and a DWARF 4 unit never states an alignment at all: both stay
``None`` (unknown), as before.

The default is *declared* (``Fact`` PRESENT) only on a standalone definition,
one DIE that both compilers annotate. GCC splits ``extern int v;`` (the
header) and ``int v __attribute__((aligned(64))) = 1;`` (the unit) into a
declaration DIE and a definition DIE linked by ``DW_AT_specification``, and
writes a specifier spelled only on the definition nowhere (gcc 13: neither
DIE carries ``DW_AT_alignment``; a specifier on the declaration stays on the
declaration). A definition completing a declaration, and a declaration-only
DIE, therefore record the default as a lower bound (PARTIAL):
``VAR_ALIGNMENT_CHANGED`` still sees a specifier added to the header, while
the address-placement veto never trusts the value
(``compare/declared_alignment``).
"""

from __future__ import annotations

from typing import Any

from ..dwarf_utils import attr_int, resolve_type_die
from ..model.fact import Fact
from .dwarf_abi_fact import dwarf_fundamental, dwarf_type_abi_fact

__all__ = [
    "dwarf_variable_alignment_bits",
    "dwarf_variable_alignment_fact",
    "dwarf_variable_layout_facts",
]

_THROUGH = frozenset(
    {
        "DW_TAG_typedef",
        "DW_TAG_const_type",
        "DW_TAG_volatile_type",
        "DW_TAG_restrict_type",
    }
)
_MAX_DEPTH = 16

_LOWER_BOUND = (
    "the type's default alignment, read from a declaration or a definition "
    "completing one: GCC writes a specifier spelled only on the definition "
    "nowhere, so this is a lower bound"
)


def _unit_header(cu: Any, key: str) -> int:
    try:
        return int(cu.header[key])
    except (AttributeError, KeyError, TypeError, ValueError):
        return 0


def _type_default_bytes(die: Any, cu: Any, address_size: int, depth: int) -> int | None:
    if die is None or depth > _MAX_DEPTH:
        return None
    own = attr_int(die, "DW_AT_alignment")
    if own > 0:
        return own
    if die.tag in _THROUGH:
        return _type_default_bytes(
            resolve_type_die(die, cu), cu, address_size, depth + 1
        )
    if die.tag == "DW_TAG_array_type" and "DW_AT_GNU_vector" not in die.attributes:
        return _type_default_bytes(
            resolve_type_die(die, cu), cu, address_size, depth + 1
        )
    fundamental = dwarf_fundamental(die, cu)
    if fundamental is None:
        return None
    if not fundamental.lanes and fundamental.align > address_size:
        return None
    return fundamental.align


def dwarf_variable_alignment_bits(die: Any, cu: Any) -> int | None:
    """The declared alignment, in bits, of the variable *die* declares.

    Its own ``DW_AT_alignment`` when present; in a DWARF 5 unit otherwise its
    type's default alignment where DWARF states it (module docstring);
    ``None`` when unknown.
    """
    own = attr_int(die, "DW_AT_alignment")
    if own > 0:
        return own * 8
    if _unit_header(cu, "version") < 5:
        return None
    address_size = _unit_header(cu, "address_size")
    if address_size <= 0:
        return None
    default = _type_default_bytes(resolve_type_die(die, cu), cu, address_size, 0)
    return default * 8 if default else None


def dwarf_variable_alignment_fact(die: Any, cu: Any) -> Fact[int | None]:
    """:func:`dwarf_variable_alignment_bits` as a fact: an explicit
    ``DW_AT_alignment``, or a default on a standalone definition, is PRESENT;
    a default read from a declaration or a definition completing one is
    PARTIAL (module docstring); unknown is PRESENT ``None``, as before."""
    bits = dwarf_variable_alignment_bits(die, cu)
    if bits is None or attr_int(die, "DW_AT_alignment") > 0:
        return Fact.present(bits)
    attrs = die.attributes
    if "DW_AT_specification" in attrs or "DW_AT_declaration" in attrs:
        return Fact.partial(bits, _LOWER_BOUND, producer="dwarf")
    return Fact.present(bits)


def dwarf_variable_layout_facts(die: Any, cu: Any) -> dict[str, Any]:
    """The ``Variable`` layout fields a ``DW_TAG_variable`` states: its type's
    ABI fact (``dwarf_abi_fact``) and its declared alignment (above)."""
    return {
        "type_abi_fact": dwarf_type_abi_fact(die, cu),
        "alignment_bits_fact": dwarf_variable_alignment_fact(die, cu),
    }
