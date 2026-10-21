# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors
"""Which typedef keys a spelling resolves to, read from a snapshot's maps.

Split out of ``policy/public_surface_closure.py`` (which must stay under the
800-line production cap) and kept a leaf module: it reads only
:class:`~abicheck.model.snapshot.AbiSnapshot`'s own declaration maps, so the
closure walk and any future typedef consumer can share one rule without a
cycle. ``typedefs`` is keyed by the bare alias, which collapses two classes'
colliding member aliases (``Foo::value_type``/``Bar::value_type``) onto one
key and keeps only the last target; ``typedefs_qualified`` (schema v25) keeps
both, so resolution goes through it whenever the snapshot carries it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot


def typedef_keys_for_spelling(snap: AbiSnapshot, spelling: str) -> tuple[str, ...]:
    """Every typedef key *spelling* resolves to, read from *snap*'s maps.

    Schema v25 (``typedefs_qualified`` non-empty): a qualified spelling
    matches its exact key; a bare one matches every qualified typedef whose
    ``::``-tail equals it, so two classes' colliding ``value_type`` aliases
    both match. A spelling with no qualified match, and every spelling of a
    pre-v25 snapshot (empty ``typedefs_qualified``), falls back to the bare
    ``typedefs`` dict unchanged. Callers follow *all* returned keys and treat
    more than one as ambiguous (see :func:`_walk_type_closure`).
    """
    qualified = snap.declarations.typedefs_qualified
    keys: tuple[str, ...] = ()
    if qualified:
        if "::" in spelling:
            keys = (spelling,) if qualified.get(spelling) else ()
        else:
            keys = tuple(
                key
                for key, target in qualified.items()
                if target and key.rsplit("::", 1)[-1] == spelling
            )
    if keys:
        return keys
    return (spelling,) if snap.declarations.typedefs.get(spelling) else ()
