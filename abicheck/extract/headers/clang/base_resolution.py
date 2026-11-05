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
  "detail::I"`` -- no ``desugaredQualType`` at all -- and an alias or
  typedef base gives ``desugaredQualType`` as the target was written in the
  *innermost* alias of its ``typeAliasDeclId`` chain (``using B = a::A;``
  over ``using A = detail::I;`` desugars to ``detail::I``, relative to
  ``A``'s scope). Confirmed against LLVM 23.1.5
  (``.github/workflows/clang-latest.yml``'s pinned leg).

:class:`BaseResolver` therefore resolves structurally, in this order:

1. a non-alias ``desugaredQualType`` is clang's canonical, fully qualified
   spelling (clang 22 writes one only for that), so it is taken as an exact
   record key;
2. an alias base's ``desugaredQualType`` is looked up from the scope of the
   innermost alias in its chain, then tried as an exact key (clang <= 21
   wrote it fully qualified);
3. the written ``qualType`` is resolved only by C++ name lookup from the
   derived record's enclosing scopes, never as an exact key first: ``I``
   written inside ``namespace lib`` names ``lib::I`` when that exists, even
   if a global ``I`` does too.

Lookup replaces every name chain in the spelling (the head and each
template argument) by what [basic.lookup.unqual]/[basic.lookup.qual] find:
the innermost enclosing scope declaring the chain's first component wins,
and the rest of the chain must exist inside it. A chain whose first
component is a template parameter of the derived record (or of a template
enclosing it) is dependent and never resolves, so ``template <class T>
struct D : T`` stays unresolved even when some record ``T`` exists. The
result is accepted only when it is exactly one record key; anything else
stays unresolved and the caller reports the vtable as incomplete rather
than empty.

Not handled, by design (each leaves the base unresolved, never guessed):
using-directives and inline-namespace members found through them, and a
base named through a member typedef of a base class.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ....name_classification import strip_anonymous_type_location

#: One ``a::b::C`` name chain (optionally ``::``-anchored). Not preceded by
#: an identifier character, ``:`` or ``>``, so a match never starts
#: mid-chain -- ``Outer<int>::Inner`` yields ``Outer`` and ``int`` only, and
#: ``::Inner`` there is not misread as a global-scope reference. The first
#: character of each component is any Unicode letter or ``_`` (C++
#: identifiers may be non-ASCII).
_CHAIN = re.compile(r"(?<![\w:>])(?:::\s*)?[^\W\d]\w*(?:\s*::\s*[^\W\d]\w*)*")
_SPACE_AROUND = re.compile(r"\s*(::|<|>|,|\*|&)\s*")

#: ``alias id -> (declaring scope, the next alias id its target names)``.
AliasIndex = Mapping[str, tuple[tuple[str, ...], str | None]]
#: ``record key -> template parameter names in scope at its declaration``.
TemplateParamIndex = Mapping[str, frozenset[str]]


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

    *aliases* and *template_params* are called lazily, at most once each,
    and only when a base needs them (see :data:`AliasIndex`,
    :data:`TemplateParamIndex`).
    """

    def __init__(
        self,
        record_keys: Iterable[str],
        aliases: Callable[[], AliasIndex] | None = None,
        template_params: Callable[[], TemplateParamIndex] | None = None,
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
        self._aliases_fn = aliases
        self._aliases: AliasIndex | None = None
        self._tparams_fn = template_params
        self._tparams: TemplateParamIndex | None = None

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

    def _lookup(
        self, spelling: str, scope: tuple[str, ...], dependent: frozenset[str]
    ) -> str | None:
        """The record key C++ lookup of every name chain in *spelling* from
        *scope* yields, or ``None`` when the head names nothing known or any
        chain starts with a template parameter in *dependent*."""
        ok = True

        def sub(m: re.Match[str]) -> str:
            nonlocal ok
            chain = m.group(0)
            if re.sub(r"\s+", "", chain).split("::", 1)[0] in dependent:
                ok = False
                return chain
            found = self._lookup_chain(chain, scope)
            if found is None:
                if m.start() == 0:
                    ok = False
                return chain
            return found

        qualified = _CHAIN.sub(sub, spelling.strip())
        return self._exact(qualified) if ok else None

    def _innermost_alias_scope(self, alias_id: object) -> tuple[str, ...] | None:
        """The scope of the last alias in *alias_id*'s chain: where the
        desugared spelling clang 22+ prints was written."""
        if not isinstance(alias_id, str) or self._aliases_fn is None:
            return None
        if self._aliases is None:
            self._aliases = self._aliases_fn()
        scope: tuple[str, ...] | None = None
        seen: set[str] = set()
        current: str | None = alias_id
        while current is not None and current not in seen:
            seen.add(current)
            entry = self._aliases.get(current)
            if entry is None:
                break
            scope, current = entry
        return scope

    def _dependent_names(self, derived_qualname: str) -> frozenset[str]:
        if self._tparams_fn is None:
            return frozenset()
        if self._tparams is None:
            self._tparams = self._tparams_fn()
        return self._tparams.get(derived_qualname, frozenset())

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
        alias_id = type_obj.get("typeAliasDeclId")
        dependent = self._dependent_names(derived_qualname)
        if desugared:
            if alias_id is None:
                # Only a canonical spelling gets a non-alias desugaredQualType.
                if (found := self._exact(desugared)) is not None:
                    return found
            else:
                alias_scope = self._innermost_alias_scope(alias_id)
                if (
                    alias_scope is not None
                    and (found := self._lookup(desugared, alias_scope, frozenset()))
                    is not None
                ):
                    return found
                if (found := self._exact(desugared)) is not None:
                    return found
        if written:
            return self._lookup(written, scope_of_qualname(derived_qualname), dependent)
        return None


class BaseLookupIndex(dict[str, dict[str, Any]]):
    """``record key -> node`` index that also owns its memoized
    :class:`BaseResolver`, so every vtable walk over the same index shares
    one resolver instead of rebuilding it per record."""

    def __init__(
        self,
        items: Mapping[str, dict[str, Any]],
        aliases: Callable[[], AliasIndex] | None = None,
        template_params: Callable[[], TemplateParamIndex] | None = None,
    ) -> None:
        super().__init__(items)
        self._aliases = aliases
        self._template_params = template_params
        self._resolver: BaseResolver | None = None

    def resolver(self) -> BaseResolver:
        if self._resolver is None:
            self._resolver = BaseResolver(
                self.keys(), self._aliases, self._template_params
            )
        return self._resolver


def resolver_for(records_by_qualname: Mapping[str, dict[str, Any]]) -> BaseResolver:
    """The shared resolver of a :class:`BaseLookupIndex`, or a fresh one over
    a plain mapping (hand-built test indices)."""
    if isinstance(records_by_qualname, BaseLookupIndex):
        return records_by_qualname.resolver()
    return BaseResolver(records_by_qualname.keys())
