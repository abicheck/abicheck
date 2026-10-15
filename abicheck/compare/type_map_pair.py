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

"""Old/new type matching: the qualified-identity ``TypeMap`` and the
``TypeMapPair`` that answers "which type on the other side is this one?".

Moved out of ``diff_helpers`` so the pair can own the fallback without
importing back into it (ADR-061: old/new entity matching is ``compare/``).
"""

from __future__ import annotations

from collections.abc import ItemsView, Iterable, Iterator, Mapping, ValuesView
from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Protocol, TypeVar

from .qualified_name_normalization import strip_inline_abi_namespaces

# ── Type-level old/new matching (moved out of diff_types.py, PR #608) ── Generalized (PR #608 follow-up) over any entity kind that has the same bare-``name`` / optional-``qualified_name`` split — ``RecordType`` was the original motivating case, ``EnumType`` shares the identical ambiguity (two distinct enums sharing a bare leaf name in different namespaces) and the identical fix, so both are expressed as one generic implementation via the ``_QualifiedNamed`` structural protocol rather than duplicating ``TypeMap`` per entity kind.


class _QualifiedNamed(Protocol):
    name: str
    qualified_name: str | None


Q = TypeVar("Q", bound=_QualifiedNamed)


def type_map_key(t: _QualifiedNamed) -> str:
    """Key a ``RecordType``/``EnumType`` for old/new matching by its
    namespace-qualified identity, not its bare declaration name.

    The header-mode dumpers (castxml, clang) keep ``t.name`` bare (see its
    docstring in model.py) and carry the real namespace path in
    ``t.qualified_name`` instead; DWARF has no such split and already
    stores the qualified spelling directly in ``name``. Matching by bare
    ``t.name`` alone lets two unrelated types sharing only a short/leaf
    spelling collide and diff against each other, producing spurious
    field/base-class findings. Falls back to ``t.name`` when
    ``qualified_name`` is unset (global-scope/DWARF-only snapshots).
    """
    return t.qualified_name or t.name


class TypeMap(Mapping[str, Q]):
    """An old/new matching map (``RecordType`` or ``EnumType``) keyed by
    :func:`type_map_key`, with a collision-safe bare-``name`` alias used
    only for lookups.

    The alias exists for schema-evolution compatibility: an older snapshot
    that predates (or never populates) ``qualified_name`` keys its entries
    by the bare name alone, so without an alias, matching it against a
    freshly-dumped side that DOES carry ``qualified_name`` would key the two
    sides differently (``Foo`` vs. ``ns::Foo``) and manufacture a false
    ``TYPE_REMOVED``/``TYPE_ADDED`` pair for an unchanged type (PR #608).

    Only used for ``get``/``in`` — kept OUT of ``items``/``values``/
    iteration (else a dict containing both the qualified key and the bare
    alias for one object would make every ``for name, t in
    old_map.items()``-style loop process it twice) — and only added when
    the bare name is unambiguous within *this* snapshot, so it cannot
    reopen the collision :func:`type_map_key` fixes.
    """

    def __init__(self, types: Iterable[Q]) -> None:
        self._primary: dict[str, Q] = {}
        self._bare_owner: dict[str, str | None] = {}
        for t in types:
            key = type_map_key(t)
            self._primary[key] = t
            bare = t.name
            if bare in self._bare_owner:
                if self._bare_owner[bare] != key:
                    self._bare_owner[bare] = (
                        None  # ambiguous: >1 distinct qualified identity
                    )
            else:
                self._bare_owner[bare] = key
        self._bare_alias: dict[str, str] = {
            bare: key
            for bare, key in self._bare_owner.items()
            if key is not None and bare not in self._primary
        }

    def bare_name_is_unambiguous(self, bare: str) -> bool:
        """True if exactly one distinct qualified identity in this map
        shares the bare declaration name *bare* (including the trivial case
        of a single global-scope type whose own key already equals its bare
        name). False for "no type has this bare name" and "two-or-more
        *distinct* qualified identities share it" alike — both are unsafe to
        treat as a single unambiguous target.
        """
        return self._bare_owner.get(bare) is not None

    def __getitem__(self, key: str) -> Q:
        # get()/__contains__ come from the Mapping mixin, implemented in
        # terms of this — alias resolution lives in exactly one place.
        t = self._primary.get(key)
        if t is not None:
            return t
        alias_key = self._bare_alias.get(key)
        if alias_key is not None:
            # _bare_alias values are always keys already present in
            # _primary (built from it, see __init__) -- a plain indexing
            # KeyError here would indicate a construction bug, not a normal
            # "key absent" case.
            return self._primary[alias_key]
        raise KeyError(key)

    def __len__(self) -> int:
        return len(self._primary)

    def __iter__(self) -> Iterator[str]:
        return iter(self._primary)

    def items(self) -> ItemsView[str, Q]:
        return self._primary.items()

    def values(self) -> ValuesView[Q]:
        return self._primary.values()


