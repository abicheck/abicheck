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

"""The x86_64 SysV return convention of a by-value record, from its header layout.

A function that returns a record by value returns it in registers or through a
hidden caller-provided pointer (sret), by the System V x86_64 classification
(psABI, section 3.2.3): a record larger than 16 bytes, or with an unaligned
field, is MEMORY; a record of at most 16 bytes is classified eightbyte by
eightbyte as INTEGER (returned in RAX, then RDX) or SSE (XMM0, then XMM1), an
INTEGER eightbyte winning over an SSE one that shares it.

The classification reads only what the header records: each member's spelling
and its ``offset_bits``, and the record's size from the semantic IR. A member
this table does not decide (a nested record, an array, an enum, a bit-field,
``long double``) makes the convention unknown, and an unknown side yields no
finding. The check runs only on LP64 x86_64 ELF snapshots, which is the model
the table is written for.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Protocol

from ..diff_helpers import make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.declarations import Function
from ..model.entities import RecordType, TypeField
from ..model.semantic_ir import CanonicalEntity
from ..model.semantic_ir_record_layout import entity_layout

__all__ = [
    "record_return_convention_changes",
    "returned_by_value_names",
    "return_convention",
    "scalar_class",
]

#: Scalars by spelling: (size in bytes, SysV class). Pointers are handled apart.
_SCALARS: dict[str, tuple[int, str]] = {
    "bool": (1, "INTEGER"),
    "char": (1, "INTEGER"),
    "signed char": (1, "INTEGER"),
    "unsigned char": (1, "INTEGER"),
    "short": (2, "INTEGER"),
    "unsigned short": (2, "INTEGER"),
    "int": (4, "INTEGER"),
    "unsigned int": (4, "INTEGER"),
    "long": (8, "INTEGER"),
    "unsigned long": (8, "INTEGER"),
    "long long": (8, "INTEGER"),
    "unsigned long long": (8, "INTEGER"),
    "float": (4, "SSE"),
    "double": (8, "SSE"),
}


class LayoutIndex(Protocol):
    """The ``RecordLayoutIndex`` surface this module reads."""

    @property
    def lp64_x86_64(self) -> bool: ...

    @property
    def returned_by_value(self) -> frozenset[str]: ...

    def entity_for(self, record: RecordType) -> CanonicalEntity | None: ...


_KEYWORDS = re.compile(r"\b(struct|class|union|const|volatile)\b")


def scalar_class(spelling: str) -> tuple[int, str] | None:
    """``(size_bytes, class)`` of a scalar member, or ``None`` if undecided."""
    text = _KEYWORDS.sub("", spelling)
    text = re.sub(r"\s+", " ", text).strip()
    if text.endswith(("*", "&")):
        return 8, "INTEGER"
    if text in _SCALARS:
        return _SCALARS[text]
    # Spellings with an explicit ``int``/``signed`` ('long int', 'unsigned long int').
    words = [w for w in text.split() if w not in ("signed", "int")]
    key = " ".join(words) if words else "int"
    if key.startswith("unsigned "):
        key = key[len("unsigned ") :]
    if key in ("", "int"):
        return _SCALARS["int"]
    if key in _SCALARS:
        return _SCALARS[key]
    return None


def _merge(current: str | None, new: str) -> str:
    """An eightbyte's class: INTEGER wins over SSE (psABI 3.2.3 rule 4)."""
    if current is None:
        return new
    return "INTEGER" if "INTEGER" in (current, new) else "SSE"


def return_convention(
    size_bits: int | None, fields: list[TypeField]
) -> tuple[str, ...] | None:
    """``("memory",)`` or ``("regs", eightbyte classes)``, or ``None`` if undecided."""
    if size_bits is None or size_bits % 8:
        return None
    size = size_bits // 8
    if size == 0:
        return None
    if size > 16:
        return ("memory",)
    eightbytes: list[str | None] = [None] * ((size + 7) // 8)
    for member in fields:
        if member.is_bitfield or member.offset_bits is None:
            return None
        decided = scalar_class(member.type)
        if decided is None:
            return None
        member_size, member_class = decided
        # A scalar's natural alignment equals its size on LP64 x86_64.
        if member.offset_bits % (member_size * 8):
            return ("memory",)
        start = member.offset_bits // 8
        for index in range(start // 8, (start + member_size - 1) // 8 + 1):
            if index >= len(eightbytes):
                return None
            eightbytes[index] = _merge(eightbytes[index], member_class)
    if any(c is None for c in eightbytes):
        # A padding-only eightbyte has no class of its own; not decided here.
        return None
    return ("regs", *(str(c) for c in eightbytes))


def returned_by_value_names(functions: Iterable[Function]) -> frozenset[str]:
    """The record spellings that some declared function returns by value."""
    names: set[str] = set()
    for function in functions:
        text = _KEYWORDS.sub("", function.return_type)
        text = re.sub(r"\s+", " ", text).strip()
        if text and not text.endswith(("*", "&")):
            names.add(text)
    return frozenset(names)


def record_return_convention_changes(
    changes: list[Change],
    name: str,
    t_old: RecordType,
    t_new: RecordType,
    old_index: LayoutIndex,
    new_index: LayoutIndex,
) -> None:
    """Append ``STRUCT_RETURN_CONVENTION_CHANGED`` for one matched record pair.

    Only when both sides are LP64 x86_64 ELF, the record is returned by value
    by some declared function, and both sides' conventions are decided and
    differ. An undecided side yields no finding.
    """
    if not (old_index.lp64_x86_64 and new_index.lp64_x86_64):
        return
    if (
        name not in old_index.returned_by_value
        and name not in new_index.returned_by_value
    ):
        return
    old_size, _ = entity_layout(old_index.entity_for(t_old))
    new_size, _ = entity_layout(new_index.entity_for(t_new))
    old_conv = return_convention(
        old_size.value if old_size.is_present else None, t_old.fields
    )
    new_conv = return_convention(
        new_size.value if new_size.is_present else None, t_new.fields
    )
    if old_conv is None or new_conv is None or old_conv == new_conv:
        return
    qualified = t_new.qualified_name or t_old.qualified_name
    changes.append(
        make_change(
            ChangeKind.STRUCT_RETURN_CONVENTION_CHANGED,
            symbol=name,
            name=name,
            old=_describe(old_conv),
            new=_describe(new_conv),
            description=(
                f"{name}: by-value return convention changed "
                f"({_describe(old_conv)} -> {_describe(new_conv)})"
            ),
            qualified_name=qualified,
            entity_id=t_old.entity_id or t_new.entity_id,
        )
    )


def _describe(conv: tuple[str, ...]) -> str:
    if conv[0] == "memory":
        return "memory (sret)"
    return "regs(" + ",".join(conv[1:]) + ")"
