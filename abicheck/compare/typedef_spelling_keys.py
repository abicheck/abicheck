# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Which typedef keys a spelling resolves to, read from a snapshot's maps.

``AbiSnapshot.declarations.typedefs`` is keyed by the bare alias, which
collapses two classes' colliding member aliases (``Foo::value_type`` and
``Bar::value_type``) onto one key and keeps only the last target.
``typedefs_qualified`` (schema v25) keeps both, so resolution goes through it
whenever the snapshot carries it.

:func:`typedef_spelling_index` builds the lookup once per snapshot. Every
closure walk shares that one index (it rides on
:class:`~abicheck.compare.surface_graph.ReferencedIdentifiers`), so a spelling
costs a dict lookup rather than a scan of the whole qualified map.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot


class TypedefSpellingIndex(NamedTuple):
    """Spelling -> typedef key(s) lookup for one snapshot."""

    #: ``True`` when the snapshot carries ``typedefs_qualified`` (schema v25+).
    has_qualified: bool
    #: Qualified keys whose target is non-empty.
    qualified: frozenset[str]
    #: Bare ``::``-tail -> every qualified key with that tail, in map order.
    tails: dict[str, tuple[str, ...]]
    #: Bare keys of ``typedefs`` whose target is non-empty (legacy fallback).
    bare: frozenset[str]

    def keys_for(self, spelling: str) -> tuple[str, ...]:
        """Every typedef key *spelling* resolves to.

        With ``typedefs_qualified``: a qualified spelling matches its exact key,
        a bare one matches every qualified typedef whose ``::``-tail equals it.
        A spelling with no qualified match, and every spelling of a pre-v25
        snapshot, falls back to the bare ``typedefs`` dict unchanged. Callers
        follow *all* returned keys and treat more than one as ambiguous.
        """
        if self.has_qualified:
            keys: tuple[str, ...]
            if "::" in spelling:
                keys = (spelling,) if spelling in self.qualified else ()
            else:
                keys = self.tails.get(spelling, ())
            if keys:
                return keys
        return (spelling,) if spelling in self.bare else ()


def typedef_spelling_index(snap: AbiSnapshot) -> TypedefSpellingIndex:
    """Build the spelling index for *snap* in one pass over its maps."""
    qualified = snap.declarations.typedefs_qualified
    tails: dict[str, list[str]] = {}
    for key, target in qualified.items():
        if target:
            tails.setdefault(key.rsplit("::", 1)[-1], []).append(key)
    return TypedefSpellingIndex(
        has_qualified=bool(qualified),
        qualified=frozenset(k for k, t in qualified.items() if t),
        tails={tail: tuple(keys) for tail, keys in tails.items()},
        bare=frozenset(k for k, t in snap.declarations.typedefs.items() if t),
    )
