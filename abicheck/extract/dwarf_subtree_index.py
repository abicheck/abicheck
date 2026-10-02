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

"""Skip DWARF subtrees without parsing them, when the producer gave no sibling links.

Why this exists
---------------
Every abicheck DWARF walker skips most of the tree: a ``DW_TAG_subprogram``'s
body (its parameters, local variables, lexical blocks, inlined calls and call
sites) holds nothing ABI-relevant, so the walkers ``continue`` past it. But
walking to the *next* sibling means knowing where this subtree ends, and
pyelftools' ``CompileUnit.iter_DIE_children`` only knows that cheaply when the
DIE carries ``DW_AT_sibling``. GCC emits that attribute; **clang (and so
icx/icpx) never does**. Without it pyelftools fully decodes every DIE of the
skipped subtree -- attributes, forms, translated values -- builds a Python
object for each, and caches all of them for the rest of the run.

Measured on a clang-built oneCCL ``libccl.so``, that was 64% of a whole dump
(43 s of pyelftools decoding 1.46M ``formal_parameter``, 310k
``inlined_subroutine`` and 114k ``call_site_parameter`` DIEs nothing reads)
and 3.2 GB of RSS, since the skipped DIEs stay cached.

What it does
------------
:func:`subtree_ends` makes one pass over a CU's raw ``.debug_info`` bytes,
decoding only each DIE's abbreviation code and stepping over its attribute
values by *form size* -- no values are interpreted and no objects are built --
and records, for every DIE that has children, the offset just past its null
terminator. :func:`install_subtree_index` then gives each compile unit an
``iter_DIE_children`` that uses that table in place of ``DW_AT_sibling``,
yielding exactly the DIEs the stock method yields, in the same order, and
setting the same ``_terminator`` on the parent.

It never guesses: a form this module cannot size, an abbreviation code the
table does not declare, or a scan that does not end exactly at the unit's end
makes :func:`subtree_ends` answer ``None``, and that unit keeps pyelftools'
own behaviour. A wrong table would silently drop or duplicate declarations;
a missing one only costs the old time.
"""

from __future__ import annotations

import logging
import types
from collections.abc import Iterator
from typing import Any

from ..model.execution_cache import reference_mode, request_key
from ..model.execution_cache_scoped import InstanceMemo

log = logging.getLogger(__name__)

__all__ = [
    "FORMAL_PARAMETER_TAGS",
    "drop_index",
    "install_subtree_index",
    "iter_children_tagged",
    "iter_formal_parameters",
    "open_indexed_dwarf_info",
    "subtree_ends",
]

# Step codes in an abbreviation's skip plan. A non-negative step is a fixed
# number of bytes; a negative one names a variable-length encoding.
_LEB = -1  # ULEB128/SLEB128 value (the skip is identical for both)
_CSTRING = -2  # NUL-terminated inline string
_BLOCK1 = -3  # 1-byte length, then that many bytes
_BLOCK2 = -4  # 2-byte length
_BLOCK4 = -5  # 4-byte length
_BLOCK_LEB = -6  # ULEB128 length (DW_FORM_block, DW_FORM_exprloc)
_INDIRECT = -7  # ULEB128 form code, then a value of that form

_FIXED_FORMS: dict[str, int] = {
    "DW_FORM_data1": 1,
    "DW_FORM_ref1": 1,
    "DW_FORM_flag": 1,
    "DW_FORM_strx1": 1,
    "DW_FORM_addrx1": 1,
    "DW_FORM_data2": 2,
    "DW_FORM_ref2": 2,
    "DW_FORM_strx2": 2,
    "DW_FORM_addrx2": 2,
    "DW_FORM_strx3": 3,
    "DW_FORM_addrx3": 3,
    "DW_FORM_data4": 4,
    "DW_FORM_ref4": 4,
    "DW_FORM_strx4": 4,
    "DW_FORM_addrx4": 4,
    "DW_FORM_ref_sup4": 4,
    "DW_FORM_data8": 8,
    "DW_FORM_ref8": 8,
    "DW_FORM_ref_sig8": 8,
    "DW_FORM_ref_sup8": 8,
    "DW_FORM_data16": 16,
    "DW_FORM_flag_present": 0,
    "DW_FORM_implicit_const": 0,
}

