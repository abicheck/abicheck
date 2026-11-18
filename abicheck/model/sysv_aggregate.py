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

"""How x86-64 System V returns a struct, from the header's record layout.

ADR-094's ``call`` axis needs the :class:`RetShape` of a struct return and,
to compare two structs of the same class, their member layout
(:class:`Member`). This module derives both from the L2 header facts a
snapshot stores -- the record's size and alignment, each field's offset, its
ABI fact (:class:`AbiFundamental`) or its pointer spelling, bitfield widths,
and nested records looked up by name. It reads no DWARF.

The SysV rules applied (psABI 3.2.3, "Returning of values"):

* a struct over 16 bytes is ``MEMORY`` (caller memory, hidden ``rdi``);
* otherwise each eightbyte is INTEGER if any member in it is INTEGER, else
  SSE; ``INT1``/``SSE1``/``INT2``/``SSE2`` name the four uniform shapes;
* a C++ record that is not trivial for the purposes of calls is returned in
  memory (Itanium C++ ABI 3.1.1). The register rules are applied only when
  that is excluded: the header was parsed as C, or the extractor proved the
  record trivial for calls. Only the castxml backend's trait proves that:
  it additionally requires a non-deleted copy or move constructor, which is
  the Itanium condition. The clang and gcc backends record plain
  ``is_trivially_copyable``, which is also true of a record whose copy and
  move constructors are all deleted (returned in memory), so a C++ record
  from any other backend is not classified.

Everything else is ``None`` (the comparison reads UNKNOWN): unions, a
mixed INTEGER/SSE struct (``rax:xmm0``), ``float`` / ``long double`` /
vector members, ``_Bool`` members (their encoding is not carried in
:class:`Member`, so ``_Bool`` and ``unsigned char`` would compare equal),
records with more than :data:`MAX_MEMBERS` scalar members, arrays whose element is a fundamental type (the element's
ABI fact is not stored), enum fields without an ABI fact, base classes, a
vtable, packed or over-aligned records, misaligned members, and any field
whose type cannot be resolved.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .abi_fundamental import FundamentalEncoding
from .availability import FactStatus
from .call_interface import ArgClass, Member, RetShape
from .declarations import Function
from .entities import RecordType, TypeField
from .fact import Fact
from .snapshot import AbiSnapshot

__all__ = ["StructReturn", "classify_record", "struct_return_of"]

#: A 16-byte struct is the largest returned in registers.
_MAX_REGISTER_BITS = 128

#: The most flattened scalar members one record may expand to; past it the
#: record is not classified (bounds work on ``void *p[10000000]``).
MAX_MEMBERS = 256

_ARRAY = re.compile(r"^(?P<elem>.+?)\s*\[(?P<n>\d+)\]$")
_TAG = re.compile(r"^(?:struct|class)\s+")


@dataclass(frozen=True, slots=True)
class StructReturn:
    """A classified struct return: its shape, size in bits and flattened
    scalar members (``None`` when the shape is known but they are not)."""

    shape: RetShape
    size: int
    members: tuple[Member, ...] | None


Resolve = Callable[[str], RecordType | None]


def _is_object_pointer(spelling: str) -> bool:
    text = spelling.strip()
    return text.endswith("*") and "::*" not in text and "(" not in text


def _fundamental(f: TypeField) -> tuple[int, int, ArgClass] | None:
    """(size bits, align bits, class) of a fundamental field, or ``None``."""
    fact = f.type_abi_fact
    if fact is None or fact.status is not FactStatus.PRESENT or fact.value is None:
        return None
    v = fact.value
    if v.lanes or v.encoding is FundamentalEncoding.BOOLEAN:
        return None
    if v.encoding is FundamentalEncoding.FLOAT:
        # Only double: float pairs share an eightbyte, long double is X87.
        return (64, 64, ArgClass.SSE) if v.size == v.align == 8 else None
    if v.size > 8:
        return None
    return v.size * 8, v.align * 8, ArgClass.INTEGER


def _maybe_nonempty(fact: Fact[list[str]] | None) -> bool:
    """Whether a list fact may hold an entry. A list that was not collected
    is taken as empty: a C record has no bases or vtable, and a C++ record
    reaches here only when proved trivially copyable (no virtual member or
    base); a non-virtual base of such a record is recorded when present."""
    return (
        fact is not None
        and fact.status in (FactStatus.PRESENT, FactStatus.PARTIAL)
        and bool(fact.value)
    )


def _flatten(
    record: RecordType, base: int, resolve: Resolve, depth: int
) -> tuple[list[Member], int] | None:
    """The record's scalar members at *base*, and its natural alignment."""
    if (
        depth > 8
        or record.is_union
        or record.kind == "union"
        or record.is_opaque
        or record.is_template_pattern
        or record.size_bits is None
        or record.alignment_bits is None
        or not record.fields
        or _maybe_nonempty(record.bases_fact)
        or _maybe_nonempty(record.virtual_bases_fact)
        or _maybe_nonempty(record.vtable_fact)
    ):
        return None
    out: list[Member] = []
    natural = 8
    for f in record.fields:
        if f.offset_bits is None:
            return None
        got = _field(f, base + f.offset_bits, resolve, depth)
        if got is None:
            return None
        members, align = got
        if f.is_bitfield and not f.name:
            # An unnamed bitfield is padding: it neither aligns the record nor
            # holds a value the caller reads, so it is not part of the image.
            align = 8
            members = []
        elif not f.is_bitfield and f.offset_bits % align:
            return None  # misaligned member: packed
        out.extend(members)
        if len(out) > MAX_MEMBERS:
            return None
        natural = max(natural, align)
    if record.alignment_bits != natural or record.size_bits % natural:
        return None  # packed or explicitly over-aligned
    return out, natural


