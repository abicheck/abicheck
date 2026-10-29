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

"""Schema v69: the ABI fact of each DWARF layout member (``FieldInfo.type_abi_fact``).

A binary-only dump's TypeField carries the fundamental-type fact its member DIE
states (extract/dwarf_abi_fact.py). The same value is persisted per member of
the ``dwarf`` block (``structs`` and the ``struct_odr_conflicts`` variants), so
the depth projection, which rebuilds record fields from that layout, keeps it.

A member whose type DWARF does not state a fundamental for (a pointer, a
struct) has no value and is written without the key, so every other document
encodes exactly as v68. A document before v69, or one without the key, decodes
to ``None`` (unset), which never proves two slots equal.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..model.abi_fundamental import AbiFundamental
from .fact_decode import abi_fundamental_from_json

__all__ = [
    "decode_dwarf_member_abi",
    "dwarf_layout_dicts",
    "drop_uncaptured_dwarf_layout_facts",
]

_TYPE_ABI_KEY = "type_abi_fact"


def dwarf_layout_dicts(dwarf_dict: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Every layout dict under a ``dwarf`` block: ``structs`` and the further
    ODR-conflicting definitions (``struct_odr_conflicts``), in document order."""
    yield from dwarf_dict.get("structs", {}).values()
    for variants in dwarf_dict.get("struct_odr_conflicts", {}).values():
        yield from variants


def decode_dwarf_member_abi(member: dict[str, Any]) -> AbiFundamental | None:
    """A DWARF layout member's ABI fact, or ``None`` when absent or unreadable.

    A value that does not rebuild is malformed evidence and reads as unset, so
    it never proves two slots equal.
    """
    raw = member.get(_TYPE_ABI_KEY)
    if raw is None:
        return None
    try:
        return abi_fundamental_from_json(raw)
    except (KeyError, TypeError, ValueError):
        return None


def drop_uncaptured_dwarf_layout_facts(d: dict[str, Any]) -> None:
    """In place: omit a member's ``type_abi_fact`` when it is ``None``, the value
    the decoder reads an absent key back as."""
    dwarf = d.get("dwarf")
    if not isinstance(dwarf, dict):
        return
    for layout in dwarf_layout_dicts(dwarf):
        for member in layout.get("fields", ()):
            if member.get(_TYPE_ABI_KEY) is None:
                member.pop(_TYPE_ABI_KEY, None)
