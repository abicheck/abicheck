# SPDX-License-Identifier: Apache-2.0
"""Resolve named C++ record types in exported callback signatures and tables."""

from __future__ import annotations

import re

from .declarations import Function
from .signature_token_scan import _split_top_level_commas
from .snapshot import AbiSnapshot
from .type_label import qualified_spelling
from .type_spelling_resolve import scope_of, split_components
from .vocabulary import AccessLevel

# Ordinary function pointers. A member pointer, a nested callback declarator
# or a template containing a callback needs its own type evidence.
_CALLBACK = re.compile(r"([^()<>&]+?)\s*\(\s*\*\s*\)\s*\((.*)\)\s*(?:noexcept)?")
_CV = re.compile(
    r"^(?:(?:const|volatile|class|struct)\s+)+|(?:\s+(?:const|volatile))+$"
)


class CallbackTypeIndex:
    def __init__(self, snap: AbiSnapshot) -> None:
        declarations = snap.declarations
        self.aliases = declarations.typedefs_qualified
        self.traits: dict[str, bool | None] = {}
        for record in declarations.types:
            name = record.qualified_name or record.name
            self.traits[name] = (
                record.trivial_for_calls if name not in self.traits else None
            )
        self.records = {r.qualified_name or r.name: r for r in declarations.types}
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

    def slots(self, function: Function) -> dict[tuple[str, bool], str]:
        scope = scope_of(qualified_spelling(function.entity_id) or function.name)
        slots = {}

        def add(path: str, spelling: str, owner: str) -> None:
            for index, name in self.arguments(spelling, owner).items():
                suffix = "return" if index == -1 else f"p{index}"
                slots[f"{path}.callback.{suffix}", index == -1] = name

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
