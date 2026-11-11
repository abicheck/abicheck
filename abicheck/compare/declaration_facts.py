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
    from ..model.change import Change
    from ..model.fact import Fact
    from ..model.identity import EntityId
    from ..model.semantic_ir import CanonicalEntity
    from ..model.snapshot import AbiSnapshot

__all__ = [
    "access_changes",
    "alignment_changes",
    "deprecation_changes",
    "is_access_narrowing",
    "records_variable_access",
]

_LOUD = frozenset({FactStatus.FAILED, FactStatus.PARTIAL})
_RANK = {"public": 0, "protected": 1, "private": 2}


#: The extractor generation from which the clang header backend records a
#: variable's member access (a static data member's ``private:``/
#: ``protected:``). An older clang snapshot spelled every variable ``public``,
#: so reading its access would report a private static member as narrowed.
CLANG_VARIABLE_ACCESS_GENERATION = 54


def records_variable_access(snapshot: AbiSnapshot) -> bool:
    """Whether *snapshot*'s header backend recorded each variable's access.

    castxml always has; clang has since ``CLANG_VARIABLE_ACCESS_GENERATION``
    (``fact_generations`` is ``None`` for a snapshot this process extracted).
    A ``hybrid`` merge mixes both and stays excluded.
    """
    if snapshot.ast_producer == "castxml":
        return True
    if snapshot.ast_producer != "clang":
        return False
    gens = snapshot.fact_generations
    return gens is None or gens[0] >= CLANG_VARIABLE_ACCESS_GENERATION


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
    placement: tuple[int | None, int | None] = (None, None),
) -> list[Change]:
    """``VAR_ALIGNMENT_CHANGED`` when both sides recorded a declared
    alignment and it provably differs.

    Each side is an interval. An exact (``PRESENT``) value is itself. A
    ``PARTIAL`` value is a lower bound, closed above by *placement* when the
    object's address is known (an object aligned to ``A`` sits at a multiple
    of ``A``, so its address alignment bounds ``A`` from above). The sides
    differ provably exactly when their intervals are disjoint: an exact value
    below a bound, or above the address an unknown declaration sits at.
    """
    if old is None or new is None:
        return []
    o, n = old.declared_alignment_bits, new.declared_alignment_bits
    if not (o.is_present and n.is_present) or o.value == n.value:
        return []
    o_span = _interval(o, placement[0])
    n_span = _interval(n, placement[1])
    if o_span is None or n_span is None or not _disjoint(o_span, n_span):
        return []
    return [
        make_change(
            ChangeKind.VAR_ALIGNMENT_CHANGED,
            symbol=mangled,
            name=name,
            old=_spell(o_span, n_span),
            new=_spell(n_span, o_span),
            entity_id=entity_id,
        )
    ]


#: (low, high) in bits; ``high`` is None when nothing bounds it from above.
_Span = tuple[int, int | None]


def _interval(fact: Fact[int], placement: int | None) -> _Span | None:
    """One side's possible declared alignments, or None when unreadable."""
    if fact.value is None:
        return None
    if fact.status is not FactStatus.PARTIAL:
        return (fact.value, fact.value)
    high = placement if placement is not None and placement >= fact.value else None
    return (fact.value, high)


def _disjoint(a: _Span, b: _Span) -> bool:
    return (a[1] is not None and a[1] < b[0]) or (b[1] is not None and b[1] < a[0])


def _spell(side: _Span, other: _Span) -> str:
    """*side*'s value as the proof uses it: exact, or the bound that excludes
    the other side (``<=`` its address alignment, ``>=`` its lower bound)."""
    low, high = side
    if high == low:
        return str(low)
    if high is not None and high < other[0]:
        return f"<={high}"
    return f">={low}"
