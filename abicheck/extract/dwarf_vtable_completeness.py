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

"""ADR-063 Phase 5B / T9 (duplication-and-convergence-assessment.md Phase 6
item 4), third slice: the DWARF-specific per-translation-unit completeness
signal for ``RecordType.bases``/``virtual_bases``/``vtable``.

Split out of ``dwarf_snapshot.py`` (ADR-061: that module is a legacy root
module still migrating its responsibilities into ``extract/`` piece by
piece, and is already at its own ``architecture/debt.yaml`` ``no_growth``
baseline) -- these three functions have no dependency on the builder's own
mutable walk state beyond what's passed in explicitly, so they move here
the same way ``dwarf_records.py``'s four free functions already did.

Leaf module: depends only on ``dwarf_utils`` and ``model`` (allowed:
``extract -> model``, ADR-061) -- nothing above, and in particular nothing
from ``compare/`` (this module never decides whether a transition is
*evidenced*, only whether a record's own evidence is *complete* -- see
``compare/vtable_evidence.py`` for the consumer half).

**Why this exists.** ``_DwarfSnapshotBuilder``'s own "first definition
wins" ODR handling (``_check_and_register_type_name``) builds each record
type from exactly ONE compilation unit's own view of it, discarding every
other CU's own copy outright -- including whatever that CU independently
observed about the same class's virtual methods and bases. Two CUs
compiled from the same header can legitimately disagree here for reasons
that have nothing to do with a real ABI difference (a differing ``-g``
level, a TU that never instantiated/used a given virtual so the compiler
omitted its DIE, ``-flimit-debug-info``-style trimming) -- this is exactly
the capture-gap shape ``compare/vtable_evidence.py``'s own module
docstring already names as an unguarded false-positive source: "one
side's virtual methods happen to live in a translation unit only the
other side's debug info covers." That docstring's own framing is
*cross-snapshot*; this module closes the same gap *within* one snapshot,
across its own CUs -- and DWARF, uniquely among the supported debug
formats, actually carries the redundant per-CU evidence needed to detect
it: a second CU's own (discarded) definition of the same class is direct
proof that this binary's own debug info is not uniformly complete for
that class, independent of anything the *other* side of a comparison
shows.

``note_duplicate_record_evidence`` is called from
``_DwarfSnapshotBuilder._process_record_type_named``'s existing
ODR-duplicate branch, once per non-retained, non-declaration duplicate
DIE encountered; ``finalize_vtable_evidence_completeness`` is a new
post-CU-walk pass (mirroring the existing ``_finalize_vptr_offsets``
pass's own "needs every CU to be known first" shape), called once every
CU has been walked. Only ever escalates evidence from confirmed-complete
to confirmed-partial -- never resolves a genuine disagreement, never picks
a winner between the two DIEs' own views, and never runs at all for a
class DWARF only ever saw defined once (the overwhelming common case).

Downgrades are scoped **per disagreeing field**, not per record (Codex
review finding on this PR): a duplicate that disagrees only on ``vtable``
marks only ``vtable_fact`` as ``PARTIAL`` -- ``bases_fact``/
``virtual_bases_fact`` stay ``PRESENT`` if their own membership actually
agreed. Downgrading all three whenever any one disagreed would let one
incomplete evidence family (e.g. an omitted virtual method DIE) blind a
completely-evidenced ``bases``/``virtual_bases`` comparison to a real
``TYPE_BASE_CHANGED``/``BASE_CLASS_VIRTUAL_CHANGED`` finding elsewhere in
the same record -- exactly the over-suppression this closure must not
introduce while fixing the original under-suppression.

Both take *builder* (an ``_DwarfSnapshotBuilder``, typed ``Any`` here --
this module still imports nothing from ``dwarf_snapshot.py``, only reads
four of its instance attributes by name: ``types``,
``_vtable_evidence_conflicts`` (a ``dict[str, set[str]]``: qualified
record name -> the subset of ``{"bases", "virtual_bases", "vtable"}`` that
some discarded duplicate disagreed on), ``_record_by_qualified_name``,
``_resolve_base_name_and_key``) rather than each piece of state
individually -- ``dwarf_snapshot.py`` is already at its own
``architecture/debt.yaml`` ``no_growth`` line-count ceiling, so the two
call sites there must stay one line each.

``PARTIAL`` is deliberately the *existing* ``FactStatus`` member for this,
not a new one: its own docstring -- "the producer ran but covered only
part of the requested scope... the uncovered part is unknown, not absent"
-- already states the DWARF-specific-completeness claim this closure
needs, and every sibling reader in this cluster
(``diff_cxx_rules._fact_str_list_confirmed`` for bases/virtual_bases,
``diff_vtable_layout._is_polymorphic`` and ``diff_layout._check_vptr_
introduced`` for vtable_fact) already treats a ``PARTIAL`` list as "known
incomplete, don't trust an empty reading" -- this is the first *producer*
to actually emit it for these three fields, not a new consumer-side
convention. Deliberately does NOT touch ``vptr_offset_bits_fact`` -- see
``compare/vtable_evidence.py``'s own "NOT consulted here" note: that field
is a circular derivation on this exact producer wherever it isn't
independently resolved, and ``diff_layout._check_vptr_introduced``
already gives ``PARTIAL`` there a DIFFERENT meaning (the direct-clang
backend's own scalar heuristic, deliberately trusted rather than treated
as a gap) -- overloading the same status on the same field for a third,
DWARF-specific meaning would be exactly the kind of drive-by extension
that module's docstring warns against.

See ``compare/vtable_evidence.py``'s own "T9 second slice" docstring note
for the consumer half (``vtable_transition_is_evidenced`` declining on
the resulting ``FactStatus.PARTIAL``), and ``tests/test_dwarf_vtable_
completeness.py`` for the producer-side bug-class test suite.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..dwarf_utils import attr_int as _attr_int, attr_str as _attr_str
from ..model import Fact, resolved_fact_value as _resolved_fact_value

__all__ = [
    "duplicate_record_evidence_signature",
    "finalize_vtable_evidence_completeness",
    "note_duplicate_record_evidence",
]

#: ``(base class bare name, DIE key)`` resolver for a ``DW_TAG_inheritance``
#: child -- ``_DwarfSnapshotBuilder._resolve_base_name_and_key``, injected
#: rather than reimplemented here since it needs the builder's own CU-ref
#: resolution machinery (``dwarf_utils.resolve_die_ref``), not a leaf-level
#: concern.
BaseNameResolver = Callable[[Any, Any], "tuple[str, tuple[int, int] | None]"]

#: The three ``RecordType`` fields this closure can independently mark
#: ``PARTIAL`` -- also the complete key set ``builder._vtable_evidence_
#: conflicts``' per-record value may hold.
_ALL_FIELDS: frozenset[str] = frozenset({"bases", "virtual_bases", "vtable"})


def duplicate_record_evidence_signature(
    die: Any,
    CU: Any,
    children: list[Any] | None,
    *,
    resolve_base_name_and_key: BaseNameResolver,
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """``(bases, virtual_bases, vtable)`` name sets read directly off a
    *non-retained* record DIE, for comparison-only use by
    :func:`note_duplicate_record_evidence`.

    Deliberately NOT ``_DwarfSnapshotBuilder._collect_record_type_children``:
    that method also extracts fields (irrelevant here) and registers
    base/vptr edge state keyed by ``id(rec)`` for a ``RecordType`` this DIE
    will never get (it is a discarded duplicate) -- reusing it would either
    skip that registration awkwardly or register bookkeeping for an object
    that doesn't exist. This is a read-only subset: only the two child tags
    that feed the comparison (``DW_TAG_inheritance``, ``DW_TAG_subprogram``),
    same field-level logic as ``_process_inheritance_child``/
    ``_process_virtual_method_child`` but collecting into sets (membership,
    not order) rather than the ordered lists the retained definition's own
    ``RecordType`` carries.
    """
    bases: set[str] = set()
    virtual_bases: set[str] = set()
    vtable: set[str] = set()
    kids = children if children is not None else die.iter_children()
    for child in kids:
        if child.tag == "DW_TAG_inheritance":
            base_name, _base_die_key = resolve_base_name_and_key(child, CU)
            if not base_name:
                continue
            if _attr_int(child, "DW_AT_virtuality") > 0:
                virtual_bases.add(base_name)
            else:
                bases.add(base_name)
        elif child.tag == "DW_TAG_subprogram":
            if _attr_int(child, "DW_AT_virtuality") > 0:
                mangled = (
                    _attr_str(child, "DW_AT_linkage_name")
                    or _attr_str(child, "DW_AT_MIPS_linkage_name")
                    or _attr_str(child, "DW_AT_name")
                )
                if mangled:
                    vtable.add(mangled)
    return frozenset(bases), frozenset(virtual_bases), frozenset(vtable)


def note_duplicate_record_evidence(
    builder: Any, qualified: str, die: Any, CU: Any, children: list[Any] | None
) -> None:
    """Record, in ``builder._vtable_evidence_conflicts`` (in place), which
    of ``{"bases", "virtual_bases", "vtable"}`` this non-retained
    ODR-duplicate DIE's own membership disagrees with the retained
    definition's (already built, looked up in
    ``builder._record_by_qualified_name``) -- **only** the fields that
    actually disagree, not all three whenever any one does (Codex review
    finding on this PR: see the module docstring's "Downgrades are scoped
    per disagreeing field" note for why blanket-downgrading would let one
    incomplete evidence family blind an otherwise fully-evidenced
    comparison on a *different* field of the same record).

    Cheap by construction: only ever called for a DIE that has already
    failed ``_check_and_register_type_name`` -- i.e. only for genuine
    ODR-duplicates, not the common one-definition case -- and does nothing
    once *qualified* already has all three fields flagged, so a third,
    fourth, ... duplicate of an already-fully-known-incomplete type costs
    one dict lookup, not another full child scan.

    Declaration-only stub DIEs (``byte_size == 0 and DW_AT_declaration``)
    never reach here -- ``_process_record_type_named`` returns for those
    before calling this, since a forward reference carries no member
    children to compare in the first place.
    """
    conflicts: dict[str, set[str]] = builder._vtable_evidence_conflicts
    already = conflicts.get(qualified)
    if already is not None and already >= _ALL_FIELDS:
        return  # every field already known incomplete -- nothing more to learn
    retained = builder._record_by_qualified_name.get(qualified)
    if retained is None:
        # Should not happen (a duplicate is only reachable once the first
        # definition already registered itself here), but there is nothing
        # to compare against without it.
        return
    dup_bases, dup_virtual_bases, dup_vtable = duplicate_record_evidence_signature(
        die, CU, children, resolve_base_name_and_key=builder._resolve_base_name_and_key
    )
    # Fact[T]-bridged reads (ADR-063 Phase 0, `fact-field-readers` gate):
    # resolve through `resolved_fact_value`/`resolved_bases()` rather than
    # the raw legacy field -- for these exact three fields that function's
    # own docstring guarantees it is a pure re-spelling of what a direct
    # `retained.bases`/etc. read would already have returned (the retained
    # legacy field and `resolved_fact_value(rec.bases_fact, [])` are a
    # provable invariant per `bridge_legacy_and_fact`), so this is
    # representation-only, not a behavior change.
    disagreeing: set[str] = set()
    if dup_bases != frozenset(retained.resolved_bases()):
        disagreeing.add("bases")
    if dup_virtual_bases != frozenset(retained.resolved_virtual_bases()):
        disagreeing.add("virtual_bases")
    if dup_vtable != frozenset(_resolved_fact_value(retained.vtable_fact, [])):
        disagreeing.add("vtable")
    if disagreeing:
        conflicts.setdefault(qualified, set()).update(disagreeing)


def finalize_vtable_evidence_completeness(builder: Any) -> None:
    """Downgrade to ``Fact.partial(...)`` exactly the fields recorded in
    ``builder._vtable_evidence_conflicts`` (populated by
    :func:`note_duplicate_record_evidence` over the course of a full CU
    walk) for each matching record in ``builder.types`` -- e.g. only
    ``vtable_fact`` when only ``"vtable"`` was ever flagged for that
    record, leaving ``bases_fact``/``virtual_bases_fact`` at ``PRESENT`` if
    their own membership never disagreed (see the module docstring's
    "Downgrades are scoped per disagreeing field" note).

    Mutates each matching ``RecordType`` in place -- the same plain
    post-construction-mutation pattern ``dwarf_snapshot.
    resolve_vptr_offset_bits`` already uses for this exact builder's own
    second, whole-binary-scoped pass (``_finalize_vptr_offsets``); this is
    that pass's sibling, run right alongside it, for the identical reason
    (a conflicting duplicate can appear in a CU processed *after* the
    retained definition, so this cannot be decided at construction time).
    """
    conflicts: dict[str, set[str]] = builder._vtable_evidence_conflicts
    if not conflicts:
        return
    diagnostic = (
        "cross-translation-unit disagreement: another compilation unit's "
        "own definition of this class disagreed on this field's own "
        "membership, so this side's evidence may not reflect the complete "
        "set (ADR-063 Phase 5B / T9 DWARF per-TU completeness slice)"
    )
    for rec in builder.types:
        fields = conflicts.get(rec.name)
        if not fields:
            continue
        # Fact[T]-bridged reads (ADR-063 Phase 0, `fact-field-readers`
        # gate): resolve the value to preserve through `resolved_fact_
        # value`/`resolved_bases()` rather than the raw legacy field --
        # see `note_duplicate_record_evidence`'s identical comment above
        # for why this is representation-only for these three fields.
        # `rec` was just built by its own (PRESENT) definition, so this is
        # exactly value-preserving; only the status of the flagged
        # field(s) changes to PARTIAL.
        if "bases" in fields:
            rec.bases_fact = Fact.partial(
                rec.resolved_bases(), diagnostic, producer="dwarf"
            )
        if "virtual_bases" in fields:
            rec.virtual_bases_fact = Fact.partial(
                rec.resolved_virtual_bases(), diagnostic, producer="dwarf"
            )
        if "vtable" in fields:
            rec.vtable_fact = Fact.partial(
                _resolved_fact_value(rec.vtable_fact, []), diagnostic, producer="dwarf"
            )
