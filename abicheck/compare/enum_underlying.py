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

"""Header-tier enum underlying-size comparison.

``ENUM_UNDERLYING_SIZE_CHANGED`` used to come only from the DWARF layout
pass (``type_layout_diff.diff_enum_layouts``), so a headers-plus-binary
compare with no debug info read ``enum E : unsigned char`` →
``enum E : unsigned int`` on a by-value parameter as NO_CHANGE. This pass
reads the header model's ``EnumType.underlying_type`` instead.

It compares *byte sizes*, not spellings: a signedness-only change keeps the
size. ``long`` takes its size from the side's data model (4 on PE/LLP64,
the ELF pointer size otherwise, ``diff_symbols``'s own LLP64 rule); a
spelling whose size is still unknown (``wchar_t``, a typedef, a placeholder)
resolves to ``None`` and is never compared, so it cannot manufacture a break. The old/new values are
the byte sizes the DWARF pass reports, so when both tiers see the change
``finding_dedup`` collapses the pair into one finding.
"""

from __future__ import annotations

from collections.abc import Callable

from ..diff_helpers import make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.entities import EnumType
from ..model.execution_cache import memoized
from ..model.snapshot import AbiSnapshot

#: Target-independent byte sizes of the integer spellings an enum's
#: underlying type can resolve to. ``long`` is in :data:`_LONG_SPELLINGS`
#: instead; ``wchar_t`` is absent (2 on Windows, 4 elsewhere, and rare).
_UNDERLYING_BYTE_SIZE: dict[str, int] = {
    **dict.fromkeys(
        ("bool", "_Bool", "char", "signed char", "unsigned char", "char8_t"), 1
    ),
    **dict.fromkeys(("int8_t", "uint8_t"), 1),
    **dict.fromkeys(
        ("short", "short int", "signed short", "unsigned short", "short unsigned int"),
        2,
    ),
    **dict.fromkeys(("char16_t", "int16_t", "uint16_t", "short signed int"), 2),
    **dict.fromkeys(
        ("int", "signed", "signed int", "unsigned", "unsigned int", "char32_t"), 4
    ),
    **dict.fromkeys(("int32_t", "uint32_t"), 4),
    **dict.fromkeys(
        ("long long", "long long int", "unsigned long long", "signed long long"), 8
    ),
    **dict.fromkeys(
        ("long long unsigned int", "long long signed int", "int64_t", "uint64_t"), 8
    ),
    **dict.fromkeys(("__int128", "unsigned __int128", "__int128 unsigned"), 16),
}
#: The ``<cstdint>`` names also spelled through ``std::``.
_UNDERLYING_BYTE_SIZE.update(
    {
        f"std::{name}": _UNDERLYING_BYTE_SIZE[name]
        for name in ("int8_t", "uint8_t", "int16_t", "uint16_t")
        + ("int32_t", "uint32_t", "int64_t", "uint64_t")
    }
)


#: The ``long`` spellings, whose size is the data model's ``long`` width.
_LONG_SPELLINGS = frozenset(
    {"long", "long int", "signed long", "long signed int", "unsigned long"}
    | {"long unsigned int", "unsigned long int"}
)


def long_byte_size(snapshot: AbiSnapshot | None) -> int | None:
    """``sizeof(long)`` on *snapshot*'s target: 4 under LLP64 (PE), the ELF
    pointer size under ILP32/LP64, 8 on Mach-O; ``None`` when unknown."""
    if snapshot is None:
        return None
    if getattr(snapshot, "platform", None) == "pe" or getattr(snapshot, "pe", None):
        return 4
    elf = getattr(snapshot, "elf", None)
    if elf is not None:
        return int(elf.pointer_size)
    if getattr(snapshot, "platform", None) == "macho" or getattr(
        snapshot, "macho", None
    ):
        return 8
    return None


@memoized(maxsize=256)
def underlying_byte_size(spelling: str, long_size: int | None = None) -> int | None:
    """The byte size of an underlying-type *spelling*, or ``None`` when it is
    not an integer spelling this table knows (*long_size* sizes ``long``)."""
    text = " ".join(spelling.replace("const", " ").replace("volatile", " ").split())
    if text in _LONG_SPELLINGS:
        return long_size
    return _UNDERLYING_BYTE_SIZE.get(text)


def enum_underlying_size_detector(
    old: AbiSnapshot | None, new: AbiSnapshot | None, header_aware: bool = True
) -> Callable[[EnumType, EnumType], list[Change]]:
    """The per-enum comparison for one snapshot pair, with each side's
    ``sizeof(long)`` resolved once rather than per enum. *header_aware* is
    ``both_header_aware(old, new)``: without confirmed header evidence on
    both sides ``underlying_type`` may be the ``"int"`` default, so nothing
    is compared (the DWARF layout pass still covers debug-info snapshots)."""
    old_long, new_long = long_byte_size(old), long_byte_size(new)

    def changes(e_old: EnumType, e_new: EnumType) -> list[Change]:
        """One ``ENUM_UNDERLYING_SIZE_CHANGED`` when both sides' underlying
        types resolve to known, different byte sizes; otherwise nothing."""
        if not header_aware:
            return []
        old_size = underlying_byte_size(e_old.underlying_type, old_long)
        new_size = underlying_byte_size(e_new.underlying_type, new_long)
        if old_size is None or new_size is None or old_size == new_size:
            return []
        return [
            make_change(
                ChangeKind.ENUM_UNDERLYING_SIZE_CHANGED,
                symbol=e_old.name,
                name=e_old.name,
                old=str(old_size),
                new=str(new_size),
            )
        ]

    return changes
