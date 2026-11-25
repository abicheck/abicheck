# SPDX-License-Identifier: Apache-2.0
"""Function-level changes to C++ record parameter passing under SysV AMD64."""

from __future__ import annotations

from ..diff_helpers import make_change
from ..model.call_interface import SYSV_AMD64
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.snapshot import AbiSnapshot
from .call_axis_shadow import abi_of
from .export_names import default_export_names


def value_parameter_changes(old: AbiSnapshot, new: AbiSnapshot) -> list[Change]:
    if abi_of(old) != SYSV_AMD64 or abi_of(new) != SYSV_AMD64:
        return []
    old_functions, new_functions = old.function_map, new.function_map
    changes = []
    for symbol in sorted(
        (default_export_names(old) or set()) & (default_export_names(new) or set())
    ):
        a, b = old_functions.get(symbol), new_functions.get(symbol)
        if a is None or b is None or len(a.params) != len(b.params):
            continue
        before, after = [], []
        for i, (left, right) in enumerate(zip(a.params, b.params, strict=True)):
            x, y = left.trivial_for_calls_fact, right.trivial_for_calls_fact
            if not (
                x is not None
                and y is not None
                and x.is_present
                and y.is_present
                and type(x.value) is bool
                and type(y.value) is bool
            ):
                continue
            old_id, new_id = left.type_identities_fact, right.type_identities_fact
            if (
                x.value == y.value
                or old_id is None
                or new_id is None
                or not old_id.is_present
                or not new_id.is_present
                or not old_id.value
                or old_id.value != new_id.value
            ):
                continue
            before.append(f"p{i}:" + ("trivial" if x.value else "nontrivial"))
            after.append(f"p{i}:" + ("trivial" if y.value else "nontrivial"))
        if before:
            # The DWARF detector owns a function when both sides carry its
            # fingerprint. It also handles any simultaneous return transition.
            if (
                old.declarations.debug_advanced
                and new.declarations.debug_advanced
                and symbol in old.declarations.debug_advanced.value_abi_traits
                and symbol in new.declarations.debug_advanced.value_abi_traits
            ):
                continue
            changes.append(
                make_change(
                    ChangeKind.VALUE_ABI_TRAIT_CHANGED,
                    symbol=symbol,
                    entity_id=a.entity_id,
                    old="|".join(before),
                    new="|".join(after),
                    description=f"C++ by-value parameter passing changed for {a.name}: "
                    f"{', '.join(before)} → {', '.join(after)}",
                )
            )
    return changes
