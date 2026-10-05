# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""Qualified labels for type findings whose bare name is ambiguous.

The header-AST backends store a record's or enum's *leaf* name in ``name``
(``Ctx``) and its scope separately (``qualified_name``, ``entity_id``). Type
detectors pair old/new declarations by qualified identity, so ``lib::m3::Ctx``
is diffed against ``lib::m3::Ctx`` -- but every finding they emit is labelled
with the bare ``name``. While that name is unique in the comparison the label
is unambiguous and is kept as is (it is what suppression rules, baselines and
golden reports already key on). When two or more distinct declarations share
it, the label names neither, and everything downstream that keys on the
label merges them: a change to ``lib::m3::Ctx`` was attributed to the
functions taking ``lib::m0::Ctx *`` too, and the root-type grouping that
folds derived findings picked whichever ``Ctx`` came first.

This module owns that one rule. :func:`ambiguous_type_spellings` finds the
bare names that collide across the two snapshots;
:func:`disambiguate_type_symbols` relabels a type finding whose bare symbol is
one of them with the qualified spelling of the declaration its own
``entity_id`` names. It changes nothing else: not the kind, not the verdict,
not a finding whose bare name is unique, and not one whose identity it cannot
resolve to a qualified spelling (an anonymous or function-local scope keeps
its bare label rather than receiving an invented one).

:func:`resolve_in_scope` is the companion for consumers that read type names
out of *signature spellings*, which the header backends also spell bare
(``Ctx*``): it resolves a bare ambiguous name the way C++ unqualified lookup
would from a given enclosing scope -- innermost scope outward.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..model import AbiSnapshot, Function
from ..model.identity import (
    EntityId,
    EntityKind,
    InlineNamespace,
    Namespace,
    Record,
)

__all__ = [
    "AmbiguousTypeNames",
    "ambiguous_type_spellings",
    "attribute_scoped_types",
    "disambiguate_type_symbols",
    "qualified_spelling",
    "function_scope",
    "record_scope",
    "resolve_in_scope",
    "scoped_type_mentions",
]

_TYPE_KINDS = frozenset({EntityKind.TYPE, EntityKind.ENUM, EntityKind.TYPEDEF})


def qualified_spelling(entity_id: EntityId | None) -> str | None:
    """``ns::Outer::Leaf`` for *entity_id*, or ``None`` when a scope segment
    has no spelling a reader could use (anonymous, function-local)."""
    if entity_id is None:
        return None
    parts: list[str] = []
    for seg in entity_id.scope:
        if isinstance(seg, (Namespace, Record, InlineNamespace)) and seg.name:
            parts.append(seg.name)
        else:
            return None
    parts.append(entity_id.leaf_name)
    return "::".join(parts)


@dataclass(frozen=True)
class AmbiguousTypeNames:
    """Bare type names shared by more than one declaration, with the
    qualified spelling of each declaration sharing it."""

    #: bare name -> qualified spellings of every declaration carrying it
    spellings: Mapping[str, frozenset[str]]
    #: entity identity -> its qualified spelling, for those declarations
    by_entity: Mapping[EntityId, str]

    def __bool__(self) -> bool:
        return bool(self.spellings)


def _declarations(snap: object) -> Iterable[object]:
    decls = snap.declarations  # type: ignore[attr-defined]
    yield from decls.types
    yield from decls.enums


