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

"""``downgrade_opaque_struct_changes`` and its identity-bridging helpers
(ADR-063 Phase 10) -- split out of ``diff_filtering.py`` into a sibling
``compare/`` leaf module (Codex review, PR #1218, round 10) once this
migration's own additions pushed that legacy, ``debt.yaml``-pinned
``no_growth`` file past its recorded baseline. Mirrors the established
pattern for exactly this situation -- see ``base_class_diff.py``/
``va_list_diff.py``/``qualified_name_normalization.py``'s own docstrings and
``compare/AGENTS.md``'s module list ("new behavior in a no_growth-tracked
legacy compare module belongs here... not as growth in the legacy file") --
rather than growing ``diff_filtering.py`` further. ``diff_filtering.py``
imports this module's public names back under their original, private
(underscore-prefixed) spellings, so every existing internal call site and
external test import is unaffected by the move.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from ..diff_helpers import depth_aware_bare_name, make_change
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import EntityId
from .opaque_struct_types import find_opaque_struct_types

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model import AbiSnapshot

__all__ = [
    "OPAQUE_DOWNGRADEABLE_KINDS",
    "downgrade_opaque_struct_changes",
    "resolve_struct_change_entity_id",
    "struct_change_record_name",
]


#: ChangeKinds that should be downgraded when the type is opaque in both snapshots.
OPAQUE_DOWNGRADEABLE_KINDS: frozenset[ChangeKind] = frozenset(
    {
        ChangeKind.TYPE_SIZE_CHANGED,
        ChangeKind.TYPE_FIELD_ADDED,
        ChangeKind.TYPE_FIELD_REMOVED,
        ChangeKind.TYPE_FIELD_OFFSET_CHANGED,
        ChangeKind.TYPE_FIELD_TYPE_CHANGED,
        ChangeKind.TYPE_ALIGNMENT_CHANGED,
        ChangeKind.STRUCT_SIZE_CHANGED,
        ChangeKind.STRUCT_FIELD_OFFSET_CHANGED,
        ChangeKind.STRUCT_FIELD_REMOVED,
        ChangeKind.STRUCT_FIELD_TYPE_CHANGED,
        ChangeKind.STRUCT_ALIGNMENT_CHANGED,
    }
)


def struct_change_record_name(c: Change) -> str:
    """The owning record's own name for a ``STRUCT_*``/``TYPE_FIELD_*``
    change -- not always ``c.symbol`` itself.

    Whole-struct changes (``STRUCT_SIZE_CHANGED``/``STRUCT_ALIGNMENT_
    CHANGED``, and the header-AST ``TYPE_FIELD_*`` kinds, which already
    pass ``entity_id`` directly and need no bridging at all) use the bare
    record name as ``symbol`` already. Only ``diff_platform``'s
    DWARF-sourced ``STRUCT_FIELD_REMOVED``/``STRUCT_FIELD_OFFSET_CHANGED``/
    ``STRUCT_FIELD_TYPE_CHANGED`` compound their own ``symbol`` as
    ``f"{record}::{field_name}"`` (Codex review, PR #1218, round 7) --
    recovered here by stripping the *exact*, already-known ``field_name``
    suffix ``Change.field_name`` carries, never by a generic bare-name
    guess (which would instead yield the field's own name)."""
    if c.field_name and c.symbol.endswith(f"::{c.field_name}"):
        return c.symbol[: -(len(c.field_name) + 2)]
    return c.symbol


def resolve_struct_change_entity_id(
    c: Change, old: AbiSnapshot, new: AbiSnapshot
) -> Change:
    """Borrow a stable identity for *c* from header-AST ``RecordType`` data,
    when *c* itself carries none.

    ``diff_platform``'s DWARF struct-layout diff (the production source of
    the ``STRUCT_*`` changes :func:`downgrade_opaque_struct_changes` acts
    on) builds its ``Change``s from ``StructLayout`` -- a DWARF-only model
    with no ``entity_id`` field -- so those changes never carry one
    (Codex review, PR #1218, round 5). Real identity for the *same* symbol
    can still be available on the header-AST side: ``AbiSnapshot.types``
    is populated by the header-AST backend independently of DWARF's own
    struct-layout facts, so a hybrid/mixed-evidence dump commonly has both.

    Only ever narrows to a *single, unambiguous* candidate: if more than
    one distinct ``entity_id`` resolves for *c*'s own symbol (its exact
    spelling or its depth-aware bare name) across ``old.types``/
    ``new.types`` combined, or none at all, *c* is returned unchanged --
    this never guesses under ambiguity, leaving the always-safe spelling
    tier as the fallback exactly as before this bridge existed.

    **Field-level DWARF changes need their owning record's name, not
    their own ``symbol``** (Codex review, PR #1218, round 7):
    ``_removed_field_changes``/``_existing_field_changes`` compound
    ``symbol`` as ``f"{record}::{field_name}"``, so a generic bare-name
    guess on the *whole* symbol yields the field's own name (e.g.
    ``"field"``), never the record's. :func:`struct_change_record_name`
    recovers the exact record spelling by stripping the known
    ``field_name`` suffix instead of guessing.

    **An exact-name match, even an identity-less one, blocks the bare-name
    fallback** (Codex review, PR #1218, round 8): if ``old.types``/
    ``new.types`` contains a declaration whose own name is *exactly*
    *record_name*, that declaration -- not some other, differently-named
    one -- is what this change is actually about. Falling through to the
    bare-name candidate when that exact declaration simply has no
    resolved ``entity_id`` would instead risk borrowing an unrelated
    namesake's id (an opaque bare ``Handle`` sharing a leaf spelling with
    a visible ``other::Handle`` the change is really about, say) --
    exactly the collision the stable tier's own bare-name-grouping bugs
    were about, reintroduced here through the bridge instead. The
    bare-name candidate is only ever consulted when *no* exact-named
    declaration exists at all.

    **"Exact" means either ``RecordType.name`` or ``RecordType.
    qualified_name``** (Codex review, PR #1218, round 9): a header-AST
    backend commonly stores only the bare leaf in ``name`` and the real
    scoped spelling in ``qualified_name`` -- checking ``name`` alone would
    never recognize a DWARF change's own qualified *record_name*
    (``"api::Handle"``) as naming the *same* declaration a header-AST
    backend renders leaf-only, wrongly treating it as no exact match at
    all and falling through to an unrelated bare-name namesake exactly
    the round-8 fix was meant to prevent."""
    if c.entity_id is not None:
        return c
    record_name = struct_change_record_name(c)

    exact_found = False
    exact_ids: set[EntityId] = set()
    for snap in (old, new):
        for t in snap.types:
            if t.name == record_name or t.qualified_name == record_name:
                exact_found = True
                if t.entity_id is not None:
                    exact_ids.add(t.entity_id)
    if exact_found:
        if len(exact_ids) == 1:
            return dataclasses.replace(c, entity_id=next(iter(exact_ids)))
        return c

    bare = depth_aware_bare_name(record_name)
    if bare == record_name:
        return c
    bare_ids: set[EntityId] = set()
    for snap in (old, new):
        for t in snap.types:
            if t.name == bare and t.entity_id is not None:
                bare_ids.add(t.entity_id)
    if len(bare_ids) == 1:
        return dataclasses.replace(c, entity_id=next(iter(bare_ids)))
    return c


def downgrade_opaque_struct_changes(
    changes: list[Change],
    old: AbiSnapshot,
    new: AbiSnapshot,
) -> tuple[list[Change], list[Change]]:
    """Downgrade/exclude BREAKING changes for types opaque in both snapshots.

    If a type is forward-declared only (is_opaque=True) in both old and new
    snapshots, consumers cannot allocate, embed, or sizeof the type — they
    only hold pointers. Layout changes detected via DWARF are invisible to
    consumers: a genuine field *addition* is relabelled compatible; every
    other structural observation (removal, mutation, size/alignment change)
    is excluded outright rather than relabelled, and returned as the second
    element of the ``(kept, filtered)`` pair -- see the round-10 docstring
    note below for why relabelling used to be unconditional and why that
    was wrong.

    **Identity-tiered since ADR-063 Phase 10.** The opaqueness computation
    itself (asymmetric-existence + by-value-embedding) lives in
    :func:`~abicheck.compare.opaque_struct_types.find_opaque_struct_types` —
    moved there for the same reason
    :func:`~abicheck.compare.opaque_types.find_opaque_types` already sits
    there rather than in ``diff_filtering.py`` (a matching concern belongs
    at its ADR-061 owner, and that module sits on a zero-slack ``debt.yaml``
    no-growth pin). The final membership test against each ``Change`` goes
    through :meth:`~abicheck.compare.opaque_types.OpaqueTypeIndex.contains`
    rather than a bare ``c.symbol in truly_opaque`` string-set test, closing
    the same qualified-vs-bare spelling mismatch
    ``diff_filtering._downgrade_opaque_type_changes`` already closed for its
    own, separate opaque-suppression path: a stable, cross-snapshot
    ``EntityId`` match is consulted first, and a bare-spelling match remains
    the always-safe fallback (``strict=False`` — this index is not the
    product of a paired old/new ``intersect()``, so it carries no proof of
    completeness that would license narrowing a miss into "not opaque").

    **The stable tier needs a real ``Change.entity_id`` to fire at all**
    (Codex review, PR #1218, round 5): the production DWARF struct-layout
    diff (``diff_platform._struct_size_and_alignment_changes``/
    ``_removed_field_changes``/``_existing_field_changes``) builds its
    ``Change``s from ``StructLayout`` — a DWARF-only model with no
    ``entity_id`` field at all — so those changes never carry one when they
    reach this function. Populating DWARF's own layout parser with real
    identity is a separate, much larger undertaking (this is the same,
    already-documented "DWARF identity unimplemented" producer gap
    ``docs/_meta/one-semantic-pipeline-status.yaml``'s ``identity`` concept
    already records), out of this migration's scope. What *is* in scope:
    when the SAME snapshot pair also carries header-AST ``RecordType``
    data for the changed symbol (a hybrid/mixed-evidence dump — the common
    case, since ``dumper.py`` populates ``AbiSnapshot.types`` from the
    header-AST backend independently of DWARF's own struct-layout facts),
    that data already carries real identity. :func:`resolve_struct_change_
    entity_id` bridges the two: an identity-less ``Change`` borrows the
    unique stable id its own bare or qualified symbol unambiguously
    resolves to among ``old.types``/``new.types`` — never guessing under
    ambiguity (more than one distinct id resolves, or none at all), which
    leaves the always-safe spelling tier as the fallback exactly as before.

    **Field-level changes need their owning record's name, not their own
    ``symbol``, for either tier** (Codex review, PR #1218, round 7):
    ``_removed_field_changes``/``_existing_field_changes`` compound
    ``symbol`` as ``f"{record}::{field_name}"`` for
    ``STRUCT_FIELD_REMOVED``/``STRUCT_FIELD_OFFSET_CHANGED``/
    ``STRUCT_FIELD_TYPE_CHANGED`` — a compound string the *spelling* tier
    was already comparing against a bare record-name set even before this
    migration, and always missing, since neither identity nor the pre-
    migration bare-string check was ever record-name-aware for these three
    kinds. :func:`struct_change_record_name` recovers the exact record
    spelling (from the already-known ``Change.field_name``, not a generic
    bare-name guess) and is used as the ``spelling`` argument to
    :meth:`~abicheck.compare.opaque_types.OpaqueTypeIndex.contains` here,
    closing that pre-existing gap for the spelling tier at the same time
    as wiring the new stable tier through it correctly.

    **Only a real addition is ever relabelled as one** (Codex review, PR
    #1218, round 10): this predates the ADR-063 migration itself, but the
    migration is what brought the site under review -- every matched
    :data:`OPAQUE_DOWNGRADEABLE_KINDS` kind, ``STRUCT_FIELD_REMOVED``/
    ``STRUCT_FIELD_OFFSET_CHANGED``/``STRUCT_FIELD_TYPE_CHANGED`` included,
    used to be unconditionally replaced with ``TYPE_FIELD_ADDED_COMPATIBLE``,
    turning an observed *removal* or *mutation* into a fabricated addition --
    a genuine "record before disposing" violation (root ``AGENTS.md``): the
    original disposition (a removal, say) is not merely reclassified, it is
    misreported as a different fact than the one detected. Only
    ``ChangeKind.TYPE_FIELD_ADDED`` -- the one kind in
    :data:`OPAQUE_DOWNGRADEABLE_KINDS` that already *is* an addition -- is
    still substituted with its own compatible counterpart (the observation
    is the same shape, only the compatibility label changes). Every other
    matched kind is excluded outright and returned in *filtered*, mirroring
    the already-established ``_filter_opaque_size_changes`` shape so the
    post-processing pipeline can route it into ``ctx.opaque_filtered`` --
    the same non-gating, audited-but-hidden-by-default bucket
    ``DowngradeOpaqueTypeChanges``'s own opaque-suppressed structural changes
    reach, rather than either silently vanishing or being counted twice
    under a fabricated replacement's own disposition.
    """
    index = find_opaque_struct_types(old, new)
    if not index:
        return changes, []

    result: list[Change] = []
    filtered: list[Change] = []
    for c in changes:
        lookup_c = resolve_struct_change_entity_id(c, old, new)
        record_name = struct_change_record_name(c)
        if c.kind in OPAQUE_DOWNGRADEABLE_KINDS and index.contains(
            lookup_c, record_name
        ):
            if c.kind == ChangeKind.TYPE_FIELD_ADDED:
                # Genuine addition: relabel as its own compatible counterpart.
                result.append(
                    make_change(
                        ChangeKind.TYPE_FIELD_ADDED_COMPATIBLE,
                        symbol=c.symbol,
                        description=f"(opaque struct) {c.description}",
                        old_value=c.old_value,
                        new_value=c.new_value,
                        source_location=c.source_location,
                    )
                )
            else:
                # Removal/mutation/size/alignment: the observation is not an
                # addition, so it is excluded on its own merits (opaque
                # layout is invisible to consumers) rather than fabricated
                # into one -- see ``filtered``'s docstring note above.
                filtered.append(c)
        else:
            result.append(c)
    return result, filtered
