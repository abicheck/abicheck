# SPDX-License-Identifier: Apache-2.0
"""SysV AMD64 invisible-reference transitions inside typed callback parameters."""

from __future__ import annotations

import re

from ..diff_helpers import make_change
from ..model.call_interface import SYSV_AMD64
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.declarations import Function
from ..model.signature_token_scan import _split_top_level_commas
from ..model.snapshot import AbiSnapshot
from ..model.type_label import qualified_spelling
from ..model.type_spelling_resolve import scope_of, split_components
from ..model.vocabulary import AccessLevel
from .call_axis_shadow import abi_of
from .export_names import default_export_names
from .record_return_convention import return_convention

# Ordinary function pointers. A member pointer, a nested callback declarator
# or a template containing a callback needs its own type evidence.
_CALLBACK = re.compile(r"([^()<>&]+?)\s*\(\s*\*\s*\)\s*\((.*)\)\s*(?:noexcept)?")
_CV = re.compile(
    r"^(?:(?:const|volatile|class|struct)\s+)+|(?:\s+(?:const|volatile))+$"
)


class _CallbackTypes:
    def __init__(self, snap: AbiSnapshot) -> None:
        self.aliases = snap.declarations.typedefs_qualified
        self.traits: dict[str, bool | None] = {}
        for record in snap.declarations.types:
            name = record.qualified_name or record.name
            self.traits[name] = (
                record.trivial_for_calls if name not in self.traits else None
            )
        self.records = {r.qualified_name or r.name: r for r in snap.declarations.types}
        self.names = self.traits.keys() | self.aliases.keys()

    def resolve(self, spelling: str, scope: str) -> tuple[str, str]:
        seen: set[str] = set()
        while True:
            name = _CV.sub("", spelling.strip()).strip()
            if not name or "(" in name:
                return name, scope
            if name.startswith("::"):
                name = name[2:]
            else:
                parts = split_components(scope) if scope else []
                for depth in range(len(parts), 0, -1):
                    candidate = "::".join([*parts[:depth], name])
                    if candidate in self.names:
                        name = candidate
                        break
            if name not in self.aliases:
                return name, scope
            if name in seen:
                return "", scope
            seen.add(name)
            spelling, scope = self.aliases[name], scope_of(name)

    def arguments(self, spelling: str, scope: str) -> dict[int, str]:
        signature, alias_scope = self.resolve(spelling, scope)
        match = _CALLBACK.fullmatch(signature)
        if not match:
            return {}
        slots = {
            i: self.resolve(arg, alias_scope)[0]
            for i, arg in enumerate(_split_top_level_commas(match[2]))
        }
        result = self.resolve(match[1], alias_scope)[0]
        if result in self.records:
            slots[-1] = result
        return slots

    def slots(self, function: Function) -> dict[str, str]:
        scope = scope_of(qualified_spelling(function.entity_id) or function.name)
        slots = {}

        def add(path: str, spelling: str, owner: str) -> None:
            for index, name in self.arguments(spelling, owner).items():
                suffix = "return" if index == -1 else f"p{index}"
                slots[f"{path}.callback.{suffix}"] = name

        endpoints = [
            ("return", function.return_type, function.return_type_identities_fact)
        ]
        endpoints.extend(
            (f"p{i}", p.type, p.type_identities_fact)
            for i, p in enumerate(function.params)
        )
        for path, spelling, identities in endpoints:
            add(path, spelling, scope)
            if (
                identities is None
                or not identities.is_present
                or len(identities.value or ()) != 1
            ):
                continue
            owner = (identities.value or ())[0]
            # Only direct record values, pointers and references expose this table.
            direct = re.fullmatch(
                r"([^*&#()\[\]<>]+?)\s*(?:[*&]\s*(?:const)?)?", spelling
            )
            if not direct or self.resolve(direct[1], scope)[0] != owner:
                continue
            record = self.records.get(owner)
            if record:
                for field in record.fields:
                    if field.access != AccessLevel.PRIVATE:
                        add(f"{path}.{field.name}", field.type, owner)
        return slots

    def direct_result(self, name: str) -> bool:
        record = self.records[name]
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
    before, after = _CallbackTypes(old), _CallbackTypes(new)
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
        for slot, name in old_slots.items():
            if name not in transitions or new_slots.get(slot) != name:
                continue
            if slot.endswith(".return") and not (
                before if before.traits[name] else after
            ).direct_result(name):
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
