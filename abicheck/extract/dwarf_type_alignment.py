# Copyright 2026 Nikolay Petrov
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

"""Alignment walks over DWARF type DIEs: is a by-value aggregate misaligned?

Moved out of ``dwarf_advanced`` (pure move of the functions, plus the iterative,
cycle-safe rewrite of ``_type_unaligned_at`` and the bounded-free qualifier
unwrap). These walks feed the SysV return-classification of by-value aggregates.
"""

from __future__ import annotations

import logging
from typing import Any

from ..dwarf_utils import (
    attr_int as _attr_int,
    decode_member_location as _shared_decode_member_location,
    resolve_type_die as _resolve_type_die,
)

log = logging.getLogger("abicheck.dwarf_advanced")

_NATURAL_ALIGN: dict[int, int] = {1: 1, 2: 2, 4: 4, 8: 8, 16: 16}


def _unwrap_qualifiers(type_die: Any, CU: Any, cache: Any = None) -> Any:
    """Unwrap transparent qualifier/typedef layers.

    Follows the chain until it reaches a non-qualifier DIE. A chain can be as
    long as the program's typedef graph, so there is no fixed step cap; a
    repeated DIE offset is a reference cycle, reached no non-qualifier, and
    the walk stops at the DIE where it started to repeat.
    """
    key = getattr(type_die, "offset", None)
    if cache is not None and key is not None and key in cache.unwrap:
        return cache.unwrap[key]

    cur = type_die
    seen: set[int] = set()
    qualifiers = (
        "DW_TAG_typedef",
        "DW_TAG_const_type",
        "DW_TAG_volatile_type",
        "DW_TAG_restrict_type",
    )
    while getattr(cur, "tag", "") in qualifiers:
        cur_off = getattr(cur, "offset", None)
        if cur_off is not None:
            if cur_off in seen:
                log.debug("_unwrap_qualifiers: reference cycle at offset %s", cur_off)
                break
            seen.add(cur_off)
        nxt = _resolve_type_die(cur, CU)
        if nxt is None:
            break
        cur = nxt

    if cache is not None and key is not None:
        cache.unwrap[key] = cur
    return cur


_SCALAR_LEAF_TAGS: tuple[str, ...] = (
    "DW_TAG_base_type",
    "DW_TAG_pointer_type",
    "DW_TAG_reference_type",
    "DW_TAG_rvalue_reference_type",
    "DW_TAG_enumeration_type",
    "DW_TAG_ptr_to_member_type",
)

_AGGREGATE_TAGS: tuple[str, ...] = (
    "DW_TAG_structure_type",
    "DW_TAG_class_type",
    "DW_TAG_union_type",
)


def _scalar_leaf_align(t: Any) -> int:
    """Natural alignment of an already-unwrapped scalar/enum/pointer type DIE."""
    if "DW_AT_alignment" in t.attributes:
        try:
            return int(t.attributes["DW_AT_alignment"].value)
        except (TypeError, ValueError):
            pass
    sz = _attr_int(t, "DW_AT_byte_size")
    return _NATURAL_ALIGN.get(min(sz, 16), 1) if sz > 0 else 1


def _type_unaligned_at(type_die: Any, CU: Any, base_offset: int, cache: Any) -> bool:
    """Whether any scalar leaf of *type_die* lands at a misaligned absolute offset.

    *base_offset* is the absolute offset at which this type starts within the
    outermost aggregate. Walks nested aggregates (carrying member offsets) and
    array members (an array shares its element's alignment, so the array's own
    offset determines element alignment). A scalar/enum/pointer leaf is
    misaligned when ``base_offset`` is not a multiple of its natural alignment.

    The walk is an explicit stack, so nesting depth is not bounded by the
    interpreter's recursion limit. A (type, offset) pair is visited at most
    once: revisiting it can only reach leaves already examined, so a reference
    cycle in the DIE graph (declaration and definition DIEs can refer back to
    each other) terminates without changing the answer.
    """
    stack: list[tuple[Any, int]] = [(type_die, base_offset)]
    seen: set[tuple[int, int]] = set()
    while stack:
        die, base = stack.pop()
        key = (getattr(die, "offset", id(die)), base)
        if key in seen:
            continue
        seen.add(key)
        t = _unwrap_qualifiers(die, CU, cache=cache)
        if t.tag in _SCALAR_LEAF_TAGS:
            if base % _scalar_leaf_align(t) != 0:
                return True
            continue
        if t.tag == "DW_TAG_array_type":
            elem = _resolve_type_die(t, CU)
            if elem is not None:
                stack.append((elem, base))
            continue
        if t.tag in _AGGREGATE_TAGS:
            for child in t.iter_children():
                if child.tag != "DW_TAG_member" or _attr_int(child, "DW_AT_bit_size"):
                    continue
                mt = _resolve_type_die(child, CU)
                if mt is None:
                    continue
                rel = _decode_member_location(child)  # None: misalignment not provable
                if rel is not None:
                    stack.append((mt, base + rel))
    return False


def _aggregate_has_unaligned_member(die: Any, CU: Any, cache: Any = None) -> bool:
    """Whether a by-value aggregate return type has an unaligned member (recursively).

    A struct/class/union with a leaf at a misaligned offset (e.g. a packed
    aggregate) is MEMORY-classified by the SysV AMD64 ABI regardless of size, so
    it is returned via a hidden sret pointer either way. Walks the full type tree
    — nested aggregates and array members included — accumulating absolute
    offsets, so e.g. ``packed R{char c; int a[1];}`` (``a[0]`` at offset 1) and
    ``packed Outer{char c; Inner{double d};}`` (``i.d`` at offset 1) are caught.
    """
    t0 = _resolve_type_die(die, CU)
    if t0 is None or t0.tag in (
        "DW_TAG_pointer_type",
        "DW_TAG_reference_type",
        "DW_TAG_rvalue_reference_type",
    ):
        return False
    t = _unwrap_qualifiers(t0, CU, cache=cache)
    if t.tag not in _AGGREGATE_TAGS:
        return False
    return _type_unaligned_at(t, CU, 0, cache)


def _decode_member_location(member_die: Any) -> int | None:
    """Decode DW_AT_data_member_location to a byte offset, or ``None`` if unevaluable.

    Delegates to the shared implementation in dwarf_utils.
    """
    if "DW_AT_data_member_location" not in member_die.attributes:
        return 0
    return _shared_decode_member_location(
        member_die.attributes["DW_AT_data_member_location"].value
    )