def _field(
    f: TypeField, at: int, resolve: Resolve, depth: int
) -> tuple[list[Member], int] | None:
    if f.is_bitfield:
        fund = _fundamental(f)
        if fund is None or fund[2] is not ArgClass.INTEGER or not f.bitfield_bits:
            return None
        if at // 64 != (at + f.bitfield_bits - 1) // 64:
            return None
        return [Member(at, f.bitfield_bits, ArgClass.INTEGER)], fund[1]
    typed = _typed(f.type, f, at, resolve, depth)
    return None if typed is None else (typed[0], typed[1])


def _typed(
    spelling: str, f: TypeField | None, at: int, resolve: Resolve, depth: int
) -> tuple[list[Member], int, int] | None:
    """(members at *at*, alignment, size) of a field typed *spelling*."""
    if _is_object_pointer(spelling):
        return [Member(at, 64, ArgClass.INTEGER)], 64, 64
    m = _ARRAY.match(spelling.strip())
    if m:
        count = int(m.group("n"))
        elem = _typed(m.group("elem"), None, 0, resolve, depth)
        if elem is None or count == 0:
            return None
        members, align, stride = elem
        if count * len(members) > MAX_MEMBERS:
            return None
        return (
            [
                Member(at + i * stride + x.offset, x.size, x.cls)
                for i in range(count)
                for x in members
            ],
            align,
            stride * count,
        )
    if f is not None:
        fund = _fundamental(f)
        if fund is not None:
            size, align, cls = fund
            return [Member(at, size, cls)], align, size
    nested = resolve(spelling)
    if nested is None or nested.size_bits is None:
        return None
    flat = _flatten(nested, at, resolve, depth + 1)
    if flat is None:
        return None
    members, align = flat
    return members, align, nested.size_bits


def classify_record(
    record: RecordType,
    resolve: Resolve,
    *,
    c_language: bool,
    trivial_for_calls_proved: bool = False,
) -> StructReturn | None:
    """The SysV return classification of *record*, or ``None`` when it is
    outside the modelled domain (see the module docstring).
    *trivial_for_calls_proved* says the record's ``is_trivially_copyable``
    also excludes deleted copy/move constructors (the castxml trait)."""
    if not c_language and (
        not trivial_for_calls_proved or record.is_trivially_copyable is not True
    ):
        return None
    flat = _flatten(record, 0, resolve, 0)
    if flat is None or record.size_bits is None:
        return None
    members = tuple(sorted(flat[0], key=lambda x: x.offset))
    if not members:
        return None  # only padding (unnamed bitfields): nothing the caller reads
    size = record.size_bits
    for a, b in zip(members, members[1:]):
        if a.offset + a.size > b.offset:
            return None
    if members[-1].offset + members[-1].size > size:
        return None
    if size > _MAX_REGISTER_BITS:
        return StructReturn(RetShape.MEMORY, size, members)
    classes = []
    for eb in range(-(-size // 64)):
        in_eb = {x.cls for x in members if x.offset // 64 == eb}
        if not in_eb:
            return None  # an all-padding eightbyte: not modelled
        classes.append(ArgClass.INTEGER if ArgClass.INTEGER in in_eb else ArgClass.SSE)
    shape = {
        (ArgClass.INTEGER,): RetShape.INT1,
        (ArgClass.SSE,): RetShape.SSE1,
        (ArgClass.INTEGER, ArgClass.INTEGER): RetShape.INT2,
        (ArgClass.SSE, ArgClass.SSE): RetShape.SSE2,
    }.get(tuple(classes))
    return None if shape is None else StructReturn(shape, size, members)


def _resolver(snapshot: AbiSnapshot) -> Resolve:
    typedefs = snapshot.declarations.typedefs

    def resolve(spelling: str) -> RecordType | None:
        name = spelling.strip()
        for _ in range(8):
            if _TAG.match(name):
                # An elaborated ``struct R`` names the tag R; an ordinary
                # typedef also called R is another namespace (C 6.2.3).
                return snapshot.type_by_name(_TAG.sub("", name).strip())
            target = typedefs.get(name)
            # castxml maps a typedef of an anonymous record to "", clang to
            # "struct <typedef name>" (followed above as a tag).
            if not target:
                return snapshot.type_by_name(name)
            name = target.strip()
        return None

    return resolve


def struct_return_of(fn: Function, snapshot: AbiSnapshot) -> StructReturn | None:
    """The classified struct return of *fn* (from *snapshot*), or ``None``
    when the return is not a by-value record or is outside the domain."""
    if fn.return_pointer_depth or _is_object_pointer(fn.return_type):
        return None
    resolve = _resolver(snapshot)
    record = resolve(fn.return_type)
    if record is None:
        return None
    return classify_record(
        record,
        resolve,
        c_language=snapshot.language_profile == "c",
        trivial_for_calls_proved=snapshot.ast_producer == "castxml",
    )
