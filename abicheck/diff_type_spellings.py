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

from .model import AbiSnapshot, Function, Visibility


@dataclass(frozen=True)
class TypeSlotChange:
    """A single public type slot whose spelling changed between versions."""

    symbol: str  # owning function/type name for reporting
    slot: str  # human description, e.g. "return type", "parameter 'n'", "field 'buf'"
    old_type: str
    new_type: str


def iter_type_slot_changes(old: AbiSnapshot, new: AbiSnapshot) -> Iterator[TypeSlotChange]:
    """Yield every public function/field type slot whose spelling changed.

    Matching is by mangled name (functions) and type name (records); only slots
    present in both versions with a differing spelling are yielded.
    """
    def _differ(a: object, b: object) -> bool:
        # Only compare plain string spellings; guard against None/other shapes
        # so a malformed snapshot cannot crash (and thus disable) the caller.
        return isinstance(a, str) and isinstance(b, str) and a != b

    def _emit(of: Function, nf: Function) -> Iterator[TypeSlotChange]:
        if _differ(of.return_type, nf.return_type):
            yield TypeSlotChange(of.name, "return type", of.return_type, nf.return_type)
        for op, npm in zip(of.params, nf.params):
            if _differ(op.type, npm.type):
                pname = op.name or npm.name or "?"
                yield TypeSlotChange(of.name, f"parameter '{pname}'", op.type, npm.type)

    # ── Functions: return type + positional parameters ──────────────────────
    old_fns = {f.mangled: f for f in old.functions if f.visibility == Visibility.PUBLIC}
    new_fns = {f.mangled: f for f in new.functions if f.visibility == Visibility.PUBLIC}
    matched_new: set[str] = set()
    for key in set(old_fns) & set(new_fns):
        matched_new.add(key)
        yield from _emit(old_fns[key], new_fns[key])

    # Fallback: a type migration such as char->char8_t, ->_BitInt(N), or an
    # _Atomic qualifier change *alters the mangled name itself* (e.g. PKc->PKDu),
    # so the two symbols never share a mangled key and the pass above misses
    # them (Codex review P2). Pair any leftover functions by their demangled
    # name, but only when that name is unambiguous on both sides (exactly one
    # unmatched old and one unmatched new) so overload sets are never mispaired.
    leftover_old = [f for k, f in old_fns.items() if k not in set(new_fns)]
    leftover_new = [f for k, f in new_fns.items() if k not in matched_new]
    if leftover_old and leftover_new:
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
                yield from _emit(olist[0], nlist[0])

    # ── Record types: field spellings ───────────────────────────────────────
    old_types = {t.name: t for t in old.types}
    new_types = {t.name: t for t in new.types}
    for name in set(old_types) & set(new_types):
        ot, nt = old_types[name], new_types[name]
        new_fields = {f.name: f for f in nt.fields}
        for ofield in ot.fields:
            nfield = new_fields.get(ofield.name)
            if nfield is not None and _differ(ofield.type, nfield.type):
                yield TypeSlotChange(name, f"field '{ofield.name}'", ofield.type, nfield.type)
