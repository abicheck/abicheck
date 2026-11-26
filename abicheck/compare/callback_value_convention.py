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
from .call_axis_shadow import abi_of
from .export_names import default_export_names

# Ordinary function pointers. A member pointer, a nested callback declarator
# or a template containing a callback needs its own type evidence.
_CALLBACK = re.compile(r"[^()<>&]+?\s*\(\s*\*\s*\)\s*\((.*)\)\s*(?:noexcept)?")
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
        return {
            i: self.resolve(arg, alias_scope)[0]
            for i, arg in enumerate(_split_top_level_commas(match[1]))
        }


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
        old_scope = scope_of(qualified_spelling(a.entity_id) or a.name)
        new_scope = scope_of(qualified_spelling(b.entity_id) or b.name)
        for i, (left, right) in enumerate(zip(a.params, b.params, strict=True)):
            old_args = before.arguments(left.type, old_scope)
            if not transitions.intersection(old_args.values()):
                continue
            new_args = after.arguments(right.type, new_scope)
            for j, name in old_args.items():
                if name not in transitions or new_args.get(j) != name:
                    continue
                slot = f"p{i}.callback.p{j}"
                changes.append(
                    make_change(
                        ChangeKind.VALUE_ABI_TRAIT_CHANGED,
                        symbol=symbol,
                        entity_id=a.entity_id,
                        old=f"{slot}:"
                        + ("trivial" if before.traits[name] else "nontrivial"),
                        new=f"{slot}:"
                        + ("trivial" if after.traits[name] else "nontrivial"),
                        description=f"Passing {name} by value changed for callback argument {j} of {a.name} parameter {i}",
                    )
                )
    return changes
