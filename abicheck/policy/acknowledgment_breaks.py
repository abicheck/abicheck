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

"""ADR-067 D5 (``acknowledgments.breaks: accept``): which acknowledged breaks
stop contributing to the exit code.

The rule is stated once here so the ledger that records the acceptance
(:func:`~abicheck.policy.disposition_acknowledgment.resolve_acknowledgments`) and the
exit fold that acts on it (:func:`~abicheck.policy.exit_decision.
resolve_compare_exit_decision`) cannot disagree about which changes were
accepted.

An accepted break is a *disposition*, not an edit to the comparison: its
ledger record moves from ``gating`` to ``non_gating`` with application point
``acknowledgment``, its ``verdict`` and ``changes`` entry are untouched, and the
release recommendation still reads it as a break. Only the compatibility exit
fold drops it, and only when the run opted in with ``breaks: accept``.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from typing import Any

from ..model.change_catalog.registry import Verdict

__all__ = [
    "ACCEPTED_BREAK_APPLICATION_POINT",
    "ACCEPTABLE_BREAK_VERDICTS",
    "accepted_break_ids",
    "break_verdict_of",
    "gate_population",
]

#: The application point an accepted break's disposition is recorded under.
ACCEPTED_BREAK_APPLICATION_POINT = "acknowledgment"

#: The verdict classes ``breaks: accept`` may accept. A compatible or risk
#: finding never gates a break's exit, so an acknowledgment cannot lower it;
#: it stays exactly as it was.
ACCEPTABLE_BREAK_VERDICTS: frozenset[str] = frozenset(
    {Verdict.BREAKING.value, Verdict.API_BREAK.value}
)


def break_verdict_of(change: Any, result: Any, *, today: date | None = None) -> str:
    """The change's effective verdict class value (``"BREAKING"``, ...).

    Read off the same per-finding resolution the verdict and the gate use
    (``effective_verdict_for_change`` over the result's own policy, kind sets
    and policy file), never a second copy of it. ``resolve_verdict_classes``
    only stamps suppressed records, so a gating record's class is computed
    here, at the one point ``breaks: accept`` needs it.
    """
    from .evaluate import effective_kind_sets
    from .reclassify import effective_verdict_for_change

    verdict = effective_verdict_for_change(
        change,
        policy=result.policy,
        kind_sets=effective_kind_sets(result),
        policy_file=result.policy_file,
        today=today,
    )
    return str(verdict.value)


def accepted_break_ids(result: Any) -> frozenset[int]:
    """``id()`` of every change this run accepted as a break (empty by default).

    Empty for every run that did not state ``breaks: accept``, and for a
    result with no persisted ledger (a hand-built ``DiffResult``).
    """
    ledger = getattr(result, "disposition_ledger", None)
    if ledger is None:
        return frozenset()
    # `_anchors` is index-aligned with `_records` (the close path reads both
    # the same way): the change each accepted disposition was recorded for.
    accepted: Iterable[object] = (
        change
        for record, change in zip(ledger._records, ledger._anchors)  # noqa: SLF001
        if record.application_point == ACCEPTED_BREAK_APPLICATION_POINT
    )
    return frozenset(id(change) for change in accepted)


def gate_population(result: Any) -> list[Any]:
    """The changes the compatibility exit is decided over.

    ``result.changes`` minus the accepted breaks. Identical to
    ``list(result.changes)`` whenever nothing was accepted, so every run that
    did not opt in reads the same population it always did.
    """
    changes = list(getattr(result, "changes", None) or ())
    accepted = accepted_break_ids(result)
    if not accepted:
        return changes
    return [change for change in changes if id(change) not in accepted]