_OFFSET_FORMS = frozenset(
    {
        "DW_FORM_strp",
        "DW_FORM_sec_offset",
        "DW_FORM_line_strp",
        "DW_FORM_strp_sup",
        "DW_FORM_GNU_ref_alt",
        "DW_FORM_GNU_strp_alt",
    }
)

_VARIABLE_FORMS: dict[str, int] = {
    "DW_FORM_udata": _LEB,
    "DW_FORM_sdata": _LEB,
    "DW_FORM_ref_udata": _LEB,
    "DW_FORM_strx": _LEB,
    "DW_FORM_addrx": _LEB,
    "DW_FORM_loclistx": _LEB,
    "DW_FORM_rnglistx": _LEB,
    "DW_FORM_GNU_addr_index": _LEB,
    "DW_FORM_GNU_str_index": _LEB,
    "DW_FORM_string": _CSTRING,
    "DW_FORM_block1": _BLOCK1,
    "DW_FORM_block2": _BLOCK2,
    "DW_FORM_block4": _BLOCK4,
    "DW_FORM_block": _BLOCK_LEB,
    "DW_FORM_exprloc": _BLOCK_LEB,
    "DW_FORM_indirect": _INDIRECT,
}

#: DWARF form codes, for ``DW_FORM_indirect`` (the form is then in the data).
_FORM_BY_CODE: dict[int, str] = {
    0x01: "DW_FORM_addr",
    0x03: "DW_FORM_block2",
    0x04: "DW_FORM_block4",
    0x05: "DW_FORM_data2",
    0x06: "DW_FORM_data4",
    0x07: "DW_FORM_data8",
    0x08: "DW_FORM_string",
    0x09: "DW_FORM_block",
    0x0A: "DW_FORM_block1",
    0x0B: "DW_FORM_data1",
    0x0C: "DW_FORM_flag",
    0x0D: "DW_FORM_sdata",
    0x0E: "DW_FORM_strp",
    0x0F: "DW_FORM_udata",
    0x10: "DW_FORM_ref_addr",
    0x11: "DW_FORM_ref1",
    0x12: "DW_FORM_ref2",
    0x13: "DW_FORM_ref4",
    0x14: "DW_FORM_ref8",
    0x15: "DW_FORM_ref_udata",
    0x17: "DW_FORM_sec_offset",
    0x18: "DW_FORM_exprloc",
    0x19: "DW_FORM_flag_present",
    0x1A: "DW_FORM_strx",
    0x1B: "DW_FORM_addrx",
    0x1C: "DW_FORM_ref_sup4",
    0x1D: "DW_FORM_strp_sup",
    0x1E: "DW_FORM_data16",
    0x1F: "DW_FORM_line_strp",
    0x20: "DW_FORM_ref_sig8",
    0x22: "DW_FORM_loclistx",
    0x23: "DW_FORM_rnglistx",
    0x24: "DW_FORM_ref_sup8",
    0x25: "DW_FORM_strx1",
    0x26: "DW_FORM_strx2",
    0x27: "DW_FORM_strx3",
    0x28: "DW_FORM_strx4",
    0x29: "DW_FORM_addrx1",
    0x2A: "DW_FORM_addrx2",
    0x2B: "DW_FORM_addrx3",
    0x2C: "DW_FORM_addrx4",
}


class _Unsizable(Exception):
    """A form or code this module cannot step over; the unit keeps the stock path."""


def _form_step(form: str, address_size: int, offset_size: int, version: int) -> int:
    fixed = _FIXED_FORMS.get(form)
    if fixed is not None:
        return fixed
    if form == "DW_FORM_addr":
        return address_size
    if form in _OFFSET_FORMS:
        return offset_size
    if form == "DW_FORM_ref_addr":
        # DWARF 2 sized this as an address; DWARF 3+ as an offset.
        return address_size if version <= 2 else offset_size
    variable = _VARIABLE_FORMS.get(form)
    if variable is not None:
        return variable
    raise _Unsizable(form)


