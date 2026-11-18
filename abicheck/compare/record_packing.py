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

"""Packed-record detection from the header-recorded layout (``STRUCT_PACKING_CHANGED``).

A record is *packed* when its recorded alignment is below the largest natural
alignment of its members: ``__attribute__((packed))`` and ``#pragma pack(N)``
both lower the alignment this way, and castxml and clang both record that
alignment. ``alignas``/``aligned(N)`` raise alignment and are not packing.

The effective pack value is the record's recorded alignment, in bits, when it
is packed. A record is compared only when every member's natural alignment is
known: a scalar, a pointer or reference, or an array of one of those, on the
LP64 x86_64 model this check runs under. A member of any other kind (a nested
record, a typedef, an enum) makes the record's packing unknown, and an unknown
side yields no finding (T9: recorded as declined, never guessed).

Layout is read through the semantic IR (``entity_layout``), never from
``RecordType`` fields, as ``record_layout.py`` requires; the member types are
the recorded field spellings.
"""

from __future__ import annotations

import re
from typing import Protocol

from ..diff_helpers import make_change
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.entities import RecordType, TypeField
from ..model.semantic_ir import CanonicalEntity
from ..model.semantic_ir_record_layout import entity_layout
from .declined_comparisons import record_declined

__all__ = ["packing_state", "record_packing_changes", "scalar_natural_align_bits"]


class LayoutIndex(Protocol):
    """The ``RecordLayoutIndex`` surface this module reads (kept a protocol so
    ``record_layout`` can import this module without a cycle)."""

    @property
    def lp64_x86_64(self) -> bool: ...

    def entity_for(self, record: RecordType) -> CanonicalEntity | None: ...


_CV = re.compile(r"\b(const|volatile)\b")
_WORDS = {
    "unsigned",
    "signed",
    "int",
    "short",
    "long",
    "char",
    "bool",
    "float",
    "double",
    "__int128",
    "wchar_t",
    "char8_t",
    "char16_t",
    "char32_t",
}


def scalar_natural_align_bits(spelling: str) -> int | None:
    """The natural alignment, in bits, of a member spelled *spelling* on LP64.

    ``None`` for any spelling this table does not decide (a record, an enum, a
    typedef, a template): the caller treats that as unknown, not as 8 bits.
    """
    text = re.sub(r"\s+", " ", _CV.sub("", spelling)).strip()
    if text.endswith(("*", "&")):
        return 64
    if "[" in text:
        return scalar_natural_align_bits(text.split("[", 1)[0])
    words = text.split()
    if not words or any(w not in _WORDS for w in words):
        return None
    # ``signed``/``unsigned``/``int`` only select a width or signedness, which
    # the core words below already fix; a bare ``int``/``unsigned`` is 32 bits.
    core = [w for w in words if w not in ("signed", "unsigned", "int")]
    if not core:
        return 32
    key = " ".join(core)
    table = {
        "char": 8,
        "bool": 8,
        "char8_t": 8,
        "short": 16,
        "char16_t": 16,
        "float": 32,
        "wchar_t": 32,
        "char32_t": 32,
        "long": 64,
        "long long": 64,
        "double": 64,
        "long double": 128,
        "__int128": 128,
    }
    if key in table:
        return table[key]
    if key in ("double long",):
        return 128
    return None


def _natural_max_bits(fields: list[TypeField]) -> int | None:
    """The largest natural alignment among *fields*, or ``None`` if any is unknown."""
    if not fields:
        return None
    best = 0
    for field_ in fields:
        align = scalar_natural_align_bits(field_.type)
        if align is None:
            return None
        best = max(best, align)
    return best


def packing_state(
    index: LayoutIndex, record: RecordType, fields: list[TypeField]
) -> tuple[bool, int] | None:
    """``(packed, pack_bits)`` of *record*, or ``None`` when it cannot be decided.

    ``pack_bits`` is the recorded alignment in bits, and only ``packed`` records
    carry a meaningful pack value; a non-packed record reports ``(False, 0)``.
    """
    _size, align = entity_layout(index.entity_for(record))
    if not align.is_present or align.value is None:
        return None
    natural = _natural_max_bits(fields)
    if natural is None:
        return None
    declared = int(align.value)
    if declared < natural:
        return True, declared
    return False, 0


def record_packing_changes(
    changes: list[Change],
    name: str,
    t_old: RecordType,
    t_new: RecordType,
    old_index: LayoutIndex,
    new_index: LayoutIndex,
) -> None:
    """Append ``STRUCT_PACKING_CHANGED`` for one matched record pair.

    Only when both sides are LP64 x86_64 ELF (the natural-alignment table
    assumes that model) and never for a union. A side whose packing is unknown
    is recorded as declined rather than compared.
    """
    if not (old_index.lp64_x86_64 and new_index.lp64_x86_64):
        return
    if t_old.is_union or t_new.is_union:
        return
    old_state = packing_state(old_index, t_old, t_old.fields)
    new_state = packing_state(new_index, t_new, t_new.fields)
    qualified = t_new.qualified_name or t_old.qualified_name
    if old_state is None or new_state is None:
        if (old_state is None) != (new_state is None):
            record_declined(
                qualified or name,
                "record packing established on one side only",
            )
        return
    if old_state == new_state:
        return
    changes.append(
        make_change(
            ChangeKind.STRUCT_PACKING_CHANGED,
            symbol=name,
            name=name,
            old=_describe(old_state),
            new=_describe(new_state),
            description=(
                f"{name}: record packing changed "
                f"({_describe(old_state)} -> {_describe(new_state)})"
            ),
            qualified_name=qualified,
            entity_id=t_old.entity_id or t_new.entity_id,
        )
    )


def _describe(state: tuple[bool, int]) -> str:
    packed, pack_bits = state
    return f"packed({pack_bits} bits)" if packed else "natural"
