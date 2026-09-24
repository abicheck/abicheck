# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors

"""Which debug-info types a comparison diffs at the L1 tier -- read off the
Phase 2 debug-type join (evidence-entity-model plan).

``diff_platform._diff_dwarf`` diffs only the debug types that *are* the
header-declared ones, so an internal type sharing a name with nothing public
never reaches the layout detectors. That question used to be answered by a
private bare-name rule (a debug name was in scope when it or its last
``::`` segment equalled any header record's bare name), which let
``impl::Foo`` in whenever a public ``api::Foo`` existed -- name equality,
not evidence. It is now the debug-type join's own answer: a debug type is in
scope when its qualified name identifies a header entity of the same kind on
either side of the comparison (:meth:`~abicheck.compare.debug_type_join.
DebugTypeJoin.debug_names_joined`, with a layout-contradicted candidate
still counting -- the header and the binary disagreeing about a public type
is no reason to stop diffing it).

A type opaque (forward-declared only) in *both* snapshots' headers stays out:
callers never see its fields. ``policy/depth_projection.py`` pre-scopes a
projected pair's raw debug pool through this same function, so the two can
never disagree.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .debug_type_join import join_debug_types

if TYPE_CHECKING:
    from ..model.snapshot import AbiSnapshot

__all__ = ["DebugLayoutScope", "debug_layout_scope"]

#: ``(struct names, enum names)``; ``None`` for a kind neither snapshot's
#: headers declare at all -- no header model to scope by, so every debug
#: type of that kind is diffed (the DWARF-only mode).
DebugLayoutScope = tuple["frozenset[str] | None", "frozenset[str] | None"]


def debug_layout_scope(old: AbiSnapshot, new: AbiSnapshot) -> DebugLayoutScope:
    """The debug struct/enum names ``_diff_dwarf`` compares for *old*/*new*."""
    old_named, new_named = _named_by_header(old), _named_by_header(new)
    return (
        _scope(old_named, new_named, "record") if old.types or new.types else None,
        _scope(old_named, new_named, "enum") if old.enums or new.enums else None,
    )


def _named_by_header(snap: AbiSnapshot) -> dict[tuple[str, str], bool]:
    """``(kind, debug name) -> every header entity it names is opaque`` for
    each debug type whose qualified name identifies a header entity (joined,
    or rejected on layout)."""
    join = join_debug_types(snap)
    opaque = {
        ident.node_id
        for rec, ident in zip(snap.types, join.identities.records)
        if rec.is_opaque
    }
    named: dict[tuple[str, str], bool] = {}
    for rec in join.join.right.values():
        nodes = (*rec.candidates, *rec.rejected)
        if not nodes:
            continue
        occ = join.occurrences[rec.subject]
        key = (occ.kind, occ.name)
        named[key] = named.get(key, True) and all(n in opaque for n in nodes)
    return named


def _scope(
    old_named: dict[tuple[str, str], bool],
    new_named: dict[tuple[str, str], bool],
    kind: str,
) -> frozenset[str]:
    names = {n for k, n in (*old_named, *new_named) if k == kind}
    both_opaque = {
        n
        for n in names
        if old_named.get((kind, n)) is True and new_named.get((kind, n)) is True
    }
    return frozenset(names - both_opaque)
