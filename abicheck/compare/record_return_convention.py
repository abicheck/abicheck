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

from collections.abc import Iterable
from typing import Protocol

from ..diff_helpers import make_change
from ..model.abi_fundamental import FundamentalEncoding
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.declarations import Function
from ..model.entities import RecordType, TypeField
from ..model.semantic_ir import CanonicalEntity
from ..model.semantic_ir_record_layout import entity_layout

__all__ = [
    "record_return_convention_changes",
    "returned_by_value_records",
    "return_convention",
    "member_class",
]


class LayoutIndex(Protocol):
    """The ``RecordLayoutIndex`` surface this module reads."""

    @property
    def lp64_x86_64(self) -> bool: ...

    @property
    def returned_by_value(self) -> frozenset[str]: ...

    def entity_for(self, record: RecordType) -> CanonicalEntity | None: ...


def member_class(member: TypeField) -> tuple[int, str] | None:
    """``(size_bytes, class)`` of a scalar member from its ABI fact, or ``None``.

    The fact (``TypeField.type_abi_fact``) is the header backend's reading of
    the declared type: a fundamental, an enumeration, or a pointer. A vector,
    a float wider than eight bytes (``long double``, x87), or a member with no
    fact is undecided.
    """
    fact = member.type_abi_fact
    if fact is None or not fact.is_present or fact.value is None:
        return None
    abi = fact.value
    if abi.lanes:
        return None
    if abi.encoding is FundamentalEncoding.FLOAT:
        return (abi.size, "SSE") if abi.size <= 8 else None
    return abi.size, "INTEGER"


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
        decided = member_class(member)
        if decided is None:
            return None
        member_size, eight_class = decided
        # A scalar's natural alignment equals its size on LP64 x86_64.
        if member.offset_bits % (member_size * 8):
            return ("memory",)
        start = member.offset_bits // 8
        for index in range(start // 8, (start + member_size - 1) // 8 + 1):
            if index >= len(eightbytes):
                return None
            eightbytes[index] = _merge(eightbytes[index], eight_class)
    if any(c is None for c in eightbytes):
        # A padding-only eightbyte has no class of its own; not decided here.
        return None
    return ("regs", *(str(c) for c in eightbytes))


def returned_by_value_records(functions: Iterable[Function]) -> frozenset[str]:
    """The record identities some declared function returns by value.

    Read from ``Function.return_by_value_fact`` (set by the header backend from
    the declared type graph) and the resolved identities of the return type.
    A function whose fact is unset or False contributes nothing, and a return
    type whose identities are unresolved contributes nothing -- an undetermined
    return never yields a finding.
    """
    names: set[str] = set()
    for function in functions:
        by_value = function.return_by_value_fact
        if by_value is None or by_value.value is not True:
            continue
        identities = function.return_type_identities_fact
        if identities is None or not identities.is_present:
            continue
        names.update(identities.value or ())
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
    keys = {name, t_old.qualified_name, t_new.qualified_name} - {None}
    if not keys & (old_index.returned_by_value | new_index.returned_by_value):
        return
    old_size, _ = entity_layout(old_index.entity_for(t_old))
    new_size, _ = entity_layout(new_index.entity_for(t_new))
    old_conv = return_convention(
        old_size.value if old_size.is_present else None, t_old.fields
    )
    new_conv = return_convention(
        new_size.value if new_size.is_present else None, t_new.fields
    )
    # A nontrivial copy or destructor forces an invisible result pointer,
    # even for a one-word record. Older snapshots lack this independent fact;
    # do not infer a transition from a one-sided observation.
    if t_old.trivial_for_calls is not None and t_new.trivial_for_calls is not None:
        if not t_old.trivial_for_calls:
            old_conv = ("memory",)
        if not t_new.trivial_for_calls:
            new_conv = ("memory",)
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
