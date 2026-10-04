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

"""The declaration-fact detector family, reading through ``SemanticIRIndex``
(ADR-063 6B, declaration-fact cohort: ``FUNC_DEPRECATED_*``,
``VAR_DEPRECATED_*``, ``METHOD_ACCESS_CHANGED``, ``VAR_ACCESS_*``,
``VAR_ALIGNMENT_CHANGED``).

Each finding is decided from ``CanonicalEntity.deprecated``/``access``/
``declared_alignment_bits`` (``model/semantic_ir_declaration_facts.py``),
whose statuses are the source ``Fact`` statuses copied verbatim. So the
gates here are the legacy ones restated over IR facts:

* deprecation compares only when both sides are ``PRESENT``, recording a
  decline when that is informative -- ``compare.fact_gate.
  both_facts_present``'s rule;
* variable access compares through ``compare_facts`` (``PRESENT``/``PARTIAL``
  on both sides), as ``var_access_changes`` did;
* alignment and method access compare whenever both sides hold a value.

The caller supplies each side's entity (from the function or variable
index it already built), and pairing stays with the caller. **This module
may not read the facts off a ``Function``/``Variable``**:
``scripts/semantic_ir_cutover.py`` forbids ``deprecated_fact``/
``access_fact``/``alignment_bits``/``alignment_bits_fact`` reads here
(``deprecated``/``access`` are also the IR facts' own names, which the
name-based scan cannot separate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..diff_helpers import make_change
from ..model.availability import FactStatus
from ..model.change_catalog.kinds import ChangeKind
from ..model.vocabulary import AccessLevel
from .declined_comparisons import record_declined
from .fact_comparison import compare_facts

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..model.identity import EntityId
    from ..model.semantic_ir import CanonicalEntity

__all__ = [
    "access_changes",
    "alignment_changes",
    "deprecation_changes",
    "is_access_narrowing",
]

_LOUD = frozenset({FactStatus.FAILED, FactStatus.PARTIAL})
_RANK = {"public": 0, "protected": 1, "private": 2}


def is_access_narrowing(old_access: str, new_access: str) -> bool:
    """Narrowing = less accessible (public->protected/private,
    protected->private); widening is backward-compatible."""
    return _RANK.get(new_access, 0) > _RANK.get(old_access, 0)


def deprecation_changes(
    mangled: str,
    name: str,
    old: CanonicalEntity | None,
    new: CanonicalEntity | None,
    *,
    entity_id: EntityId | None,
    added: ChangeKind,
    removed: ChangeKind,
) -> list[Change]:
    """A deprecation gained or lost, compared only when both sides
    established it."""
    if old is None or new is None:
        return []
    o, n = old.deprecated, new.deprecated
    if not (o.status is FactStatus.PRESENT and n.status is FactStatus.PRESENT):
        if FactStatus.PRESENT in (o.status, n.status) or {o.status, n.status} & _LOUD:
            record_declined(
                mangled,
                f"deprecated fact {o.status.value} (old) / {n.status.value} (new)",
            )
        return []
    if not o.value and n.value:
        return [
            make_change(
                added,
                symbol=mangled,
                name=name,
                detail=n.value[0],
                new_value=n.value[0],
                entity_id=entity_id,
            )
        ]
    if o.value and not n.value:
        return [
            make_change(
                removed,
                symbol=mangled,
                name=name,
                old_value=o.value[0],
                entity_id=entity_id,
            )
        ]
    return []


def access_changes(
    mangled: str,
    name: str,
    old: CanonicalEntity | None,
    new: CanonicalEntity | None,
    *,
    entity_id: EntityId | None,
    is_variable: bool,
) -> list[Change]:
    """``VAR_ACCESS_CHANGED``/``VAR_ACCESS_WIDENED`` for a variable, or
    ``METHOD_ACCESS_CHANGED`` for a narrowing method transition."""
    if old is None or new is None:
        return []
    cmp = compare_facts(old.access, new.access, AccessLevel.PUBLIC.value)
    if not cmp.is_comparable:
        return []
    o, n = cmp.old_value, cmp.new_value
    if o == n:
        return []
    narrowing = is_access_narrowing(o or "public", n or "public")
    if not is_variable:
        if not narrowing:
            return []
        kind = ChangeKind.METHOD_ACCESS_CHANGED
    else:
        kind = (
            ChangeKind.VAR_ACCESS_CHANGED
            if narrowing
            else ChangeKind.VAR_ACCESS_WIDENED
        )
    return [
        make_change(
            kind,
            symbol=mangled,
            name=name,
            old=o if o is not None else "?",
            new=n if n is not None else "?",
            entity_id=entity_id,
        )
    ]


def alignment_changes(
    mangled: str,
    name: str,
    old: CanonicalEntity | None,
    new: CanonicalEntity | None,
    *,
    entity_id: EntityId | None,
) -> list[Change]:
    """``VAR_ALIGNMENT_CHANGED`` when both sides recorded a declared
    alignment and it differs."""
    if old is None or new is None:
        return []
    o, n = old.declared_alignment_bits, new.declared_alignment_bits
    if not (o.is_present and n.is_present) or o.value == n.value:
        return []
    return [
        make_change(
            ChangeKind.VAR_ALIGNMENT_CHANGED,
            symbol=mangled,
            name=name,
            old=str(o.value),
            new=str(n.value),
            entity_id=entity_id,
        )
    ]
