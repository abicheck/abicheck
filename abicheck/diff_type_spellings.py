# Copyright 2026 Nikolay Petrov
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

"""Shared helper for token-based type-spelling change detectors.

Several modern-C++/C ABI hazards are detected the same way: a public type
*spelling* changes such that a distinctive token appears in exactly one of the
old/new spellings (or, for parameterised tokens, with different arguments).
Examples: ``char8_t`` (C++20), ``_BitInt(N)`` (C23), ``_Atomic(T)`` (C11).

This module walks every comparable public type slot — function return types,
function parameter types, and struct/class/union field types — and yields the
old/new spelling pairs that differ, so each specialised detector only has to
recognise its own token.
"""
from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from .model import (
    AbiSnapshot,
    Function,
    Visibility,
    is_abi_surface_type_name,
    stdlib_namespaces_excluded,
)


@dataclass(frozen=True)
class TypeSlotChange:
    """A single public type slot whose spelling changed between versions."""

    symbol: str  # owning function/type name for reporting
    slot: str  # human description, e.g. "return type", "parameter 'n'", "field 'buf'"
    old_type: str
    new_type: str


def _spelling_differ(a: object, b: object) -> bool:
    # Only compare plain string spellings; guard against None/other shapes
    # so a malformed snapshot cannot crash (and thus disable) the caller.
    return isinstance(a, str) and isinstance(b, str) and a != b


def _emit_function_slot_changes(of: Function, nf: Function) -> Iterator[TypeSlotChange]:
    """Yield changed return-type / parameter-type slots for a matched pair."""
    if _spelling_differ(of.return_type, nf.return_type):
        yield TypeSlotChange(of.name, "return type", of.return_type, nf.return_type)
    for op, npm in zip(of.params, nf.params):
        if _spelling_differ(op.type, npm.type):
            pname = op.name or npm.name or "?"
            yield TypeSlotChange(of.name, f"parameter '{pname}'", op.type, npm.type)


def _pair_leftover_functions_by_name(
    leftover_old: list[Function], leftover_new: list[Function]
) -> Iterator[TypeSlotChange]:
    """Pair functions whose mangled name changed, by unambiguous demangled name.

    A type migration such as char->char8_t, ->_BitInt(N), or an _Atomic
    qualifier change *alters the mangled name itself* (e.g. PKc->PKDu), so the
    two symbols never share a mangled key and the primary pass misses them
    (Codex review P2). Pair leftover functions by their demangled name, but only
    when that name is unambiguous on both sides (exactly one unmatched old and
    one unmatched new) so overload sets are never mispaired.
    """
    from collections import defaultdict

    old_by_name: dict[str, list[Function]] = defaultdict(list)
    for f in leftover_old:
        if f.name:
            old_by_name[f.name].append(f)
    new_by_name: dict[str, list[Function]] = defaultdict(list)
    for f in leftover_new:
        if f.name:
            new_by_name[f.name].append(f)
    for nm, olist in old_by_name.items():
        nlist = new_by_name.get(nm)
        if len(olist) == 1 and nlist and len(nlist) == 1:
            yield from _emit_function_slot_changes(olist[0], nlist[0])


def _match_functions_by_mangled(
    old: AbiSnapshot, new: AbiSnapshot
) -> Iterator[TypeSlotChange]:
    """Yield slot changes for public functions sharing a mangled name, plus
    pair leftovers (mangled name changed) by unambiguous demangled name."""
    old_fns = {f.mangled: f for f in old.functions if f.visibility == Visibility.PUBLIC}
    new_fns = {f.mangled: f for f in new.functions if f.visibility == Visibility.PUBLIC}
    matched_new: set[str] = set()
    for key in set(old_fns) & set(new_fns):
        matched_new.add(key)
        yield from _emit_function_slot_changes(old_fns[key], new_fns[key])

    # Fallback: pair functions whose mangled name changed (char8_t/_BitInt/_Atomic).
    # Precompute the new-key set once — rebuilding it inside the comprehension
    # is O(functions) per element, i.e. quadratic.
    new_keys = set(new_fns)
    leftover_old = [f for k, f in old_fns.items() if k not in new_keys]
    leftover_new = [f for k, f in new_fns.items() if k not in matched_new]
    if leftover_old and leftover_new:
        yield from _pair_leftover_functions_by_name(leftover_old, leftover_new)


def _match_record_fields(
    old: AbiSnapshot, new: AbiSnapshot
) -> Iterator[TypeSlotChange]:
    """Yield field-spelling changes for record types present in both snapshots."""
    excl = stdlib_namespaces_excluded(old, new)
    old_types = {t.name: t for t in old.types if is_abi_surface_type_name(t.name, exclude_stdlib=excl)}
    new_types = {t.name: t for t in new.types if is_abi_surface_type_name(t.name, exclude_stdlib=excl)}
    for name in set(old_types) & set(new_types):
        nt = new_types[name]
        new_fields = {f.name: f for f in nt.fields}
        for ofield in old_types[name].fields:
            nfield = new_fields.get(ofield.name)
            if nfield is not None and _spelling_differ(ofield.type, nfield.type):
                yield TypeSlotChange(name, f"field '{ofield.name}'", ofield.type, nfield.type)


def iter_type_slot_changes(old: AbiSnapshot, new: AbiSnapshot) -> Iterator[TypeSlotChange]:
    """Yield every public function/field type slot whose spelling changed.

    Matching is by mangled name (functions) and type name (records); only slots
    present in both versions with a differing spelling are yielded.
    """
    yield from _match_functions_by_mangled(old, new)
    yield from _match_record_fields(old, new)