def ambiguous_type_spellings(*snapshots: object | None) -> AmbiguousTypeNames:
    """The bare names that more than one distinct declaration carries across
    *snapshots* (``None`` entries are skipped).

    Counted across both sides together: a name unique on each side but naming
    a different declaration on each (``a::Ctx`` removed, ``b::Ctx`` added) is
    as ambiguous to a reader of the combined report as two on one side.
    Spellings come from each declaration's ``entity_id`` first and its
    ``qualified_name`` second; a declaration with neither cannot be told
    apart from its namesakes, so it never makes a name ambiguous -- the label
    stays what it was.
    """
    spellings: dict[str, set[str]] = {}
    entities: dict[EntityId, str] = {}
    for snap in snapshots:
        if snap is None:
            continue
        for decl in _declarations(snap):
            bare = getattr(decl, "name", "") or ""
            if not bare or "::" in bare:
                # A namespace-baked name (DWARF) is already its own label.
                continue
            eid = getattr(decl, "entity_id", None)
            qualified = qualified_spelling(eid) or getattr(decl, "qualified_name", None)
            if not qualified:
                continue
            spellings.setdefault(bare, set()).add(qualified)
            if eid is not None:
                entities[eid] = qualified
    ambiguous = {b: frozenset(q) for b, q in spellings.items() if len(q) > 1}
    return AmbiguousTypeNames(
        spellings=ambiguous,
        by_entity={
            e: q
            for e, q in entities.items()
            if e.leaf_name in ambiguous and q in ambiguous[e.leaf_name]
        },
    )


def _relabel(text: str, bare: str, qualified: str) -> str:
    # Whole-token occurrences only: not inside a longer identifier and not
    # already preceded by a scope (``x::Ctx`` stays as written).
    return re.sub(rf"(?<![\w:]){re.escape(bare)}(?!\w)", qualified, text)


def disambiguate_type_symbols(
    changes: Iterable[object], names: AmbiguousTypeNames
) -> int:
    """Relabel, in place, every type finding whose bare symbol is ambiguous.

    A finding qualifies when its ``entity_id`` is a type/enum/typedef identity
    *names* knows, and its symbol is that identity's bare leaf name, or the
    leaf followed by ``::member``. Its ``symbol`` and the matching tokens of
    its ``description`` are rewritten to the qualified spelling, and
    ``qualified_name`` is set. Returns how many findings were relabelled.
    """
    if not names:
        return 0
    relabelled = 0
    for c in changes:
        eid = getattr(c, "entity_id", None)
        if eid is None or eid.kind not in _TYPE_KINDS:
            continue
        qualified = names.by_entity.get(eid)
        if qualified is None:
            continue
        bare = eid.leaf_name
        symbol = c.symbol  # type: ignore[attr-defined]
        if symbol == bare:
            new_symbol = qualified
        elif symbol.startswith(bare + "::"):
            new_symbol = qualified + symbol[len(bare) :]
        else:
            continue
        c.symbol = new_symbol  # type: ignore[attr-defined]
        if getattr(c, "qualified_name", None) is None:
            c.qualified_name = qualified  # type: ignore[attr-defined]
        description = getattr(c, "description", None)
        if description:
            c.description = _relabel(description, bare, qualified)  # type: ignore[attr-defined]
        relabelled += 1
    return relabelled


def resolve_in_scope(
    name: str, scope: Iterable[str], names: AmbiguousTypeNames
) -> str | None:
    """The qualified spelling C++ unqualified lookup would find for *name*
    (bare, or partially qualified like ``m0::Ctx``) from inside *scope*
    (outermost-first segments), among the declarations *names* records for
    its leaf; ``None`` when the leaf is not ambiguous or no enclosing scope
    declares it.

    Innermost scope first, then each enclosing one, ending at the global
    scope -- from ``lib::m0::Svc``, ``Ctx`` is ``lib::m0::Svc::Ctx`` if that
    exists, else ``lib::m0::Ctx``, else ``lib::Ctx``, else ``Ctx``. Using
    declarations and ADL are not modelled; a name brought in that way
    resolves to nothing rather than to a wrong declaration.
    """
    candidates = names.spellings.get(name.rsplit("::", 1)[-1])
    if not candidates:
        return None
    segments = list(scope)
    for depth in range(len(segments), -1, -1):
        spelled = "::".join([*segments[:depth], name])
        if spelled in candidates:
            return spelled
    return None


_NAME_TOKEN = re.compile(r"(?<![\w:])(?:::)?[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*")


