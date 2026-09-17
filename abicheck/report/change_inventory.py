# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Change-versus-inventory split: what this release *did* vs. what it *has*.

A cross-source hygiene finding stamped
:attr:`~abicheck.policy.evidence_status.CrossSourceEvolution.PERSISTENT`
states that OLD and NEW carry the *identical* problem -- it is standing
inventory, not something the comparison observed changing. ``checker``
already refuses to charge such a finding to the verdict
(``policy.classification.excluded_from_verdict_as_persistent_hygiene``),
but every *count* a reader sees -- the one-line ``N risk (N total)``
headline and the JSON ``summary`` block alike -- still folded it in beside
genuine compatibility changes. A byte-identical rebuild of a library with
32 pre-existing ``exported_not_public`` exports therefore announced
``NO_CHANGE: 32 risk (32 total)``: a verdict and a count contradicting each
other in one line.

This module owns the split as one frozen fact so no renderer re-derives it:

* **compatibility changes** -- findings with no evolution stamp at all.
  These are what the comparison observed.
* **hygiene introduced / resolved** -- inventory this release changed.
* **hygiene persistent / not evaluated** -- inventory this release did not.

Per this package's compute/render split (``abicheck/report/AGENTS.md``),
:func:`compute_change_inventory` computes and nothing here formats.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..policy.evidence_status import CrossSourceEvolution

if TYPE_CHECKING:
    from ..checker_types import Change
    from ..policy.classification import Verdict

__all__ = [
    "ChangeInventorySplit",
    "compute_change_inventory",
    "render_change_inventory_json",
]


@dataclass(frozen=True, slots=True)
class ChangeInventorySplit:
    """Non-overlapping populations of one comparison's findings.

    ``compatibility_changes`` plus the four ``hygiene_*`` counters equals
    the report's ``summary.total_changes``; the four ``compatibility_*``
    verdict counters partition ``compatibility_changes`` over the subset
    compatibility policy actually scored, so their sum can be *lower* when
    a finding was never evaluated (ADR-049 D1).
    """

    compatibility_changes: int
    compatibility_breaking: int
    compatibility_source_breaks: int
    compatibility_risk: int
    compatibility_compatible: int
    hygiene_introduced: int
    hygiene_resolved: int
    hygiene_persistent: int
    hygiene_not_evaluated: int

    @property
    def hygiene_total(self) -> int:
        return (
            self.hygiene_introduced
            + self.hygiene_resolved
            + self.hygiene_persistent
            + self.hygiene_not_evaluated
        )

    @property
    def has_hygiene(self) -> bool:
        return self.hygiene_total > 0


_STATE_FIELD = {
    CrossSourceEvolution.INTRODUCED: "introduced",
    CrossSourceEvolution.RESOLVED: "resolved",
    CrossSourceEvolution.PERSISTENT: "persistent",
    CrossSourceEvolution.NOT_EVALUATED: "not_evaluated",
}


def compute_change_inventory(
    changes: Sequence[Change],
    evaluated: Sequence[Change],
    verdict_of: Callable[[Change], Verdict],
) -> ChangeInventorySplit:
    """Split *changes* into observed compatibility changes and standing inventory.

    *evaluated* is the compatibility-scored subset (``DiffResult.
    _evaluated_changes()``) and *verdict_of* resolves one already-evaluated
    finding's effective verdict -- the same two inputs ``build_summary``
    already has, so this never re-resolves a verdict on its own and can
    never disagree with the block beside it.
    """
    from ..policy.classification import Verdict

    hygiene = dict.fromkeys(_STATE_FIELD.values(), 0)
    compatibility = 0
    for change in changes:
        state = getattr(change, "cross_source_evolution", None)
        field = _STATE_FIELD.get(state) if state is not None else None
        if field is None:
            compatibility += 1
        else:
            hygiene[field] += 1

    buckets = dict.fromkeys(
        ("breaking", "source_breaks", "risk", "compatible"),
        0,
    )
    by_verdict = {
        Verdict.BREAKING: "breaking",
        Verdict.API_BREAK: "source_breaks",
        Verdict.COMPATIBLE_WITH_RISK: "risk",
        Verdict.COMPATIBLE: "compatible",
    }
    for change in evaluated:
        if getattr(change, "cross_source_evolution", None) is not None:
            continue
        bucket = by_verdict.get(verdict_of(change))
        if bucket is not None:
            buckets[bucket] += 1

    return ChangeInventorySplit(
        compatibility_changes=compatibility,
        compatibility_breaking=buckets["breaking"],
        compatibility_source_breaks=buckets["source_breaks"],
        compatibility_risk=buckets["risk"],
        compatibility_compatible=buckets["compatible"],
        hygiene_introduced=hygiene["introduced"],
        hygiene_resolved=hygiene["resolved"],
        hygiene_persistent=hygiene["persistent"],
        hygiene_not_evaluated=hygiene["not_evaluated"],
    )


def render_change_inventory_json(split: ChangeInventorySplit) -> dict[str, int]:
    """Project *split* into the JSON report's ``summary.change_inventory`` block."""
    return {
        "compatibility_changes": split.compatibility_changes,
        "compatibility_breaking": split.compatibility_breaking,
        "compatibility_source_breaks": split.compatibility_source_breaks,
        "compatibility_risk": split.compatibility_risk,
        "compatibility_compatible": split.compatibility_compatible,
        "hygiene_introduced": split.hygiene_introduced,
        "hygiene_resolved": split.hygiene_resolved,
        "hygiene_persistent": split.hygiene_persistent,
        "hygiene_not_evaluated": split.hygiene_not_evaluated,
    }
