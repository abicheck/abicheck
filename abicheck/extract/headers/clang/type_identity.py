# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Resolve declaration type slots using the AST's scopes, aliases and using directives."""

from __future__ import annotations

import re
from typing import Any

from ....model import Fact
from .ast_inner import ast_inner
from .context import _Decl

_CV = re.compile(r"\b(?:const|volatile|restrict|struct|class|union|enum)\b")


def _unwrap_type(spelling: str) -> str:
    # Only outer declarator wrappers are transparent. Template arguments
    # (including their pointers and cv qualifiers) belong to the identity.
    parts = []
    depth = 0
    start = 0
    for i, ch in enumerate(spelling):
        if ch == "<":
            if depth == 0:
                parts.append(_CV.sub("", spelling[start:i]))
                start = i
            depth += 1
        elif ch == ">":
            depth -= 1
            if depth == 0:
                parts.append(spelling[start : i + 1])
                start = i + 1
    parts.append(_CV.sub("", re.sub(r"[&*]|\[[^\]]*\]", "", spelling[start:])))
    return " ".join("".join(parts).split())


class TypeIdentityIndex:
    """One parse-local index; ambiguous lookup never confirms a record identity."""

    def __init__(
        self,
        root: dict[str, Any],
        records: list[_Decl],
        enums: list[_Decl],
        aliases: list[_Decl],
    ):
        self.names = {
            "::".join((*e.scope, e.node["name"]))
            for e in (*records, *enums)
            if e.node.get("name")
        }
        self.aliases = {
            "::".join((*e.scope, e.node["name"])): e.node.get("type", {}).get(
                "desugaredQualType", e.node.get("type", {}).get("qualType", "")
            )
            for e in aliases
            if e.node.get("name")
        }
        self.known = self.names | self.aliases.keys()
        self.imports: dict[tuple[str, ...], list[str]] = {}
        self.namespace_ids: dict[str, str] = {}
        self._namespaces(root, ())
        self._usings(root, ())
        self.memo: dict[tuple[str, tuple[str, ...]], Fact[tuple[str, ...]]] = {}

    def _namespaces(self, node: dict[str, Any], scope: tuple[str, ...]) -> None:
        if node.get("kind") == "NamespaceDecl" and node.get("name"):
            scope = (*scope, node["name"])
            self.namespace_ids[node.get("id", "")] = "::".join(scope)
        for child in ast_inner(node):
            if isinstance(child, dict) and child.get("kind") in (
                "NamespaceDecl",
                "TranslationUnitDecl",
            ):
                self._namespaces(child, scope)

    def _usings(self, node: dict[str, Any], scope: tuple[str, ...]) -> None:
        if node.get("kind") == "NamespaceDecl" and node.get("name"):
            scope = (*scope, node["name"])
        if node.get("kind") == "UsingDirectiveDecl":
            target = node.get("nominatedNamespace", {})
            name = self.namespace_ids.get(target.get("id", ""))
            if name:
                self.imports.setdefault(scope, []).append(name)
        for child in ast_inner(node):
            if isinstance(child, dict) and child.get("kind") in (
                "NamespaceDecl",
                "TranslationUnitDecl",
                "UsingDirectiveDecl",
                "LinkageSpecDecl",
            ):
                self._usings(child, scope)

    def resolve(self, spelling: str, scope: tuple[str, ...]) -> Fact[tuple[str, ...]]:
        key = spelling, scope
        if key not in self.memo:
            self.memo[key] = self._resolve(spelling, scope, set())
        return self.memo[key]

    def _resolve(
        self, spelling: str, scope: tuple[str, ...], seen: set[str]
    ) -> Fact[tuple[str, ...]]:
        name = _unwrap_type(spelling)
        if not name or "(" in name or name in seen:
            return Fact.not_collected()
        seen = seen | {name}
        rooted = name.startswith("::")
        name = name.removeprefix("::")
        depths = (0,) if rooted else range(len(scope), -1, -1)
        for depth in depths:
            prefix = scope[:depth]
            candidates = ["::".join((*prefix, name))]
            if not rooted:
                candidates.extend(
                    f"{ns}::{name}" for ns in self.imports.get(prefix, ())
                )
            matches = set(candidates) & self.known
            if len(matches) > 1:
                return Fact.not_collected()
            if matches:
                found = next(iter(matches))
                if found in self.aliases:
                    return self._resolve(
                        self.aliases[found], tuple(found.split("::")[:-1]), seen
                    )
                return Fact.present((found,))
        return Fact.not_collected()
