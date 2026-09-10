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

"""Which findings a ``compare --no-baseline`` audit may report, and in which state.

ADR-068 D2/D3, ``docs/contribute/plans/one-comparison-product.md`` §6 Phase
2e. ``workflows.no_baseline_compare`` implements the single-build audit as a
*self-diff* (``compare(new, new, ...)``) so the real detector/suppression/
policy pipeline runs unmodified against genuine candidate-side evidence --
see that module's own docstring for why identity, rather than a change-kind
filter, is what keeps a comparison finding from being manufactured.

That construction used to carry its whole contract in one blanket
``assert not diff.changes``. The assertion was correct when ``compare()``
had no per-side stages; ADR-068 Phase 2a/2b then moved all eleven
cross-source hygiene checks (``workflows.cross_source_evolution``) and the
pattern/preprocessor pre-scan (``workflows.pattern_preprocessor_scan``)
*into* ``compare()``, where they legitimately emit findings on a
self-compared snapshot -- so every stored-snapshot audit aborted with an
unhandled ``AssertionError`` (all eleven G20 fixtures) and every live one
rendered an empty document. This module replaces that single blanket
assertion with the two separate, individually-checkable statements it was
conflating:

1. **The identity half is unchanged and still absolute.** A snapshot
   compared against itself can never produce an addition, a removal, or a
   modification. :func:`partition_no_baseline_findings` isolates exactly
   those findings, and :func:`check_no_baseline_partition` still fails on a
   non-empty identity set -- the original assertion's real intent, now
   scoped to the findings it was ever true of.
2. **The one-sided half must reach the report.** A cross-source or
   candidate-side finding is *evidence about the candidate*, which is the
   entire reason the single-build audit exists. Discarding it (or crashing
   on it) is what ADR-068 D2 promises ``--no-baseline`` replaces ``scan``
   to deliver.

**The D3 constraint on the second half.** With OLD ``declared_absent``
there is no baseline evidence at all, so a one-sided finding may only ever
be reported ``persistent`` (the self-diff's own OLD side is literally the
candidate, so the check ran and fired on both) or ``not_evaluated`` (the
check's evidence gate closed). ``introduced`` and ``resolved`` are
*structurally* unreachable on a self-diff -- both require the two sides to
disagree -- and both would be manufactured claims about a baseline this run
was told does not exist: ADR-068 D3's "reporting a pre-existing hygiene
problem as introduced merely because the baseline lacked the evidence would
be a manufactured finding, which ``vision.md`` forbids outright", in its
sharpest form, since here the baseline is not merely evidence-poor but
declared absent. :data:`NO_BASELINE_EVOLUTION_STATES` states the permitted
pair and :func:`d3_violations` finds anything outside it.

**Why a raised error and not an ``assert``.** Both invariants describe a
detector reading non-identity state -- a real defect, never a user input
error -- but ``assert`` is stripped under ``python -O``, which would turn
the identity-half violation into a silently wrong audit report rather than
a loud failure. :class:`NoBaselineInvariantError` is raised unconditionally
instead. It is deliberately *not* a ``click`` or user-facing error type: no
invocation a user can write should be able to reach it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .evidence_status import CrossSourceEvolution

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from ..checker_types import Change

__all__ = [
    "NO_BASELINE_EVOLUTION_STATES",
    "NoBaselineFindingPartition",
    "NoBaselineInvariantError",
    "check_no_baseline_partition",
    "d3_violations",
    "is_one_sided_finding",
    "partition_no_baseline_findings",
]

#: The only two :class:`~abicheck.checker_policy.CrossSourceEvolution` states
#: a ``--no-baseline`` audit may report (ADR-068 D3). ``INTRODUCED`` and
#: ``RESOLVED`` both assert something about a baseline that was declared
#: absent, and are structurally unreachable on a self-diff besides.
NO_BASELINE_EVOLUTION_STATES = frozenset(
    {CrossSourceEvolution.PERSISTENT, CrossSourceEvolution.NOT_EVALUATED}
)


class NoBaselineInvariantError(RuntimeError):
    """A ``--no-baseline`` self-diff produced a finding it structurally cannot.

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
    tripping the identity invariant below. This is deliberately *not* a
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

    *identity* must always be empty (a snapshot equals itself); *one_sided*
    is the audit's actual reportable content.
    """

    one_sided: tuple[Change, ...]
    identity: tuple[Change, ...]


def partition_no_baseline_findings(
    changes: Iterable[Change],
) -> NoBaselineFindingPartition:
    """Split *changes* into candidate-side evidence and identity-diff findings.

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


def d3_violations(changes: Iterable[Change]) -> tuple[Change, ...]:
    """Every one-sided finding in *changes* whose evolution state D3 forbids.

    Only :data:`NO_BASELINE_EVOLUTION_STATES` are permitted. A finding with
    no ``cross_source_evolution`` at all (a bare
    ``candidate_side_enrichment``) states nothing about a baseline and so
    can never violate this rule -- it is skipped rather than counted.
    """
    return tuple(
        change
        for change in changes
        if is_one_sided_finding(change)
        and change.cross_source_evolution is not None
        and change.cross_source_evolution not in NO_BASELINE_EVOLUTION_STATES
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
    """Enforce both ``--no-baseline`` invariants, raising on either violation.

    Raises :class:`NoBaselineInvariantError` when the identity half is
    non-empty (a detector read non-identity state -- the original blanket
    assertion's real intent) or when any one-sided finding carries an
    evolution state ADR-068 D3 forbids.
    """
    if partition.identity:
        raise NoBaselineInvariantError(
            "a snapshot compared against itself must never produce a comparison "
            "finding -- if this fires, a detector is reading non-identity state. "
            f"Offending findings: {_describe(partition.identity)}"
        )
    violations = d3_violations(partition.one_sided)
    if violations:
        permitted = "/".join(
            sorted(state.value for state in NO_BASELINE_EVOLUTION_STATES)
        )
        raise NoBaselineInvariantError(
            "ADR-068 D3: with OLD declared_absent a one-sided finding may only be "
            f"reported {permitted} -- never introduced or resolved, which both "
            "assert something about a baseline this run was told does not exist. "
            f"Offending findings: {_describe(violations)}"
        )