def _skip_plan(
    abbrev: Any, address_size: int, offset_size: int, version: int
) -> tuple[bool, tuple[int, ...]]:
    """``(has_children, steps)`` for one abbreviation, adjacent fixed steps merged."""
    steps: list[int] = []
    for spec in abbrev["attr_spec"]:
        step = _form_step(spec.form, address_size, offset_size, version)
        if step == 0:
            continue
        if step > 0 and steps and steps[-1] > 0:
            steps[-1] += step
        else:
            steps.append(step)
    return bool(abbrev.has_children()), tuple(steps)


def _skip_leb(data: bytes, pos: int) -> int:
    while data[pos] & 0x80:
        pos += 1
    return pos + 1


def _read_uleb(data: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _skip_value(
    data: bytes, pos: int, step: int, address_size: int, offset_size: int, version: int
) -> int:
    if step >= 0:
        return pos + step
    if step == _LEB:
        return _skip_leb(data, pos)
    if step == _CSTRING:
        end = data.index(b"\0", pos)
        return end + 1
    if step == _BLOCK1:
        return pos + 1 + data[pos]
    if step == _BLOCK2:
        return pos + 2 + int.from_bytes(data[pos : pos + 2], "little")
    if step == _BLOCK4:
        return pos + 4 + int.from_bytes(data[pos : pos + 4], "little")
    if step == _BLOCK_LEB:
        length, pos = _read_uleb(data, pos)
        return pos + length
    # _INDIRECT
    code, pos = _read_uleb(data, pos)
    form = _FORM_BY_CODE.get(code)
    if form is None or form == "DW_FORM_indirect":
        raise _Unsizable(f"indirect form 0x{code:x}")
    inner = _form_step(form, address_size, offset_size, version)
    return _skip_value(data, pos, inner, address_size, offset_size, version)


class _CuIndex:
    """One compile unit's raw DIE bytes plus what a single scan learned.

    ``ends`` maps each DIE that has children to the offset just past its
    subtree; ``plans`` maps an abbreviation code to ``(has_children, steps,
    tag)``. Both are section-absolute/unit-local exactly as pyelftools uses
    them, and both are exact or absent -- never partial.
    """

    __slots__ = (
        "address_size",
        "data",
        "ends",
        "offset_size",
        "plans",
        "start",
        "version",
    )

    def __init__(
        self,
        start: int,
        data: bytes,
        plans: dict[int, tuple[bool, tuple[int, ...], str]],
        ends: dict[int, int],
        address_size: int,
        offset_size: int,
        version: int,
    ) -> None:
        self.start = start
        self.data = data
        self.plans = plans
        self.ends = ends
        self.address_size = address_size
        self.offset_size = offset_size
        self.version = version

    def step_over(self, offset: int) -> tuple[int, str] | None:
        """``(next sibling offset, tag)`` for the DIE at *offset*, or ``None``
        at a null entry. Decodes nothing but the abbreviation code and the
        attribute lengths."""
        data = self.data
        pos = offset - self.start
        code, pos = _read_uleb(data, pos)
        if code == 0:
            return None
        has_children, steps, tag = self.plans[code]
        if has_children:
            return self.ends[offset], tag
        for step in steps:
            if step >= 0:
                pos += step
            else:
                pos = _skip_value(
                    data, pos, step, self.address_size, self.offset_size, self.version
                )
        return self.start + pos, tag


def _index_for(CU: Any) -> _CuIndex | None:
    """The unit's index, scanning once on first use (``None`` = not indexable).

    In reference mode the index is *not built at all* (``None``), so every
    consumer takes pyelftools' own stepping -- the unindexed reference answer.
    Rebuilding it per lookup instead would rescan the whole unit for every
    parent DIE, quadratic on a clang unit without ``DW_AT_sibling``.
    """
    if reference_mode():
        _INDEX_MEMO.stats.bypasses += 1
        return None
    return _INDEX_MEMO.get_or_compute(CU, request_key(), lambda: _build_index(CU))


def drop_index(CU: Any) -> None:
    """Forget *CU*'s index (it is rebuilt on next use), for bounded-memory walks."""
    _INDEX_MEMO.drop(CU)


def subtree_ends(CU: Any) -> dict[int, int] | None:
    """Map each DIE of *CU* that has children to the offset just past its subtree.

    Offsets are section-absolute, as pyelftools' ``DIE.offset`` is. Answers
    ``None`` whenever the unit cannot be sized exactly (see the module
    docstring); never raises.
    """
    index = _index_for(CU)
    return index.ends if index is not None else None


def _build_index(CU: Any) -> _CuIndex | None:
    try:
        return _scan(CU)
    except (
        _Unsizable,
        IndexError,
        ValueError,
        KeyError,
        AttributeError,
        TypeError,
    ) as exc:
        log.debug(
            "dwarf subtree index: CU at 0x%x not indexed: %s",
            getattr(CU, "cu_offset", -1),
            exc,
        )
        return None


def _scan(CU: Any) -> _CuIndex | None:
    header = CU.header
    version = int(header["version"])
    address_size = int(header["address_size"])
    offset_size = 8 if CU.structs.dwarf_format == 64 else 4
    start = int(CU.cu_die_offset)
    unit_end = int(CU.cu_offset) + int(CU.size)
    stream = CU.dwarfinfo.debug_info_sec.stream
    stream.seek(start)
    data = stream.read(unit_end - start)
    if len(data) != unit_end - start:
        return None
    table = CU.get_abbrev_table()
    plans: dict[int, tuple[bool, tuple[int, ...], str]] = {}
    ends: dict[int, int] = {}
    open_parents: list[int] = []
    pos = 0
    size = len(data)
    while pos < size:
        die_at = pos
        code = data[pos]
        if code & 0x80:
            code, pos = _read_uleb(data, pos)
        else:
            pos += 1
        if code == 0:
            # A null entry closes the innermost open parent. Trailing padding
            # after the top DIE's subtree closed is also zero bytes.
            if open_parents:
                ends[open_parents.pop()] = start + pos
            continue
        plan = plans.get(code)
        if plan is None:
            abbrev = table.get_abbrev(code)
            plan = (
                *_skip_plan(abbrev, address_size, offset_size, version),
                str(abbrev["tag"]),
            )
            plans[code] = plan
        has_children, steps, _tag = plan
        for step in steps:
            if step >= 0:
                pos += step
            else:
                pos = _skip_value(data, pos, step, address_size, offset_size, version)
        if has_children:
            open_parents.append(start + die_at)
    if pos != size or open_parents:
        # Ran past the unit, or a subtree never closed: this is not a scan
        # the table can be trusted from.
        return None
    return _CuIndex(start, data, plans, ends, address_size, offset_size, version)


def _indexed_iter_DIE_children(self: Any, die: Any) -> Any:
    """``CompileUnit.iter_DIE_children``, with :func:`subtree_ends` standing in
    for ``DW_AT_sibling``. Same DIEs, same order, same ``_terminator``."""
    if not die.has_children:
        return
    cur_offset = die.offset + die.size
    while True:
        child = self._get_cached_DIE(cur_offset)
        child.set_parent(die)
        if child.is_null():
            die._terminator = child
            return
        yield child
        if not child.has_children:
            cur_offset += child.size
            continue
        sibling = child.attributes.get("DW_AT_sibling")
        if sibling is not None and sibling.form in _CU_RELATIVE_REF_FORMS:
            # The producer said where the next sibling is (GCC does): the
            # stock rule, and no reason to scan the unit at all.
            cur_offset = sibling.value + self.cu_offset
            continue
        index = _index_for(self)
        end = index.ends.get(child.offset) if index is not None else None
        if end is None:
            # Not a DIE boundary the scan saw: never guess, finish this
            # parent's children the stock way from here.
            yield from _stock_children_from(self, die, child)
            return
        cur_offset = end


def iter_children_tagged(die: Any, tags: frozenset[str]) -> Iterator[Any]:
    """*die*'s children whose tag is in *tags*, in order, building no others.

    Equivalent to ``(c for c in die.iter_children() if c.tag in tags)``, but
    a child with any other tag is stepped over from its abbreviation alone
    instead of being decoded into a DIE object. For a walker that wants one
    kind of child out of a function body -- the formal parameters among its
    locals, lexical blocks, call sites and inlined calls -- that is most of
    the body left undecoded. Falls back to the plain filter when the unit is
    not indexable.
    """
    CU: Any = getattr(die, "cu", None)
    real = hasattr(die, "has_children") and hasattr(CU, "_get_cached_DIE")
    if real and not die.has_children:
        return
    index = _index_for(CU) if real else None
    if index is None:
        yield from (c for c in die.iter_children() if c.tag in tags)
        return
    offset = die.offset + die.size
    while True:
        stepped = index.step_over(offset)
        if stepped is None:
            return
        next_offset, tag = stepped
        if tag in tags:
            child = CU._get_cached_DIE(offset)
            child.set_parent(die)
            yield child
        offset = next_offset


def iter_formal_parameters(die: Any) -> Iterator[Any]:
    """*die*'s ``DW_TAG_formal_parameter`` children, the rest of its body
    (locals, lexical blocks, call sites, inlined calls) left undecoded."""
    return iter_children_tagged(die, FORMAL_PARAMETER_TAGS)


def _stock_children_from(CU: Any, die: Any, current: Any) -> Any:
    """Yield *die*'s children after *current* using pyelftools' own stepping."""
    started = False
    for child in type(CU).iter_DIE_children(CU, die):
        if started:
            yield child
        elif child.offset == current.offset:
            started = True


#: The one child tag a calling-convention reader wants out of a function body.
FORMAL_PARAMETER_TAGS = frozenset({"DW_TAG_formal_parameter"})

_INDEX_MEMO = InstanceMemo(
    "abicheck.extract.dwarf_subtree_index.cu_index", "_abicheck_die_index"
)

_CU_RELATIVE_REF_FORMS = frozenset(
    {
        "DW_FORM_ref1",
        "DW_FORM_ref2",
        "DW_FORM_ref4",
        "DW_FORM_ref8",
        "DW_FORM_ref",
        "DW_FORM_ref_udata",
    }
)


def install_subtree_index(dwarf_info: Any) -> Any:
    """Give every compile unit *dwarf_info* creates the indexed child iterator.

    Hooks the (private, but long-stable) ``_parse_CU_at_offset`` factory on
    this one ``DWARFInfo`` instance, so every path to a CU -- ``iter_CUs``,
    a cross-CU reference, a lookup by address -- gets it, and nothing outside
    this object changes. Returns *dwarf_info*. Leaves it untouched if the
    pyelftools in use does not have the hooks this relies on.
    """
    factory = getattr(dwarf_info, "_parse_CU_at_offset", None)
    if factory is None or getattr(dwarf_info, "_abicheck_subtree_index", False):
        return dwarf_info

    def _parse_CU_at_offset(offset: int) -> Any:
        CU = factory(offset)
        if hasattr(CU, "_get_cached_DIE") and hasattr(CU, "iter_DIE_children"):
            CU.iter_DIE_children = types.MethodType(_indexed_iter_DIE_children, CU)
        return CU

    dwarf_info._parse_CU_at_offset = _parse_CU_at_offset
    dwarf_info._abicheck_subtree_index = True
    return dwarf_info


def open_indexed_dwarf_info(elf: Any) -> Any:
    """``elf.get_dwarf_info()`` with :func:`install_subtree_index` applied."""
    return install_subtree_index(elf.get_dwarf_info())
