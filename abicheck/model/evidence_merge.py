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

"""The one merge rule for evidence (design-hardening Phase 1, ADR-063 Phase 5B).

Several readings of the same question -- two header-AST backends, several
translation units, a producer and a fallback, a stored and a recomputed
record -- are combined here and nowhere else. Every hand-written merge this
codebase grew (``a or b``, ``list_a or []``, "take whichever is present")
collapsed one distinction or another; the family they produced (F1, see
``docs/contribute/plans/design-hardening-from-defect-families.md``) is
"an unread input consumed as a definite value". The rule:

* **unknown ⊕ absent = unknown.** A reading that did not complete
  (``NOT_COLLECTED``/``UNSUPPORTED``/``FAILED``, or a ``PARTIAL`` reading
  that did not see the thing) cannot be outvoted into "absent" by a reading
  that did complete: the incomplete one might have held it.
* **Only a completed read yields absent.** A merged negative requires every
  contributing reading to be ``PRESENT`` and negative.
* **Present wins.** A positive observation is a positive observation
  whatever the other readings say -- ``PRESENT``/``PARTIAL`` saying *yes*
  makes the merge *yes*. For a negative to lose to it, that negative was a
  completed read by construction (an unread side holds no negative at all).
* ``NOT_APPLICABLE`` is the identity: a reading for which the question is
  meaningless adds nothing. Only all-``NOT_APPLICABLE`` stays so.

These are Kleene's three-valued disjunction for :func:`merge_presence`, and
its per-element lift for :func:`merge_collection`: commutative,
associative and idempotent, so the result never depends on how many
readings there were or in which order a caller folded them
(``tests/test_evidence_merge_properties.py`` states each law against an
independently written oracle).

When no reading is usable, the merged status is the most actionable of the
unknown ones -- ``FAILED`` (re-running may fix it) over ``UNSUPPORTED``
(another producer might) over ``PARTIAL`` with no value (part was read)
over ``NOT_COLLECTED`` (nobody looked). The order
is fixed so the merge stays commutative. Diagnostics are the sorted union of
every reading's, and ``producer`` survives only when every reading names
the same one (an unattributed reading makes the merge unattributed).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypeVar

from .availability import FactStatus
from .fact import Fact

__all__ = [
    "is_completed_read",
    "merged_capture_fact",
    "merge_collection",
    "merge_presence",
    "presence_in",
]

T = TypeVar("T")

#: Statuses that carry no reading, most actionable first.
#: ``PARTIAL`` here is a partial reading that did not answer the question
#: (it holds no *yes*), merged as ``PARTIAL`` carrying no value.
_UNKNOWN_PRIORITY: tuple[FactStatus, ...] = (
    FactStatus.FAILED,
    FactStatus.UNSUPPORTED,
    FactStatus.PARTIAL,
    FactStatus.NOT_COLLECTED,
)


def is_completed_read(fact: Fact[T]) -> bool:
    """Whether *fact* is a finished read that may support an absence."""
    return fact.status is FactStatus.PRESENT


def _meta(facts: tuple[Fact[T], ...]) -> tuple[tuple[str, ...], str | None]:
    diagnostics = tuple(sorted({d for f in facts for d in f.diagnostics}))
    producers = {f.producer for f in facts}
    producer = next(iter(producers)) if len(producers) == 1 else None
    return diagnostics, producer


def _unknown(facts: tuple[Fact[T], ...]) -> Fact[T]:
    diagnostics, producer = _meta(facts)
    statuses = {f.status for f in facts} - {FactStatus.NOT_APPLICABLE} or {
        FactStatus.NOT_APPLICABLE
    }
    for status in _UNKNOWN_PRIORITY:
        if status in statuses:
            return Fact._make(status, None, diagnostics, producer)
    if statuses == {FactStatus.NOT_APPLICABLE}:
        return Fact._make(FactStatus.NOT_APPLICABLE, None, diagnostics, producer)
    # A malformed reading (``PRESENT`` with no value): nobody established it.
    return Fact._make(FactStatus.NOT_COLLECTED, None, diagnostics, producer)


def _relevant(facts: Iterable[Fact[T]]) -> tuple[Fact[T], ...]:
    collected = tuple(facts)
    if not collected:
        raise ValueError("an evidence merge needs at least one reading")
    return collected


def merge_presence(*facts: Fact[bool]) -> Fact[bool]:
    """Merge readings of one yes/no question ("is X there?").

    *Yes* from any usable reading wins; *no* needs every reading to be a
    completed ``PRESENT(False)``; anything else is the merged unknown.
    """
    readings = _relevant(facts)
    diagnostics, producer = _meta(readings)
    if any(f.is_present and f.value is True for f in readings):
        return Fact._make(FactStatus.PRESENT, True, diagnostics, producer)
    applicable = [f for f in readings if f.status is not FactStatus.NOT_APPLICABLE]
    if applicable and all(
        f.status is FactStatus.PRESENT and f.value is False for f in applicable
    ):
        return Fact._make(FactStatus.PRESENT, False, diagnostics, producer)
    return _unknown(readings)


def merge_collection(
    *facts: Fact[frozenset[T]],
) -> Fact[frozenset[T]]:
    """Merge readings of one collection ("which X are there?").

    The value is the union of every usable reading's members. The status is
    ``PRESENT`` only when every applicable reading completed -- then an
    element missing from the union is a confirmed absence. With a usable
    reading but an incomplete one alongside, it is ``PARTIAL``: members are
    confirmed, non-members are unknown (read them through
    :func:`presence_in`). With no usable reading it is the merged unknown.
    """
    readings = _relevant(facts)
    diagnostics, producer = _meta(readings)
    applicable = tuple(f for f in readings if f.status is not FactStatus.NOT_APPLICABLE)
    usable = [f for f in applicable if f.is_present]
    if not usable:
        return _unknown(readings)
    union: frozenset[T] = frozenset().union(*(f.value or frozenset() for f in usable))
    status = (
        FactStatus.PRESENT
        if all(f.status is FactStatus.PRESENT for f in applicable)
        else FactStatus.PARTIAL
    )
    return Fact._make(status, union, diagnostics, producer)


def presence_in(collection: Fact[frozenset[T]], item: T) -> Fact[bool]:
    """Answer "is *item* there?" from a collection reading.

    Membership is a positive observation from any usable reading; absence is
    stated only by a completed one. An incomplete reading that lacks *item*
    answers ``PARTIAL`` with no value -- unknown, never ``False``.
    """
    if collection.is_present:
        if item in (collection.value or frozenset()):
            return Fact.present(True)
        if collection.status is FactStatus.PRESENT:
            return Fact.present(False)
        return Fact._make(
            FactStatus.PARTIAL,
            None,
            collection.diagnostics + ("absence-not-established: incomplete read",),
            collection.producer,
        )
    return Fact._make(
        collection.status, None, collection.diagnostics, collection.producer
    )


def merged_capture_fact(
    partial_diagnostic: str,
    merged: list[T] | None,
    *captures: list[T] | None,
) -> Fact[list[T]]:
    """The fact for a value merged from optional captures (``None`` = not
    captured), status by :func:`merge_collection`.

    *merged* is the caller's own merged value (it may validate or filter the
    union); this decides only how much of it is established: every capture
    read gives ``PRESENT``, some read gives ``PARTIAL`` (stamped with
    *partial_diagnostic*), none read gives ``NOT_COLLECTED``.
    """
    status = merge_collection(
        *(
            Fact.not_collected() if c is None else Fact.present(frozenset(c))
            for c in captures
        )
    ).status
    if merged is None or status not in (FactStatus.PRESENT, FactStatus.PARTIAL):
        return Fact.not_collected()
    if status is FactStatus.PARTIAL:
        return Fact.partial(merged, partial_diagnostic)
    return Fact.present(merged)
