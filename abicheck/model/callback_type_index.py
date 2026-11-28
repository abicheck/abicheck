# SPDX-License-Identifier: Apache-2.0
"""Resolve named C++ record types in exported callback signatures and tables."""

from __future__ import annotations

import re

from .availability import FactStatus
from .declarations import Function
from .entities import RecordType
from .fact import Fact
from .signature_token_scan import _split_top_level_commas
from .snapshot import AbiSnapshot
from .type_label import qualified_spelling
from .type_spelling_resolve import scope_of, split_components
from .vocabulary import AccessLevel

# Ordinary function pointers. A member pointer
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
        self.ambiguous: set[str] = set()
        for record in declarations.types:
            name = record.qualified_name or record.name
            if name in self.traits:
                self.ambiguous.add(name)
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

    def _callback_parts(self, spelling: str, scope: str) -> tuple[dict[int, str], str]:
        signature, alias_scope = self.resolve(spelling, scope)
        match = _CALLBACK.fullmatch(signature)
        if not match:
            return {}, alias_scope
        parts = dict(enumerate(_split_top_level_commas(match[2])))
        if self.resolve(match[1], alias_scope)[0] in self.records:
            parts[-1] = match[1]
        return parts, alias_scope

    def arguments(self, spelling: str, scope: str) -> dict[int, str]:
        parts, owner = self._callback_parts(spelling, scope)
        return {i: self.resolve(part, owner)[0] for i, part in parts.items()}

    def _direct_record(
        self,
        spelling: str,
        identities: Fact[tuple[str, ...]] | None,
        scope: str,
        *,
        allow_spelling: bool = False,
    ) -> RecordType | None:
        direct = re.fullmatch(r"([^*&#()\[\]<>]+?)\s*(?:[*&]\s*(?:const)?)?", spelling)
        if not direct:
            return None
        name = self.resolve(direct[1], scope)[0]
        if identities is not None and identities.is_present:
            if identities.value != (name,):
                return None
        elif not allow_spelling or (
            identities is not None and identities.status != FactStatus.NOT_COLLECTED
        ):
            return None
        return None if name in self.ambiguous else self.records.get(name)

    def slots(self, function: Function) -> dict[tuple[str, bool], str]:
        scope = scope_of(qualified_spelling(function.entity_id) or function.name)
        slots = {}

        def add(path: str, spelling: str, owner: str) -> None:
            parts, outer_scope = self._callback_parts(spelling, owner)
            for index, part in parts.items():
                suffix = "return" if index == -1 else f"p{index}"
                endpoint = f"{path}.callback.{suffix}"
                name, _ = self.resolve(part, outer_scope)
                slots[endpoint, index == -1] = name
                # A registration callback may itself receive a typed callback.
                if index >= 0 and "(" in name:
                    for inner, value in self.arguments(part, outer_scope).items():
                        leaf = "return" if inner == -1 else f"p{inner}"
                        slots[f"{endpoint}.callback.{leaf}", inner == -1] = value

        endpoints = [
            ("return", function.return_type, function.return_type_identities_fact)
        ]
        endpoints.extend(
            (f"p{i}", p.type, p.type_identities_fact)
            for i, p in enumerate(function.params)
        )
        for path, spelling, identities in endpoints:
            add(path, spelling, scope)
            record = self._direct_record(spelling, identities, scope)
            if record is None:
                continue
            owner = record.qualified_name or record.name
            for field in record.fields:
                if field.access == AccessLevel.PRIVATE:
                    continue
                field_path = f"{path}.{field.name}"
                add(field_path, field.type, owner)
                # An interface may publish its operations through a separate table.
                table = self._direct_record(
                    field.type, field.type_identities_fact, owner, allow_spelling=True
                )
                if table is None or table is record:
                    continue
                for slot in table.fields:
                    if slot.access != AccessLevel.PRIVATE:
                        add(
                            f"{field_path}.{slot.name}",
                            slot.type,
                            table.qualified_name or table.name,
                        )
        return slots