def scoped_type_mentions(
    spelling: str, scope: Iterable[str], names: AmbiguousTypeNames
) -> set[str]:
    """The ambiguous declarations *spelling* (a parameter, return or field
    type) names, each resolved from *scope* by :func:`resolve_in_scope`."""
    if not names or not spelling:
        return set()
    segments = tuple(scope)
    out: set[str] = set()
    for match in _NAME_TOKEN.finditer(spelling):
        token = match.group(0).lstrip(":")
        if token.rsplit("::", 1)[-1] not in names.spellings:
            continue
        resolved = resolve_in_scope(token, segments, names)
        if resolved is not None:
            out.add(resolved)
    return out


def _split_top_level(text: str, sep: str = "::") -> list[str]:
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        elif depth == 0 and text.startswith(sep, i):
            parts.append(text[start:i])
            i += len(sep)
            start = i
            continue
        i += 1
    parts.append(text[start:])
    return parts


def _head(qualified: str) -> str:
    depth = 0
    for i, ch in enumerate(qualified):
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "(" and depth == 0:
            return qualified[:i]
    return qualified


_SYNTHETIC_PREFIXES = ("__abicheck_ctor__", "~")


def function_scope(name: str, mangled: str) -> tuple[str, ...]:
    """The scope a function's signature spellings are looked up from: its
    qualified name minus the leaf (``lib::m0::Svc::run`` -> ``lib``, ``m0``,
    ``Svc``). A synthetic constructor/destructor key already names the class
    (``__abicheck_ctor__lib::m0::Ctx(...)``), which *is* the member scope.
    Empty when nothing names a scope (a C function)."""
    from .template_surface import qualified_declaration_name

    for prefix in _SYNTHETIC_PREFIXES:
        if mangled.startswith(prefix):
            return tuple(_split_top_level(_head(mangled[len(prefix) :]).strip()))
    text = qualified_declaration_name(name, mangled) if mangled else name
    return tuple(_split_top_level(_head(text).strip())[:-1])


def record_scope(decl: object) -> tuple[str, ...]:
    """The scope a record's field spellings are looked up from: the record's
    own qualified path (members see the record's own nested names first)."""
    qualified = (
        qualified_spelling(getattr(decl, "entity_id", None))
        or getattr(decl, "qualified_name", None)
        or getattr(decl, "name", "")
    )
    return tuple(_split_top_level(qualified or ""))


def attribute_scoped_types(
    affected_types: set[str],
    old: AbiSnapshot,
    new: AbiSnapshot | None,
    old_pub: Mapping[str, Function],
    type_to_funcs: dict[str, set[str]],
    type_to_mangled: dict[str, set[str]],
    type_embeds: dict[str, set[str]],
) -> None:
    """Attribute the affected types :func:`disambiguate_type_symbols`
    labelled with a qualified spelling, by scope rather than by substring.

    ``diff_filtering``'s impact attribution matches an affected type's label
    as a substring of each signature and field spelling. The header backends
    spell those bare (``Ctx*``), so a qualified label matches nothing, and the
    bare label it replaced matched every namesake. Each bare mention is
    resolved here the way C++ lookup resolves it from the function's or the
    record's own scope instead, and recorded into the same three maps.
    """
    names = ambiguous_type_spellings(old, new)
    scoped = {k for k in affected_types if k in names.by_entity.values()}
    if not scoped:
        return
    for func in old_pub.values():
        scope = function_scope(func.name, func.mangled)
        refs = [func.return_type, *(p.type for p in func.params)]
        for ref in refs:
            for key in scoped_type_mentions(ref, scope, names) & scoped:
                type_to_funcs.setdefault(key, set()).add(func.name)
                type_to_mangled.setdefault(key, set()).add(func.mangled)
    for t in old.declarations.types:
        scope = record_scope(t)
        for fld in t.fields:
            for key in scoped_type_mentions(fld.type, scope, names) & scoped:
                type_embeds.setdefault(key, set()).add(t.name)
