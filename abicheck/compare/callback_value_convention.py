# SPDX-License-Identifier: Apache-2.0
"""SysV AMD64 invisible-reference transitions inside typed callback parameters."""

from __future__ import annotations

from ..diff_helpers import make_change
from ..model.call_interface import SYSV_AMD64
from ..model.callback_type_index import CallbackTypeIndex
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.declarations import Function
from ..model.entities import RecordType
from ..model.snapshot import AbiSnapshot
from .call_axis_shadow import abi_of
from .export_names import default_export_names
from .record_return_convention import return_convention


def _direct_result(record: RecordType) -> bool:
    if record.resolved_bases() or record.resolved_vtable():
        return False
    convention = return_convention(record.size_bits, record.fields)
    return bool(convention and convention[0] == "regs")


def callback_value_changes(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_functions: dict[str, Function],
    new_functions: dict[str, Function],
) -> list[Change]:
    before, after = CallbackTypeIndex(old), CallbackTypeIndex(new)
    transitions = {
        name
        for name, trait in before.traits.items()
        if trait is not None
        and after.traits.get(name) is not None
        and trait != after.traits[name]
    }
    if not transitions or abi_of(old) != SYSV_AMD64 or abi_of(new) != SYSV_AMD64:
        return []
    exports = (default_export_names(old) or set()) & (
        default_export_names(new) or set()
    )
    changes = []
    for symbol in sorted(exports & old_functions.keys() & new_functions.keys()):
        a, b = old_functions[symbol], new_functions[symbol]
        if len(a.params) != len(b.params):
            continue
        old_slots = before.slots(a)
        if not transitions.intersection(old_slots.values()):
            continue
        new_slots = after.slots(b)
        for (slot, is_result), name in old_slots.items():
            if name not in transitions or new_slots.get((slot, is_result)) != name:
                continue
            if is_result and not _direct_result(
                (before if before.traits[name] else after).records[name]
            ):
                continue
            changes.append(
                make_change(
                    ChangeKind.VALUE_ABI_TRAIT_CHANGED,
                    symbol=symbol,
                    entity_id=a.entity_id,
                    old=f"{slot}:"
                    + ("trivial" if before.traits[name] else "nontrivial"),
                    new=f"{slot}:"
                    + ("trivial" if after.traits[name] else "nontrivial"),
                    description=f"Passing {name} by value changed at {a.name} {slot}",
                )
            )
    return changes
