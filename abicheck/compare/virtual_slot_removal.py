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

"""``FUNC_VIRTUAL_REMOVED`` for a virtual method removed with its vtable slot.

``FUNC_VIRTUAL_REMOVED`` names the dispatch break: a vtable entry is gone, so
every later slot moves and an old binary calling through the vtable reaches
the wrong function. The signature detector emits it when a method stays but
stops being virtual (case168). A virtual method *removed* outright takes its
slot just the same, but was reported only as ``func_removed`` plus the
record-level ``type_vtable_changed``/``vtable_slot_count_changed``, so a
consumer filtering on the kind missed it (found by the ABI edit corpus,
``virtual_remove``).

This detector reports it for exactly that shape, from the record vtables
both sides record (header AST or DWARF), not from the function's own
evidence, which a binary-only snapshot may not carry:

* the symbol is an entry of some old record's vtable;
* the symbol is gone from the new side: no new declaration and no new
  export under that name (a method that stays and leaves the vtable is the
  devirtualization case, already reported by the signature detector);
* every such record still exists on the new side, its vtable is recorded
  (``vtable_fact`` present on both sides), lacks the symbol, and is shorter.
  Removing an *override* keeps the slot (the base's implementation fills
  it), so the vtable keeps its length and nothing is reported.

The finding is added beside the removal, never in its place: the function
is still removed (``func_removed`` keeps the removal accounting), and the
lost slot is a second, independent consequence.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..detector_registry import registry
from ..diff_helpers import make_change
from ..elf_symbol_filter import FUNCTION_SYMBOL_TYPES, exported_symbol_names
from ..model.availability import FactStatus
from ..model.change_catalog.kinds import ChangeKind

if TYPE_CHECKING:
    from ..model import AbiSnapshot, RecordType
    from ..model.change import Change

__all__ = ["virtual_slot_removals"]


def _vtable(record: RecordType) -> list[str] | None:
    fact = record.vtable_fact
    # The status attribute, not ``is_present()``: other detectors already
    # asked every record's vtable fact (the repeat-call audit counts each ask).
    if fact is None or fact.status is not FactStatus.PRESENT:
        return None
    return list(fact.value or ())


def _records_by_name(snapshot: AbiSnapshot) -> dict[str, list[RecordType]]:
    by_name: dict[str, list[RecordType]] = {}
    for record in snapshot.declarations.types:
        by_name.setdefault(record.qualified_name or record.name, []).append(record)
    return by_name


def _still_present(snapshot: AbiSnapshot, symbol: str) -> bool:
    if symbol in snapshot.function_map:
        return True
    elf = snapshot.elf
    if elf is None or not elf.symbols:
        return False
    return symbol in exported_symbol_names(elf, FUNCTION_SYMBOL_TYPES)


def virtual_slot_removals(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """One ``FUNC_VIRTUAL_REMOVED`` per removed virtual whose slot left every
    vtable that held it. See the module docstring for the rule."""
    holders: dict[str, list[RecordType]] = {}
    for record in old.declarations.types:
        for entry in _vtable(record) or ():
            holders.setdefault(entry, []).append(record)
    if not holders:
        return []
    new_records = _records_by_name(new)
    changes: list[Change] = []
    for symbol, records in holders.items():
        if _still_present(new, symbol) or not _slot_left_every_holder(
            symbol, records, new_records
        ):
            continue
        old_fn = old.function_map.get(symbol)
        name = old_fn.name if old_fn is not None else symbol
        changes.append(
            make_change(
                ChangeKind.FUNC_VIRTUAL_REMOVED,
                symbol=symbol,
                name=name,
                description=(
                    f"Virtual method removed with its vtable slot: {name} "
                    f"({', '.join(sorted({r.qualified_name or r.name for r in records}))})"
                ),
                old_value="virtual",
                new_value="removed",
                entity_id=old_fn.entity_id if old_fn is not None else None,
            )
        )
    return changes


def _slot_left_every_holder(
    symbol: str,
    records: list[RecordType],
    new_records: dict[str, list[RecordType]],
) -> bool:
    for record in records:
        counterparts = new_records.get(record.qualified_name or record.name, [])
        if len(counterparts) != 1:
            return False
        old_vtable, new_vtable = _vtable(record), _vtable(counterparts[0])
        if old_vtable is None or new_vtable is None:
            return False
        if symbol in new_vtable or len(new_vtable) >= len(old_vtable):
            return False
    return True


@registry.detector("virtual_slot_removal")
def _diff_virtual_slot_removal(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    """``FUNC_VIRTUAL_REMOVED`` for a removed virtual -- see the module docstring."""
    return virtual_slot_removals(old, new)
