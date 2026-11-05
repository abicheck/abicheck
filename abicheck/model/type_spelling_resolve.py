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

"""Resolve a type spelling to the record it names, whatever frontend wrote it.

The leak walk (:mod:`abicheck.compare.internal_leak_paths`) follows type *spellings* — a base,
a field type, a signature type — to records. Each header frontend spells the
same reference differently:

* castxml writes the bare leaf (``Impl``, ``Box<int>``), with fully qualified
  template arguments, and records which record the slot names in
  ``type_identities_fact`` (``("lib::detail::Impl",)``);
* clang writes the reference as the header did (``detail::Impl`` from inside
  ``namespace lib``; ``Wrap<detail::Impl>``), and records no identity;
* hand-built and DWARF-derived snapshots use a fully qualified or a bare name.

Records are keyed by their qualified identity (``qualified_name`` or, absent
that, ``name``), so two records sharing a leaf (``lib::Impl`` and
``lib::detail::Impl``) stay two records. :meth:`TypeResolver.resolve` tries, in
order: the spelling as a record key; the slot's recorded identities; C++ name
lookup of every name in the spelling (head and template arguments) from the
scope that wrote it; and, for a bare spelling only, the one record with that
leaf. A spelling none of these resolves is returned unchanged, which is what
the walk did before for every spelling.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .entities import RecordType

_CHAIN = re.compile(r"(?<![\w:])(?:::)?[A-Za-z_]\w*(?:\s*::\s*[A-Za-z_]\w*)*")
_SPACE_AROUND = re.compile(r"\s*(::|<|>|,|\*|&)\s*")


def normalize_spelling(text: str) -> str:
    """Whitespace-insensitive form: ``A< B ,C >`` and ``A<B, C>`` compare equal."""
    out = _SPACE_AROUND.sub(r"\1", text.strip())
    return out.replace(",", ", ")


def split_components(name: str) -> list[str]:
    """``a::B<c::D>::E`` -> ``["a", "B<c::D>", "E"]`` (template-aware)."""
    if "<" not in name:
        return [p.strip() for p in name.split("::") if p.strip()]
    parts: list[str] = []
    depth = 0
    start = 0
    i = 0
    while i < len(name):
        ch = name[i]
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif depth == 0 and name.startswith("::", i):
            parts.append(name[start:i])
            i += 2
            start = i
            continue
        i += 1
    parts.append(name[start:])
    return [p.strip() for p in parts if p.strip()]


def _stem(component: str) -> str:
    return component.split("<", 1)[0].strip()


def record_key(rec: RecordType) -> str:
    """The identity a record is walked under: its qualified name, else its name."""
    return rec.qualified_name or rec.name


class TypeResolver:
    """Spelling -> record key resolution over one snapshot's records."""

    def __init__(
        self, records: Iterable[RecordType], extra_names: Iterable[str] = ()
    ) -> None:
        self.records: dict[str, RecordType] = {}
        for rec in records:
            self.records.setdefault(record_key(rec), rec)
        self._normalized = {normalize_spelling(k): k for k in self.records}
        self._by_leaf: dict[str, list[str]] = {}
        # Every scope-qualified name a reference may name: each record's
        # component stems (`lib`, `lib::Wrap`), plus enums and typedefs, so a
        # template argument naming a non-record still qualifies.
        self._known: set[str] = set()
        for key in self.records:
            parts = split_components(key)
            if parts:
                self._by_leaf.setdefault(parts[-1], []).append(key)
            self._add_known(parts)
        for name in extra_names:
            self._add_known(split_components(name))
        # Per-resolver memo: the walk asks about the same spellings from the
        # same scopes many times over one snapshot.
        self._memo: dict[tuple[str, str | None, tuple[str, ...]], str] = {}

    def _add_known(self, parts: list[str]) -> None:
        stems = [_stem(c) for c in parts]
        for i in range(1, len(stems) + 1):
            self._known.add("::".join(stems[:i]))

    def _lookup_chain(self, chain: str, scope: list[str]) -> str | None:
        chain = re.sub(r"\s+", "", chain)
        if chain.startswith("::"):
            chain = chain[2:]
            return chain if chain in self._known else None
        for depth in range(len(scope), -1, -1):
            candidate = "::".join([*scope[:depth], chain])
            if candidate in self._known:
                return candidate
        return None

    def _qualify(self, spelling: str, scope: list[str]) -> str:
        """Every name in *spelling* replaced by what C++ lookup from *scope* finds."""

        def sub(m: re.Match[str]) -> str:
            found = self._lookup_chain(m.group(0), scope)
            return found if found is not None else m.group(0)

        return _CHAIN.sub(sub, spelling)

    def resolve(
        self,
        spelling: str,
        scope: str | None = None,
        identities: tuple[str, ...] = (),
    ) -> str:
        """The record key *spelling* names, or *spelling* when none is proven.

        *scope* is the qualified scope the spelling was written in (the
        referencing record, or a function's or variable's enclosing scope);
        *identities* is the slot's recorded ``type_identities_fact`` value.
        """
        if not spelling or (spelling in self.records and not scope):
            return spelling
        key = (spelling, scope, identities)
        found = self._memo.get(key)
        if found is None:
            found = self._memo[key] = self._resolve(spelling, scope, identities)
        return found

    def _resolve(
        self, spelling: str, scope: str | None, identities: tuple[str, ...]
    ) -> str:
        if spelling.isidentifier():
            # A bare name whose leaf one record owns: any lookup could only
            # find that record, and the unique-leaf fallback returns it anyway.
            owners = self._by_leaf.get(spelling, [])
            if len(owners) == 1:
                return owners[0]
        # A leading `::` names the global scope: lookup must not start from
        # the enclosing scopes, and no unique-leaf guess may override it.
        rooted = spelling.lstrip().startswith("::")
        norm = normalize_spelling(spelling.removeprefix("::"))
        leaf = split_components(norm)[-1] if norm else norm
        for ident in identities:
            if ident in self.records and split_components(ident)[-1] == leaf:
                return ident
        if scope and not rooted:
            # C++ lookup: the innermost enclosing scope that declares the name
            # wins over an outer (or global) record of the same spelling. The
            # scope keeps its template arguments first (`Box<int>::Impl`).
            parts = split_components(scope)
            for depth in range(len(parts), 0, -1):
                found = self._normalized.get(
                    normalize_spelling("::".join([*parts[:depth], norm]))
                )
                if found is not None:
                    return found
        scope_parts = [_stem(c) for c in split_components(scope)] if scope else []
        qualified = normalize_spelling(
            self._qualify("::" + norm if rooted else norm, scope_parts)
        )
        if qualified in self._normalized:
            return self._normalized[qualified]
        if norm in self._normalized:
            return self._normalized[norm]
        if not rooted and len(split_components(norm)) == 1 and "<" not in norm:
            owners = self._by_leaf.get(norm, [])
            if len(owners) == 1:
                return owners[0]
        return spelling


def scope_of(qualified: str) -> str:
    """``lib::Pub::get`` -> ``lib::Pub``; ``make`` -> ``""``."""
    parts = split_components(qualified)
    return "::".join(parts[:-1])
