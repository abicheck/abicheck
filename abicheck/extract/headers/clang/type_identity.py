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
#: Marks a name that matches two declarations at one depth (never confirmed).
_AMBIGUOUS = "\0ambiguous"
#: Builtin type words: a return of only these is a scalar, never a record.
_BUILTIN_WORDS = frozenset(
    {
        "void", "bool", "_Bool", "char", "wchar_t", "char8_t", "char16_t",
        "char32_t", "short", "int", "long", "float", "double", "signed", "unsigned",
    }
)  # fmt: skip


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


def record_call_traits(root: dict[str, Any]) -> dict[str, bool]:
    """Clang's call-triviality decision on complete, named C++ definitions.

    Stay within namespace and record scopes. A local class or a template
    pattern cannot be joined by the qualified name of an ordinary record.
    """
    traits: dict[str, bool] = {}
    ambiguous: set[str] = set()

    def walk(node: dict[str, Any], scope: tuple[str, ...]) -> None:
        for child in ast_inner(node):
            if not isinstance(child, dict):
                continue
            kind = child.get("kind")
            name = child.get("name")
            if kind == "LinkageSpecDecl":
                walk(child, scope)
            if not name or kind not in ("NamespaceDecl", "CXXRecordDecl"):
                continue
            path = (*scope, name)
            if kind == "CXXRecordDecl":
                data = child.get("definitionData")
                if not child.get("completeDefinition") or not isinstance(data, dict):
                    continue
                key = "::".join(path)
                trait = data.get("canPassInRegisters", False)
                # Require affirmative special-member evidence on the indirect
                # side: an AST producer omitting the flag alone proves nothing.
                if trait is not True and not any(
                    data.get(slot, {}).get("nonTrivial")
                    for slot in ("copyCtor", "moveCtor", "dtor")
                ):
                    continue
                if key in traits:
                    ambiguous.add(key)
                traits[key] = trait is True
            walk(child, path)

    walk(root, ())
    return {k: v for k, v in traits.items() if k not in ambiguous}


class TypeIdentityIndex:
    """One parse-local index; ambiguous lookup never confirms a record identity."""

    def __init__(
        self,
        root: dict[str, Any],
        records: list[_Decl],
        enums: list[_Decl],
        aliases: list[_Decl],
    ):
        self.call_traits = record_call_traits(root)
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
        self.record_names = {
            "::".join((*e.scope, e.node["name"])) for e in records if e.node.get("name")
        }
        self.enum_names = {
            "::".join((*e.scope, e.node["name"])) for e in enums if e.node.get("name")
        }
        self.imports: dict[tuple[str, ...], list[str]] = {}
        self.namespace_ids: dict[str, str] = {}
        self._namespaces(root, ())
        self._usings(root, ())
        self.memo: dict[tuple[str, tuple[str, ...]], Fact[tuple[str, ...]]] = {}

    def _namespaces(self, node: dict[str, Any], scope: tuple[str, ...]) -> None:
        # Same shape contract as the parser's own walk (dumper_clang._walk):
        # a non-object node holds no declaration to index.
        if not isinstance(node, dict):
            return
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
        # Same shape contract as the parser's own walk (dumper_clang._walk):
        # a non-object node holds no declaration to index.
        if not isinstance(node, dict):
            return
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
        found = self._match_name(name, scope)
        if found is None or found is _AMBIGUOUS:
            return Fact.not_collected()
        if found in self.aliases:
            return self._resolve(
                self.aliases[found], tuple(found.split("::")[:-1]), seen | {name}
            )
        return Fact.present((found,))

    def _match_name(self, name: str, scope: tuple[str, ...]) -> str | None:
        """The one declaration *name* means from *scope*, ``_AMBIGUOUS`` when
        two match at the same depth, or ``None`` when none does."""
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
                return _AMBIGUOUS
            if matches:
                return next(iter(matches))
        return None

    def parameter_call_trait(
        self, spelling: str, scope: tuple[str, ...]
    ) -> Fact[bool] | None:
        identities = self.resolve(spelling, scope).value
        if not identities or identities[0] not in self.call_traits:
            return None
        if self.by_value_fact(spelling, scope).value is not True:
            return None
        return Fact.present(self.call_traits[identities[0]])

    def by_value_fact(self, spelling: str, scope: tuple[str, ...]) -> Fact[bool]:
        """Whether *spelling* is a record returned by value (``Function.return_by_value_fact``).

        Read from the declared type, not the name: a pointer or reference
        declarator (the desugared return ``QualType``'s own suffix, taken before
        any alias is looked through) is not by value, and a typedef is followed to
        its target. A name that resolves to a record declaration is by value; one
        that resolves to an enumeration or a builtin is not. Anything else (an
        unresolved or ambiguous name, a function type, a template instance) is
        undetermined, so the fact is not collected.
        """
        answer = self._by_value(spelling, scope, frozenset())
        return Fact.not_collected() if answer is None else Fact.present(answer)

    def _by_value(
        self, spelling: str, scope: tuple[str, ...], seen: frozenset[str]
    ) -> bool | None:
        text = " ".join(_CV.sub(" ", spelling).split())
        if not text or "(" in text:
            return None
        if text.endswith(("*", "&")):
            return False
        if set(text.split()) <= _BUILTIN_WORDS:
            return False
        if text in seen:
            return None
        found = self._match_name(text, scope)
        if found is None or found is _AMBIGUOUS:
            return None
        if found in self.aliases:
            return self._by_value(
                self.aliases[found],
                tuple(found.split("::")[:-1]),
                seen | {text},
            )
        if found in self.record_names:
            return True
        if found in self.enum_names:
            return False
        return None
