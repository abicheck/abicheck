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

"""Resolve a clang base specifier to the record it names, whatever the clang
release.

A base specifier in ``-ast-dump=json`` carries only a type *spelling*
(``JSONNodeDumper::writeBases`` writes ``createQualType(Base.getType())``,
access, ``isVirtual``): no ``decl`` link to the base's ``CXXRecordDecl``,
on any release. What that spelling holds changed between releases:

* clang <= 21 wraps a written-qualified reference in an ``ElaboratedType``
  and the JSON dumper adds a ``desugaredQualType`` carrying the fully
  qualified name whenever it differs from the written one: ``class D :
  public detail::I`` inside ``namespace lib`` gives ``qualType:
  "detail::I"``, ``desugaredQualType: "lib::detail::I"``.
* clang 22 removed ``ElaboratedType``; a ``RecordType`` now prints with the
  qualifier it was written with, so the same base gives only ``qualType:
  "detail::I"`` -- no ``desugaredQualType`` at all -- and an alias base
  (``using A = detail::I; class D : A``) gives ``desugaredQualType:
  "detail::I"``, relative to the *alias's* scope. Confirmed against LLVM
  23.1.5 (``.github/workflows/clang-latest.yml``'s pinned leg).

Reading the spelling as a record key therefore stopped resolving relative
bases on clang 22+, and the vtable walk then read "no inherited virtuals" as
a confirmed fact. :class:`BaseResolver` resolves a spelling structurally:
first as an exact record key (the clang <= 21 desugared form, or a base
written fully qualified), then by C++ name lookup -- every name chain in the
spelling (the head and each template argument) is looked up from the scope
that wrote it, innermost enclosing scope first, the way
[basic.lookup.qual] finds a nested-name-specifier's first component. The
result is accepted only when it is exactly one record key; anything else
stays unresolved and the caller reports the vtable as incomplete rather than
empty.

Not handled, by design (each leaves the base unresolved, never guessed):
using-directives and inline-namespace members found through them, a base
named through a member typedef of a base class, and a dependent base of a
class template pattern.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ....name_classification import strip_anonymous_type_location

#: One ``a::b::C`` name chain (optionally ``::``-anchored), not preceded by
#: another identifier character or ``::`` so a match never starts mid-chain.
_CHAIN = re.compile(r"(?<![\w:])(?:::\s*)?[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*")
_SPACE_AROUND = re.compile(r"\s*(::|<|>|,|\*|&)\s*")


def normalize_spelling(text: str) -> str:
    """Whitespace-insensitive form: ``A< B ,C >`` and ``A<B, C>`` compare equal."""
    return _SPACE_AROUND.sub(r"\1", text.strip()).replace(",", ", ")


def split_scope_components(name: str) -> list[str]:
    """``a::B<c::D>::E`` -> ``["a", "B<c::D>", "E"]`` (template-aware)."""
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(name):
        ch = name[i]
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth = max(0, depth - 1)
        elif depth == 0 and name.startswith("::", i):
            parts.append(name[start:i])
            i += 2
            start = i
            continue
        i += 1
    parts.append(name[start:])
    return [p.strip() for p in parts if p.strip()]


def scope_of_qualname(qualname: str) -> tuple[str, ...]:
    """The enclosing scope a record key's own declaration sits in."""
    return tuple(split_scope_components(qualname)[:-1])


