# SPDX-License-Identifier: Apache-2.0
"""Record names a snapshot still defines member functions for.

A record can vanish from a snapshot's type list without being removed: when a
class has no key function (its only virtual is pure, say), gcc and clang emit
just a declaration DIE for it, so the DWARF reader records no definition.
Its member functions are still defined in the library (``Plain::get``), which
is direct evidence that the type exists. ``diff_types`` reads that before it
reports ``type_removed``: weaker evidence must not manufacture a removal.
"""

from __future__ import annotations

from collections.abc import Callable

from ..model import AbiSnapshot
from ..model.mangled_name import itanium_scope_components

__all__ = ["live_member_scopes", "removal_unconfirmed"]


def live_member_scopes(snap: AbiSnapshot) -> frozenset[str]:
    """Qualified scopes of *snap*'s member functions (``ns::Plain`` for
    ``ns::Plain::get``): from the spelled ``scope::name`` when it carries one
    (DWARF, clang), else from the Itanium mangling (castxml spells a bare
    leaf), so the mangling is parsed only where the name cannot answer."""
    out: set[str] = set()
    for fn in snap.declarations.functions:
        spelled = fn.name.split("(", 1)[0]
        if "::" in spelled:
            out.add(spelled.rsplit("::", 1)[0])
        elif fn.mangled.startswith("_Z"):
            comps = itanium_scope_components(fn.mangled)
            if comps and len(comps) > 1:
                out.add("::".join(comps[:-1]))
    return frozenset(out)


def removal_unconfirmed(
    new: AbiSnapshot, side_unconfirmed: bool
) -> Callable[[str], bool]:
    """Whether a record named *name* missing from NEW is no confirmed removal:
    every removal is unconfirmed when *side_unconfirmed* (RD2-5, a stripped
    new side), and a record whose member functions NEW still defines is not
    removed at all."""
    if side_unconfirmed:
        return lambda _name: True
    # Built on the first missing record only: most comparisons have none.
    live: list[frozenset[str]] = []

    def unconfirmed(name: str) -> bool:
        if not live:
            live.append(live_member_scopes(new))
        return name in live[0]

    return unconfirmed
