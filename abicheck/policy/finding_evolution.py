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

"""ADR-068 Phase 1 item 2 (``docs/contribute/plans/one-comparison-product.md``,
"Phase 1"): the ``FindingEvolution`` correspondence primitive.

A single :func:`abicheck.checker.compare` call only ever sees one pair of
snapshots, so ``DiffResult.changes`` alone cannot say whether a finding it
just emitted also showed up in an *earlier* comparison, or whether a finding
an earlier comparison reported has since gone quiet. This module is the one
place that answers that question, given two already-produced
:class:`~abicheck.checker_types.DiffResult` objects from adjacent points in
a chain -- it never re-runs :func:`compare` and never invents a second
identity scheme: correspondence is keyed by
:func:`abicheck.finding_identity.report_finding_id`, the same
cross-run-stable per-finding fingerprint schema 2.3 already defines for
exactly this purpose ("diff two CI runs' findings").

**Deliberately not wired into any pipeline yet.** Per the plan's own Phase 1
scope note ("No CLI change in this phase"), nothing in ``compare()``,
``scan``, or any CLI command calls into this module today -- it is the
reusable primitive Phase 2+ work and ADR-066 S1's longitudinal history
(``workflows/history.py``, already landed) can build on, so that a future
N>1-comparison consumer computes evolution once, the same way, instead of
each hand-rolling its own "was this finding here last time" join.
:func:`apply_finding_evolution` is the one entry point such a consumer
calls; :func:`compute_finding_evolution`/:func:`compute_resolved_findings`
are its pure halves, exposed separately for a caller that wants the mapping
without mutating a result in place.

**Reuses ADR-067 D3's ``not_evaluated`` convention, not a second one.** With
no *previous* comparison supplied at all, every current finding is stamped
``FindingEvolution.NOT_EVALUATED`` rather than guessed as ``INTRODUCED`` (a
fabricated "this is new") or ``PERSISTENT`` (a fabricated "this was already
known") -- the same "capability never exercised reads as not evaluated,
never as an inferred zero" rule
:class:`abicheck.report.disposition_audit.NotEvaluatedDetector` already
states for a detector that never ran. See
:class:`abicheck.checker_policy.FindingEvolution`'s own docstring for the
full state vocabulary.
"""

from __future__ import annotations

from dataclasses import replace

from ..checker_types import Change, DiffResult
from ..finding_identity import report_finding_id
from .evidence_status import FindingEvolution


def _identity(change: Change) -> str:
    """The correspondence key two findings across a comparison chain are
    joined on. Never a second identity scheme -- see the module docstring."""
    return report_finding_id(change)


def compute_finding_evolution(
    current: DiffResult, previous: DiffResult | None
) -> dict[str, FindingEvolution]:
    """Classify every finding in ``current.changes`` against ``previous``.

    Returns a mapping from each current finding's identity
    (:func:`report_finding_id`) to its :class:`FindingEvolution` state. Pure
    -- reads ``current``/``previous``, mutates neither.

    ``previous=None`` (no earlier comparison in the chain at all) reports
    ``NOT_EVALUATED`` for every current finding: see the module docstring's
    "Reuses ADR-067 D3's ``not_evaluated`` convention" note.
    """
    if previous is None:
        return {_identity(c): FindingEvolution.NOT_EVALUATED for c in current.changes}
    previous_ids = {_identity(c) for c in previous.changes}
    return {
        _identity(c): (
            FindingEvolution.PERSISTENT
            if _identity(c) in previous_ids
            else FindingEvolution.INTRODUCED
        )
        for c in current.changes
    }


def compute_resolved_findings(
    current: DiffResult, previous: DiffResult | None
) -> list[Change]:
    """Findings ``previous`` reported that no longer appear in ``current``.

    Returns fresh, independent :class:`Change` copies (via
    :func:`dataclasses.replace`), each stamped
    ``evolution=FindingEvolution.RESOLVED`` -- never ``previous``'s own
    ``Change`` objects, so a caller mutating one of these copies cannot
    reach back into a still-referenced earlier ``DiffResult``.

    ``previous=None`` returns ``[]``: with no earlier comparison to check
    against, "no longer appears" cannot be stated at all -- a different fact
    from "nothing resolved", which is why this never falls back to treating
    a missing baseline as an empty one.
    """
    if previous is None:
        return []
    current_ids = {_identity(c) for c in current.changes}
    return [
        replace(c, evolution=FindingEvolution.RESOLVED)
        for c in previous.changes
        if _identity(c) not in current_ids
    ]


def apply_finding_evolution(
    current: DiffResult, previous: DiffResult | None
) -> DiffResult:
    """Stamp ``current`` in place with its evolution against ``previous``.

    Sets ``Change.evolution`` on every entry of ``current.changes`` and
    replaces ``current.resolved_findings`` with
    :func:`compute_resolved_findings`'s output. Returns ``current`` for
    chaining.

    Mutates ``current`` in place, matching every other post-processing
    pipeline step that annotates already-constructed ``Change``/
    ``DiffResult`` objects (``MarkReachability``,
    ``FilterNonPublicSurface``, ...) -- unlike those, this function is not
    itself wired into any pipeline yet (see the module docstring); a caller
    that owns a comparison chain applies it explicitly, once per step.
    """
    evolutions = compute_finding_evolution(current, previous)
    for change in current.changes:
        change.evolution = evolutions.get(
            _identity(change), FindingEvolution.NOT_EVALUATED
        )
    current.resolved_findings = compute_resolved_findings(current, previous)
    return current