class BaseResolver:
    """Base-specifier spelling -> record key, over one TU's record index.

    *alias_scopes* maps a ``TypedefDecl``/``TypeAliasDecl`` clang ``id`` to
    the scope it was declared in, so an alias base's desugared spelling is
    looked up where the alias wrote it. It is called lazily, at most once,
    and only when an alias base needs it.
    """

    def __init__(
        self,
        record_keys: Iterable[str],
        alias_scopes: Callable[[], Mapping[str, tuple[str, ...]]] | None = None,
    ) -> None:
        keys = list(record_keys)
        self._keys = frozenset(keys)
        self._normalized = {normalize_spelling(k): k for k in keys}
        # Every scope a name chain's first component may name: each prefix of
        # each record key (namespaces and enclosing classes alike).
        self._scopes: set[str] = set()
        for key in keys:
            parts = split_scope_components(key)
            for i in range(1, len(parts) + 1):
                self._scopes.add("::".join(parts[:i]))
                head = parts[i - 1].split("<", 1)[0].strip()
                if head != parts[i - 1]:
                    self._scopes.add("::".join([*parts[: i - 1], head]))
        self._alias_scopes_fn = alias_scopes
        self._alias_scopes: Mapping[str, tuple[str, ...]] | None = None

    def _exact(self, spelling: str) -> str | None:
        if spelling in self._keys:
            return spelling
        return self._normalized.get(normalize_spelling(spelling))

    def _lookup_chain(self, chain: str, scope: tuple[str, ...]) -> str | None:
        """What C++ lookup of *chain* from *scope* names, if it names a known
        scope: the innermost enclosing scope declaring the chain's first
        component wins, and the rest of the chain must exist inside it."""
        chain = re.sub(r"\s+", "", chain)
        if chain.startswith("::"):
            chain = chain[2:]
            return chain if chain in self._scopes else None
        first = chain.split("::", 1)[0]
        for depth in range(len(scope), -1, -1):
            if "::".join([*scope[:depth], first]) in self._scopes:
                candidate = "::".join([*scope[:depth], chain])
                return candidate if candidate in self._scopes else None
        return None

    def _qualify(self, spelling: str, scope: tuple[str, ...]) -> str | None:
        """*spelling* with every name chain replaced by its lookup result, or
        ``None`` when the head chain names nothing known."""
        head_resolved = True

        def sub(m: re.Match[str]) -> str:
            nonlocal head_resolved
            found = self._lookup_chain(m.group(0), scope)
            if found is None:
                if m.start() == 0:
                    head_resolved = False
                return m.group(0)
            return found

        out = _CHAIN.sub(sub, spelling.strip())
        return out if head_resolved else None

    def _lookup(self, spelling: str, scope: tuple[str, ...]) -> str | None:
        qualified = self._qualify(spelling, scope)
        return self._exact(qualified) if qualified is not None else None

    def _alias_scope(self, alias_id: object) -> tuple[str, ...] | None:
        if not isinstance(alias_id, str) or self._alias_scopes_fn is None:
            return None
        if self._alias_scopes is None:
            self._alias_scopes = self._alias_scopes_fn()
        return self._alias_scopes.get(alias_id)

    def resolve(self, base: Mapping[str, Any], derived_qualname: str) -> str | None:
        """The record key *base* (one ``bases`` array entry of the record
        keyed *derived_qualname*) names, or ``None`` when none is proven."""
        type_obj = base.get("type")
        if not isinstance(type_obj, Mapping):
            return None
        desugared = strip_anonymous_type_location(
            str(type_obj.get("desugaredQualType") or "")
        )
        written = strip_anonymous_type_location(str(type_obj.get("qualType") or ""))
        derived_scope = scope_of_qualname(derived_qualname)
        for spelling in (desugared, written):
            if spelling and (found := self._exact(spelling)) is not None:
                return found
        if desugared:
            alias_scope = self._alias_scope(type_obj.get("typeAliasDeclId"))
            if alias_scope is not None:
                if (found := self._lookup(desugared, alias_scope)) is not None:
                    return found
            elif (found := self._lookup(desugared, derived_scope)) is not None:
                return found
        if written:
            return self._lookup(written, derived_scope)
        return None


class BaseLookupIndex(dict[str, dict[str, Any]]):
    """``record key -> node`` index that also owns its memoized
    :class:`BaseResolver`, so every vtable walk over the same index shares
    one resolver instead of rebuilding it per record."""

    def __init__(
        self,
        items: Mapping[str, dict[str, Any]],
        alias_scopes: Callable[[], Mapping[str, tuple[str, ...]]] | None = None,
    ) -> None:
        super().__init__(items)
        self._alias_scopes = alias_scopes
        self._resolver: BaseResolver | None = None

    def resolver(self) -> BaseResolver:
        if self._resolver is None:
            self._resolver = BaseResolver(self.keys(), self._alias_scopes)
        return self._resolver


def resolver_for(records_by_qualname: Mapping[str, dict[str, Any]]) -> BaseResolver:
    """The shared resolver of a :class:`BaseLookupIndex`, or a fresh one over
    a plain mapping (hand-built test indices)."""
    if isinstance(records_by_qualname, BaseLookupIndex):
        return records_by_qualname.resolver()
    return BaseResolver(records_by_qualname.keys())