class Side(StrEnum):
    """Which of the two snapshots a type was taken from."""

    OLD = "old"
    NEW = "new"


@dataclass(frozen=True)
class TypeMapPair(Generic[Q]):
    """The old and new ``TypeMap`` of one comparison.

    Callers name the side a type came from (:class:`Side`) instead of
    passing the probing map first, so the own/other order is decided in
    one place. :meth:`counterpart` is the single owner of old/new type
    matching."""

    old: TypeMap[Q]
    new: TypeMap[Q]

    def counterpart(self, side: Side, t: Q) -> Q | None:
        """Look up *t*'s counterpart in *other* (the opposite old/new ``TypeMap``
        from the one *t* itself came from, ``own``), trying both *t*'s own
        qualified matching key and its bare declaration name.

        ``TypeMap``'s bare-name alias only maps bare -> qualified (see its
        docstring): a legacy snapshot keyed by the bare name resolves fine
        against a *fresh* qualified-keyed counterpart, because the fresh side's
        map carries that alias. But there is no reverse qualified -> bare
        mapping, so when *t* itself comes from the *fresh* (qualified-keyed) side
        and *other* is the *legacy* one, looking ``other`` up by ``type_map_key(t)``
        alone misses — ``other`` only has the bare key, never learns the
        qualified spelling. Retrying with the bare name makes the schema-
        evolution compatibility symmetric regardless of which side is legacy
        (Codex review, PR #608).

        That bare-name retry is only safe when *t*'s own bare name is itself
        unambiguous within ``own`` — i.e. *t* is the one and only type in its own
        snapshot with that bare spelling. Without this check, a genuine
        same-leaf-name collision on the probing side (e.g. old ``ns1::Impl`` +
        ``ns2::Impl`` vs. a new side that only kept ``ns2::Impl``) would retry
        ``ns1::Impl``'s failed qualified lookup with the bare name ``Impl``,
        hit ``other``'s alias for the *unrelated* surviving ``ns2::Impl``, and
        diff two distinct types against each other — reopening the exact
        short/leaf-name collision ``type_map_key`` was introduced to fix, this
        time through the compatibility fallback instead of naive bare matching
        (Codex review, PR #608, second round).

        That unambiguity check is necessary but **not sufficient**, and the
        missing half is what this function's second fix closes. The retry exists
        for exactly one situation — *other* is a legacy side that never recorded
        a qualified identity for this type — but it used to fire whenever the
        qualified lookup missed, *including* when both sides carry full,
        genuinely different qualified identities. A real namespace move
        (oneTBB 2022's flow graph, ``tbb::detail::d1::graph`` ->
        ``tbb::detail::d2::graph``: distinct types, distinct mangled vtable
        symbols, no declaration in common) is precisely that shape: each side
        holds exactly one type whose bare name is ``graph``, so both bare names
        are "unambiguous" within their own snapshot, the retry hits the other
        side's bare alias, and two unrelated types are diffed against each other
        — manufacturing ``TYPE_SIZE_CHANGED`` / ``TYPE_FIELD_OFFSET_CHANGED`` /
        ``TYPE_VTABLE_CHANGED`` for a pair that is really one removal plus one
        addition. Unambiguity cannot see this: it answers "is this bare spelling
        claimed once here", never "did the other side actually fail to record a
        qualified name".

        So the candidate the retry finds is accepted in exactly two shapes:

        * the candidate is *legacy-shaped* — its own matching key equals its bare
          declaration name, i.e. it carries no distinct qualified identity. That
          is the schema-evolution case the alias was introduced for.
        * the two qualified identities are equal once *inline ABI-tag
          namespaces* are stripped from both
          (:func:`qualified_name_segments.strip_inline_abi_namespaces`) —
          ``std::basic_string<...>`` vs. libstdc++'s dual-ABI
          ``std::__cxx11::basic_string<...>``, or a versioned ``ns::v1::X``
          vs. ``ns::X``. An inline namespace is transparent for name lookup, so
          the two spellings name one entity and a real layout change between
          them (the dual-ABI size change) is a mutation, not a removal plus an
          addition. The stripping is deliberately narrow: only the version-shaped
          (``v1``/``__1``) and named toolchain (``__cxx11``/``__ndk1``) tags
          qualify, never an ordinary implementation namespace such as
          ``detail``, ``impl`` or oneTBB's ``d1`` — renaming one of those really
          does move every declaration inside it.

        Anything else — a side that DID record ``ns::Foo`` and simply does not
        contain *t* — is reported as a non-match, which is the truth.
        """
        own, other = (self.new, self.old) if side is Side.NEW else (self.old, self.new)
        key = type_map_key(t)
        found = other.get(key)
        if found is not None:
            return found
        if t.name != key and own.bare_name_is_unambiguous(t.name):
            candidate = other.get(t.name)
            if candidate is None:
                return None
            candidate_key = type_map_key(candidate)
            if candidate_key == candidate.name or strip_inline_abi_namespaces(
                key
            ) == strip_inline_abi_namespaces(candidate_key):
                return candidate
        return None
