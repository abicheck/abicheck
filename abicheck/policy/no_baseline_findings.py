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

"""Which findings a ``compare --no-baseline`` audit may report.

ADR-068 D2/D3, ``docs/contribute/plans/one-comparison-product.md`` §6 Phase
2e. ``workflows.no_baseline_compare`` runs the single-build audit through
the one comparison verb with the baseline absent
(``checker.compare(None, new, ...)``), so the real detector/suppression/
policy pipeline runs unmodified against genuine candidate-side evidence
while the stages that need two sides simply do not run.

That leaves exactly one invariant for this module to state, and it is the
structural one the original blanket ``assert not diff.changes`` was really
after: **a run with no baseline can produce no comparison finding.** No
addition, removal or modification can be derived from a delta that was
never evaluated, so :func:`partition_no_baseline_findings` isolates those
and :func:`check_no_baseline_partition` fails on a non-empty set -- which
now means a detector read state it structurally cannot have, not that an
identity comparison behaved unexpectedly. The candidate-side half is the
audit's reportable content and reaches the report untouched.

**The second rule set is gone, on purpose.** This module used to also
carry ``NO_BASELINE_EVOLUTION_STATES``/``d3_violations``: an allowlist
permitting a one-sided finding to read ``persistent`` *or*
``not_evaluated``, and a checker for anything outside it. ``persistent``
was only ever reachable because the audit was implemented as a self-diff
-- the candidate compared against a copy of itself, so every check fired
on "both" sides and the fold called the result present-on-both. It claimed
history nobody observed. With the baseline genuinely absent, ``compare()``
stamps :attr:`~abicheck.policy.evidence_status.CrossSourceEvolution.
NOT_EVALUATED` on every candidate-side finding by construction
(``workflows.cross_source_evolution.
compute_candidate_cross_source_findings``), so there is no second set of
states to permit and nothing for a mode-specific rule to police. Deleting
it is the point: ADR-068 D3's real content -- never report a pre-existing
condition as *introduced* or *resolved* against evidence that cannot
support either -- is now a property of how the run is constructed, and a
rule that restates a structural guarantee is a second place to keep in
sync for no added safety.

**Why a raised error and not an ``assert``.** The surviving invariant
describes a detector reading state it cannot have -- a real defect, never a
user input error -- but ``assert`` is stripped under ``python -O``, which
would turn it into a silently wrong audit report rather than a loud
failure. :class:`NoBaselineInvariantError` is raised unconditionally
instead. It is deliberately *not* a ``click`` or user-facing error type: no
invocation a user can write should be able to reach it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..checker_types import Change

__all__ = [
    "NoBaselineFindingPartition",
    "NoBaselineInvariantError",
    "check_no_baseline_partition",
    "is_one_sided_finding",
    "partition_no_baseline_findings",
]


class NoBaselineInvariantError(RuntimeError):
    """A ``--no-baseline`` run produced a finding it structurally cannot.

    Signals a defect in a detector or in the audit pipeline itself -- never
    anything a user's invocation can cause -- so it is a plain
    :class:`RuntimeError` subclass rather than a ``click`` usage error.
    """


def is_one_sided_finding(change: Change) -> bool:
    """Whether *change* is candidate-side evidence rather than a comparison finding.

    Two independent markers, both set by ``compare()`` itself and neither
    ever set on an ordinary two-sided comparison finding (see
    ``checker_types.Change``'s own field comments):

    * ``cross_source_evolution`` -- the eleven cross-source hygiene checks
      and the pattern/preprocessor pre-scan, which diff one snapshot's
      evidence *sources against each other* and carry no baseline of their
      own (ADR-068 D3).
    * ``candidate_side_enrichment`` -- a check meaningful only on the
      candidate, today the ``--abi3`` limited-API audit
      (``workflows.abi3_audit``), which ADR-068 D3 requires to ride the
      same result document "marked as such".

    Checked as a disjunction rather than on ``cross_source_evolution``
    alone so a candidate-side check that grows a second marker, or an
    ``--abi3`` audit reached through this path, is reported rather than
    tripping the comparison-half invariant below. This is deliberately *not* a
    ``ChangeKind`` allowlist: a kind-keyed filter is a second place every
    new candidate-side ``ChangeKind`` would have to be taught about
    no-baseline mode, and would drift out of sync with the detector
    registry exactly the way ``no_baseline_compare``'s module docstring
    already argues a change-kind filter would.
    """
    return change.cross_source_evolution is not None or change.candidate_side_enrichment


@dataclass(frozen=True)
class NoBaselineFindingPartition:
    """The two halves :func:`partition_no_baseline_findings` separates.

    *identity* must always be empty (no delta was evaluated, so no
    comparison finding can exist); *one_sided* is the audit's actual
    reportable content.
    """

    one_sided: tuple[Change, ...]
    identity: tuple[Change, ...]


def partition_no_baseline_findings(
    changes: Iterable[Change],
) -> NoBaselineFindingPartition:
    """Split *changes* into candidate-side evidence and comparison findings.

    Order-preserving within each half, so a report renders findings in the
    order ``compare()`` emitted them rather than in an order this partition
    happened to impose.
    """
    one_sided: list[Change] = []
    identity: list[Change] = []
    for change in changes:
        (one_sided if is_one_sided_finding(change) else identity).append(change)
    return NoBaselineFindingPartition(
        one_sided=tuple(one_sided), identity=tuple(identity)
    )


def _describe(changes: Sequence[Change]) -> str:
    return ", ".join(
        f"{c.kind.value}({c.symbol or '-'}"
        + (
            f", {c.cross_source_evolution.value}"
            if c.cross_source_evolution is not None
            else ""
        )
        + ")"
        for c in changes[:8]
    ) + (f", ... [{len(changes)} total]" if len(changes) > 8 else "")


def check_no_baseline_partition(
    partition: NoBaselineFindingPartition,
) -> None:
    """Enforce the ``--no-baseline`` invariant, raising on a violation.

    Raises :class:`NoBaselineInvariantError` when the comparison half is
    non-empty: a run with no baseline evaluated no delta, so a detector
    that produced an addition, removal or modification read state it
    cannot have.
    """
    if partition.identity:
        raise NoBaselineInvariantError(
            "a run with no baseline must never produce a comparison finding -- "
            "no delta was evaluated, so if this fires a detector is reading "
            "state it structurally cannot have. Offending findings: "
            f"{_describe(partition.identity)}"
        )
