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

"""``compare_facts`` — deciding whether a pair of ``Fact[T]``\\ s may be
compared at all (ADR-063 Phase 5B, "Fact semantic consumption").

Lives in ``compare/``, not ``model/``: ``model/AGENTS.md``'s own scoped
contract says that package answers "what is this fact" and never "does it
differ" — the exact question this module answers for two facts, one old and
one new. ``compare/AGENTS.md`` is the matching "are these two the same,
what changed" package, and it may depend on ``model`` for the ``Fact``/
``FactStatus`` shapes this module reads (Codex review on PR #1033, moved
out of ``model/fact.py`` where the primitive first landed).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from typing import Generic, TypeVar

from ..model.availability import FactStatus
from ..model.fact import Fact

__all__ = [
    "FactComparability",
    "FactComparison",
    "compare_facts",
]

T = TypeVar("T")


class FactComparability(enum.Enum):
    """What a detector may safely infer from a pair of ``Fact[T]``\\ s
    (ADR-063 Phase 5B — "Fact semantic consumption").

    ``model.resolved_fact_value`` deliberately collapses every non-
    ``PRESENT``/``PARTIAL`` status to the same caller-supplied default as a
    ``PRESENT``, confirmed-empty value — safe *only* for the specific
    bridged fields whose legacy sibling that default already equals (see
    that function's own docstring). A detector that wants to tell "old and
    new both say this is really empty" apart from "one side's evidence
    never arrived" — the distinction ``Fact[T]`` exists to make
    representable — compares the *pair* through :func:`compare_facts`
    instead, and branches on this enum rather than reading a
    possibly-fabricated value.
    """

    #: Both sides carry usable evidence (``PRESENT``/``PARTIAL``) — the
    #: detector may compare ``old_value``/``new_value`` for a real change.
    COMPARABLE = "comparable"
    #: At least one side's evidence gap makes the pair unsafe to compare
    #: (``NOT_COLLECTED``/``FAILED`` on either side, or a mismatched
    #: ``NOT_APPLICABLE``) — the difference, if any, is unknown, not
    #: confirmed. A detector must decline to emit a finding from this pair
    #: rather than treat the missing side as "confirmed empty".
    INCOMPLETE = "incomplete"
    #: At least one side's producer can never express this fact family
    #: (``UNSUPPORTED``) — re-running changes nothing, so the detector for
    #: this family is simply not applicable to this pair.
    UNSUPPORTED = "unsupported"
    #: Both sides agree the family is meaningless for this artifact kind
    #: (``NOT_APPLICABLE``) — a confirmed non-finding, not a gap.
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class FactComparison(Generic[T]):
    """The result of :func:`compare_facts` — a detector-meaning verdict for
    one ``(old_fact, new_fact)`` pair, plus the resolved values when (and
    only when) comparing them is actually safe.

    ``old_value``/``new_value`` are populated only when ``comparability`` is
    :attr:`FactComparability.COMPARABLE` — reading them for any other
    comparability is a caller bug (they are ``None`` in every other case,
    indistinguishable from a legitimately-``None`` present value, which is
    exactly why :attr:`is_comparable` must be checked first).
    """

    comparability: FactComparability
    old_value: T | None = None
    new_value: T | None = None
    #: ``True`` when either side's status was ``PARTIAL`` rather than
    #: ``PRESENT`` — the comparison is still safe (partial evidence still
    #: covers *some* of the requested scope), but a caller emitting a
    #: finding from a degraded comparison may want to say so.
    degraded: bool = False
    #: Human-readable reason, populated for every non-``COMPARABLE`` result
    #: (diagnostic text only, never parsed).
    reason: str = ""

    @property
    def is_comparable(self) -> bool:
        return self.comparability is FactComparability.COMPARABLE


def _comparability_side(status: FactStatus) -> str:
    """Classify one side's ``FactStatus`` into the three kinds
    :func:`compare_facts` combines: ``"available"`` (usable evidence),
    ``"unsupported"`` (permanent producer limitation), ``"not_applicable"``
    (confirmed meaningless), or ``"incomplete"`` (a transient or scope gap
    — ``NOT_COLLECTED``/``FAILED``, the two statuses that say nothing about
    the actual facts either way).
    """
    if status in (FactStatus.PRESENT, FactStatus.PARTIAL):
        return "available"
    if status is FactStatus.UNSUPPORTED:
        return "unsupported"
    if status is FactStatus.NOT_APPLICABLE:
        return "not_applicable"
    return "incomplete"  # NOT_COLLECTED, FAILED


def compare_facts(
    old_fact: Fact[T] | None, new_fact: Fact[T] | None, default: T
) -> FactComparison[T]:
    """The one place a detector decides whether an ``(old_fact, new_fact)``
    pair may be compared at all, and what it means when it can't (ADR-063
    Phase 5B's per-family ``FactStatus`` -> detector-meaning table, made a
    shared primitive instead of a per-detector reimplementation):

    | old status | new status | comparability | meaning |
    |---|---|---|---|
    | present/partial | present/partial | ``COMPARABLE`` | compare ``old_value``/``new_value`` |
    | not_collected/failed | (either) | ``INCOMPLETE`` | evidence gap, not a confirmed difference |
    | unsupported | (either) | ``UNSUPPORTED`` | this family isn't observable for this pair |
    | not_applicable | not_applicable | ``NOT_APPLICABLE`` | confirmed meaningless, not a gap |
    | not_applicable | (available/incomplete) | ``INCOMPLETE`` | applicability itself disagrees — unsafe to assume either reading |

    ``unsupported`` outranks ``incomplete`` (a permanent producer gap is a
    more specific, more actionable diagnosis than a transient one), and
    both outrank a lone-sided ``not_applicable`` (an artifact-kind mismatch
    between two sides that are supposed to be the same declaration is itself
    evidence something is off, not evidence of nothing).

    A missing ``Fact[T]`` (``None``) is treated as ``NOT_COLLECTED`` — the
    same "caller passed nothing yet" reading ``model.resolved_fact_value``
    documents for the direct-construction bridge.
    """
    old_status = old_fact.status if old_fact is not None else FactStatus.NOT_COLLECTED
    new_status = new_fact.status if new_fact is not None else FactStatus.NOT_COLLECTED
    old_kind = _comparability_side(old_status)
    new_kind = _comparability_side(new_status)

    if old_kind == "unsupported" or new_kind == "unsupported":
        return FactComparison(
            FactComparability.UNSUPPORTED,
            reason="producer cannot express this fact family on at least one side",
        )
    if old_kind == "not_applicable" and new_kind == "not_applicable":
        return FactComparison(
            FactComparability.NOT_APPLICABLE,
            reason="both sides: fact family not applicable to this artifact kind",
        )
    if old_kind == "not_applicable" or new_kind == "not_applicable":
        return FactComparison(
            FactComparability.INCOMPLETE,
            reason="applicability disagrees between old and new",
        )
    if old_kind == "incomplete" or new_kind == "incomplete":
        bits = []
        if old_kind == "incomplete":
            bits.append(f"old: {old_status.value}")
        if new_kind == "incomplete":
            bits.append(f"new: {new_status.value}")
        return FactComparison(FactComparability.INCOMPLETE, reason="; ".join(bits))

    degraded = old_status is FactStatus.PARTIAL or new_status is FactStatus.PARTIAL
    old_value = old_fact.value_or(default) if old_fact is not None else default
    new_value = new_fact.value_or(default) if new_fact is not None else default
    return FactComparison(
        FactComparability.COMPARABLE,
        old_value=old_value,
        new_value=new_value,
        degraded=degraded,
    )
