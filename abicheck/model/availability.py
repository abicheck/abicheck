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

"""Fact-availability vocabulary — the leaf half of ADR-062 D3, relocated.

ADR-063 Phase 0: this is the canonical home for `FactStatus`/`Confidence`
and their orders. It previously lived in
`abicheck.storage.availability_status`, but `Fact[T]` (`model/fact.py`)
needs this vocabulary and ADR-061's dependency direction is
`storage -> model`, never the reverse — so `model/` cannot import from
`storage/`. `abicheck.storage.availability_status` is now a thin
re-export shim over this module, kept for one release so existing
`storage.*` imports keep working; new code should import from here.
`FactAvailability` (the ledger record) legitimately depends on `model` and
stays in `storage/`.

Everything here is *vocabulary* — the statuses, the confidence levels, and
the total orders that decide which of two of them survives a narrowing —
and none of it needs to know what a stored record, a ledger, or a
dataclass field looks like; it depends on nothing but the standard
library.

Keeping the orders here rather than beside their callers matters for the
same reason they are tuples rather than ``enum`` declaration order: the
severity ranking is a deliberate statement (``NOT_APPLICABLE`` is not a gap,
so it sits *below* ``PRESENT``) that must be read and changed in one place.
"""

from __future__ import annotations

import enum

__all__ = [
    "ASSERTS_NO_PRODUCER",
    "COMPARABLE_STATUSES",
    "CONFIDENCE_ORDER",
    "Confidence",
    "GAP_STATUSES",
    "STATUS_ORDER",
    "FactStatus",
    "worse_confidence",
    "worse_status",
]


class FactStatus(enum.Enum):
    """Why a fact family is, or is not, present in stored evidence.

    Deliberately six *distinct* members rather than a boolean plus a note:
    a reader that collapses ``UNSUPPORTED`` and ``FAILED`` into one "no data"
    case cannot tell a permanent producer limitation (re-running changes
    nothing) from a transient extraction error (re-running may well fix it),
    and a reader that collapses either into ``PRESENT`` reintroduces the bug
    this whole module exists to close. Deliberately has no seventh
    "confirmed absent" member: per ``PRESENT``'s own meaning below, a
    confirmed absence is ``PRESENT`` carrying an empty/``None`` value, not a
    distinct status — see :class:`abicheck.model.fact.Fact`.
    """

    #: The producer ran, covered the requested scope, and established the
    #: facts — including establishing that a collection is legitimately empty.
    PRESENT = "present"
    #: The producer ran but covered only part of the requested scope. Usable,
    #: with reduced confidence; the uncovered part is unknown, not absent.
    PARTIAL = "partial"
    #: The producer was never invoked for this family (e.g. the run was
    #: capped at a shallower evidence level). Says nothing about the facts.
    NOT_COLLECTED = "not_collected"
    #: This producer cannot express this family at all. Re-running the same
    #: producer will never help; a different producer might.
    UNSUPPORTED = "unsupported"
    #: The producer was invoked and errored. Diagnostics should say how.
    FAILED = "failed"
    #: The family is meaningless for this artifact kind (e.g. vtables for a
    #: C-only artifact). Not a gap — nothing is missing.
    NOT_APPLICABLE = "not_applicable"


class Confidence(enum.Enum):
    """How much weight a consumer may put on a ``PRESENT``/``PARTIAL`` fact."""

    HIGH = "high"
    REDUCED = "reduced"
    UNKNOWN = "unknown"


#: Statuses a comparison may draw a compatibility conclusion from. Every other
#: status means the evidence is absent for a reason, so a comparison that
#: needs the family must degrade explicitly (``NOT_COMPARABLE`` or a
#: reduced-confidence result) rather than read an empty collection as "fine".
COMPARABLE_STATUSES = frozenset({FactStatus.PRESENT, FactStatus.PARTIAL})

#: Statuses that mean "evidence is missing for a reason". Deliberately not the
#: complement of :data:`COMPARABLE_STATUSES`: ``NOT_APPLICABLE`` is neither
#: usable evidence nor a gap — it is a ledger stating that there is nothing
#: here to be missing, which is an answer rather than an absence.
GAP_STATUSES = frozenset(
    {FactStatus.NOT_COLLECTED, FactStatus.UNSUPPORTED, FactStatus.FAILED}
)

#: The status asserting that no producer ran, so a merge must not attach one.
#: Distinct from :data:`GAP_STATUSES` on purpose: ``UNSUPPORTED`` and
#: ``FAILED`` are gaps *with* a producer worth naming — one was asked and
#: could not answer, or ran and failed.
ASSERTS_NO_PRODUCER = frozenset({FactStatus.NOT_COLLECTED})


#: Status severity order, worst last. ``narrowed`` picks the later of two.
#: ``NOT_APPLICABLE`` sits *below* ``PRESENT`` rather than at the top: a family
#: that does not apply to an artifact is not a gap, so a per-entity
#: "not applicable" must not drag a family-level ``PRESENT`` down to it, and a
#: family-level "not applicable" is superseded by an entity that really does
#: carry the fact.
STATUS_ORDER: tuple[FactStatus, ...] = (
    FactStatus.NOT_APPLICABLE,
    FactStatus.PRESENT,
    FactStatus.PARTIAL,
    FactStatus.NOT_COLLECTED,
    FactStatus.UNSUPPORTED,
    FactStatus.FAILED,
)

CONFIDENCE_ORDER: tuple[Confidence, ...] = (
    Confidence.HIGH,
    Confidence.REDUCED,
    Confidence.UNKNOWN,
)


def worse_status(left: FactStatus, right: FactStatus) -> FactStatus:
    """The status that survives a narrowing, per :data:`STATUS_ORDER`.

    "Worse" is that order's ranking, which is deliberately not "least
    evidence": ``NOT_APPLICABLE`` sits *below* ``PRESENT``, so a family
    declared inapplicable is superseded by an entity that really does carry
    the fact rather than dragging it down. Reading this as a plain severity
    scale is the mistake the order's own comment exists to prevent.
    """
    return max(left, right, key=STATUS_ORDER.index)


def worse_confidence(left: Confidence, right: Confidence) -> Confidence:
    """The lower of two confidence levels, per :data:`CONFIDENCE_ORDER`.

    Deliberately *not* the whole rule for merging confidence: a record whose
    status carries no usable evidence has no confidence worth propagating,
    so :func:`abicheck.storage.availability._merged_confidence` decides which
    records vote before reaching for this. Applied unconditionally, it let a
    ``NOT_APPLICABLE``/``UNKNOWN`` family degrade a real ``PRESENT``/``HIGH``
    override to ``UNKNOWN``.
    """
    return max(left, right, key=CONFIDENCE_ORDER.index)
