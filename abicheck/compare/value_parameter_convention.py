# SPDX-License-Identifier: Apache-2.0
"""Function-level changes to C++ record parameter passing under SysV AMD64."""

from __future__ import annotations

from ..diff_helpers import make_change
from ..model.call_interface import SYSV_AMD64
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.declarations import Function
from ..model.dwarf_calling_conventions import named_convention
from ..model.snapshot import AbiSnapshot
from ..model.special_member_identity import resolve_special_member_linker_names
from ..model.synthetic_key import is_synthetic_ctor_key
from .call_axis_shadow import abi_of
from .export_names import default_export_names


def value_parameter_changes(
    old: AbiSnapshot,
    new: AbiSnapshot,
    old_functions: dict[str, Function],
    new_functions: dict[str, Function],
) -> list[Change]:
    if not any(
        p.trivial_for_calls_fact is not None
        for f in old_functions.values()
        for p in f.params
    ):
        return []
    changes = []
    exports = (default_export_names(old) or set()) & (
        default_export_names(new) or set()
    )
    old_functions = _with_constructor_params(old, old_functions, exports)
    new_functions = _with_constructor_params(new, new_functions, exports)
    for symbol in sorted(exports):
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
            if abi_of(old) != SYSV_AMD64 or abi_of(new) != SYSV_AMD64:
                return []
            # DWARF often knows only the return or an unrelated argument.
            # Defer only when it independently records the same transition.
            old_debug = old.declarations.debug_advanced
            new_debug = new.declarations.debug_advanced
            if old_debug and new_debug:
                dwarf_before = old_debug.value_abi_traits.get(symbol, "").split("|")
                dwarf_after = new_debug.value_abi_traits.get(symbol, "").split("|")
                old_cc = old_debug.calling_conventions.get(symbol)
                new_cc = new_debug.calling_conventions.get(symbol)
                excluded = (
                    old_cc is not None
                    and new_cc is not None
                    and named_convention(old_cc) != named_convention(new_cc)
                )
                if (
                    not excluded
                    and set(before).issubset(dwarf_before)
                    and set(after).issubset(dwarf_after)
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


def _with_constructor_params(
    snap: AbiSnapshot, functions: dict[str, Function], exports: set[str]
) -> dict[str, Function]:
    constructors = [
        f for f in snap.declarations.functions if is_synthetic_ctor_key(f.mangled)
    ]
    if not any(
        p.trivial_for_calls_fact is not None for f in constructors for p in f.params
    ):
        return functions
    result = dict(functions)
    resolved = resolve_special_member_linker_names(
        constructors,
        default_export_names(snap) or set(),
        snap.declarations.typedefs_qualified,
    )
    for index, names in resolved.items():
        for symbol in (names.canonical, *names.variants):
            if symbol in exports and not getattr(result.get(symbol), "params", None):
                result[symbol] = constructors[index]
    return result
