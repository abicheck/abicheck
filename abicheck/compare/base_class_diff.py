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

"""``bases``/``virtual_bases`` raw-change identification (ADR-063 Phase 5B).

Deciding whether a ``RecordType`` pair's base-class hierarchy changed, and
which raw ``ChangeKind`` that is, is exactly the "identifying a raw change"
question ``compare/AGENTS.md`` reserves for this package (Codex review, PR
#1033: this `FactStatus`-aware logic was first added directly to
``diff_types.py``, a legacy flat `compare`-layer module AGENTS.md's own
task-routing table says new behavior should route away from). Moved here;
``diff_types._diff_type_bases`` is now a delegation-only facade.
"""

from __future__ import annotations

from ..diff_helpers import make_change
from ..model import RecordType
from ..model.availability import FactStatus
from ..model.change import Change
from ..model.change_catalog.kinds import ChangeKind
from ..model.identity import EntityId
from .fact_comparison import compare_facts


def diff_bases(name: str, t_old: RecordType, t_new: RecordType) -> list[Change]:
    """``bases``/``virtual_bases`` findings.

    Each side's evidence is gated through :func:`~.fact_comparison.
    compare_facts` *before* the two lists are compared: an incomplete
    ``bases_fact``/``virtual_bases_fact`` on either side (``NOT_COLLECTED``/
    ``FAILED``/an ``UNSUPPORTED`` producer) used to fall through
    ``model.resolved_fact_value(..., [])`` and read as "this side has no
    bases" — fabricating ``TYPE_BASE_CHANGED``/``BASE_CLASS_VIRTUAL_CHANGED``
    findings against real bases on the other side purely from a capture gap,
    not a real hierarchy change. This declines to compare instead (the same
    "decline rather than fabricate" discipline ``diff_types_vtable.
    _vtable_transition_is_evidenced`` already applies to the sibling vtable
    signal), rather than changing what a *fully-evidenced* pair reports —
    behavior-preserving whenever both sides' facts are actually ``PRESENT``.

    A ``degraded`` (``PARTIAL``-backed) comparison is treated the same as
    incomplete, not as usable: this is a full-list membership/set
    comparison (a removal is "present old, absent new"), and ``PARTIAL``
    means the uncovered part of the scope is unknown, not empty (Codex
    review, PR #1033) — an absent base could simply live in the part this
    side's producer didn't cover. No current producer emits
    ``Fact.partial(...)`` for either field, so this is a latent-correctness
    guard rather than an observed false positive, but the same
    "decline rather than fabricate" default applies once it does.

    The two comparisons are gated independently: a comparable
    ``virtual_bases`` pair with an incomplete ``bases`` pair still reports a
    virtual-base-only hierarchy change (just without the finer
    became-virtual/lost-virtual classification, which needs the
    non-virtual-base set too) rather than being withheld entirely.
    """
    changes: list[Change] = []
    entity_id = t_old.entity_id or t_new.entity_id

    bases_cmp = compare_facts(t_old.bases_fact, t_new.bases_fact, [])
    virtual_bases_cmp = compare_facts(
        t_old.virtual_bases_fact, t_new.virtual_bases_fact, []
    )

    old_bases_set: set[str] = set()
    new_bases_set: set[str] = set()
    if bases_cmp.is_comparable and not bases_cmp.degraded:
        old_bases = bases_cmp.old_value or []
        new_bases = bases_cmp.new_value or []
        # BASE_CLASS_POSITION_CHANGED: same set of non-virtual bases, different order
        # This shifts this-pointer adjustments for all bases → old binaries call wrong method.
        old_bases_set = set(old_bases)
        new_bases_set = set(new_bases)
        if old_bases_set == new_bases_set and old_bases != new_bases:
            changes.append(
                make_change(
                    ChangeKind.BASE_CLASS_POSITION_CHANGED,
                    symbol=name,
                    name=name,
                    old_value=str(old_bases),
                    new_value=str(new_bases),
                    entity_id=entity_id,
                )
            )
        elif old_bases_set != new_bases_set:
            # General base class set change (add/remove base) → TYPE_BASE_CHANGED
            changes.append(
                make_change(
                    ChangeKind.TYPE_BASE_CHANGED,
                    symbol=name,
                    description=f"Base classes changed: {name}",
                    old_value=str(old_bases),
                    new_value=str(new_bases),
                    entity_id=entity_id,
                )
            )

    if virtual_bases_cmp.is_comparable and not virtual_bases_cmp.degraded:
        old_virtual_bases = virtual_bases_cmp.old_value or []
        new_virtual_bases = virtual_bases_cmp.new_value or []
        # BASE_CLASS_VIRTUAL_CHANGED: a base moved between virtual and non-virtual
        old_virt_set = set(old_virtual_bases)
        new_virt_set = set(new_virtual_bases)
        # Bases that moved from non-virtual to virtual or vice versa. Empty
        # (rather than wrong) when `bases_cmp` was incomplete above: the
        # intersection against an empty `old_bases_set`/`new_bases_set` is
        # always empty, so this falls through to the plain hierarchy-change
        # branch below instead of guessing at a virtuality flip it has no
        # non-virtual-base evidence to support.
        became_virtual = (new_virt_set - old_virt_set) & old_bases_set
        lost_virtual = (old_virt_set - new_virt_set) & new_bases_set
        if became_virtual or lost_virtual:
            desc_parts = []
            if became_virtual:
                desc_parts.append(f"became virtual: {sorted(became_virtual)}")
            if lost_virtual:
                desc_parts.append(f"lost virtual: {sorted(lost_virtual)}")
            changes.append(
                make_change(
                    ChangeKind.BASE_CLASS_VIRTUAL_CHANGED,
                    symbol=name,
                    name=name,
                    detail="; ".join(desc_parts),
                    old_value=str(sorted(old_virtual_bases)),
                    new_value=str(sorted(new_virtual_bases)),
                    entity_id=entity_id,
                )
            )
        elif old_virt_set != new_virt_set:
            # Pure add/remove of a virtual base (not a migration from non-virtual):
            # e.g. class D : virtual A  →  class D : virtual A, virtual B
            # → TYPE_BASE_CHANGED (hierarchy changed, not just virtuality toggled)
            if (
                not changes
            ):  # don't duplicate if TYPE_BASE_CHANGED already emitted above
                changes.append(
                    make_change(
                        ChangeKind.TYPE_BASE_CHANGED,
                        symbol=name,
                        description=f"Virtual base classes changed: {name}",
                        old_value=str(old_virtual_bases),
                        new_value=str(new_virtual_bases),
                        entity_id=entity_id,
                    )
                )

    changes.extend(_diff_base_access(name, t_old, t_new, entity_id))
    return changes


#: Narrowing order for a base-class access specifier: a higher rank is more
#: restrictive, so a rank increase is a narrowing (a consumer that relied on
#: the wider access no longer compiles).
_ACCESS_RANK: dict[str, int] = {"public": 0, "protected": 1, "private": 2}


def _diff_base_access(
    name: str, t_old: RecordType, t_new: RecordType, entity_id: EntityId | None
) -> list[Change]:
    """``BASE_CLASS_ACCESS_NARROWED`` for every base present on both sides whose
    specifier became more restrictive.

    Only bases named on both sides are compared: a base that was added or
    removed is already a ``TYPE_BASE_CHANGED`` hierarchy change. Widening
    (private/protected -> public, protected -> public) is source- and
    binary-compatible and is not reported. A side whose access evidence is
    incomplete (not collected, failed, or partial), or a base whose specifier
    is not one of the three known spellings, is unknown and is never compared
    as a change.
    """
    old_f, new_f = t_old.base_access_fact, t_new.base_access_fact
    # Cheap pre-filter, before compare_facts: only two PRESENT, non-empty
    # maps can hold a narrowing (most records have no base at all), and any
    # other status is not comparable anyway.
    if not (
        old_f.status is FactStatus.PRESENT
        and new_f.status is FactStatus.PRESENT
        and old_f.value
        and new_f.value
    ):
        return []
    cmp = compare_facts(old_f, new_f, {})
    if not cmp.is_comparable or cmp.degraded:
        return []
    old_map: dict[str, str] = cmp.old_value or {}
    new_map: dict[str, str] = cmp.new_value or {}
    changes: list[Change] = []
    for base in sorted(set(old_map) & set(new_map)):
        old_acc = old_map[base]
        new_acc = new_map[base]
        if old_acc not in _ACCESS_RANK or new_acc not in _ACCESS_RANK:
            continue
        if _ACCESS_RANK[new_acc] > _ACCESS_RANK[old_acc]:
            changes.append(
                make_change(
                    ChangeKind.BASE_CLASS_ACCESS_NARROWED,
                    symbol=name,
                    name=name,
                    detail=base,
                    old=old_acc,
                    new=new_acc,
                    entity_id=entity_id,
                )
            )
    return changes
