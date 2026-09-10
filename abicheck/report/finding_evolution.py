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

"""ADR-068 Phase 1 item 2's report half: project ``FindingEvolution`` state
already recorded on a :class:`~abicheck.checker_types.DiffResult` into every
report view.

The *primitive* that decides ``introduced``/``resolved``/``persistent``/
``not_evaluated`` for an N>1-comparison chain lives in
``policy.finding_evolution`` (:func:`abicheck.policy.finding_evolution.
apply_finding_evolution`) -- this module never recomputes that decision. It
only reads whatever ``Change.evolution``/``DiffResult.resolved_findings``
already carry and projects them into a frozen, plain-value struct
(:func:`compute_finding_evolution_summary`), per this package's own
``compute_*``/``render_*`` split (``abicheck/report/AGENTS.md``) -- the same
shape ``report.disposition_audit`` already uses for a comparable
run-wide-audit block.

JSON is the only wired projection so far (:func:`add_finding_evolution`);
Markdown/HTML support is deliberately deferred to a follow-up PR (see the
plan item's own "Markdown/HTML can be a second PR" note), matching this
package's "a section that does not exist is a ``None`` from ``compute_*``"
convention -- a renderer with no support yet simply never calls
:func:`compute_finding_evolution_summary` rather than emitting a partial or
guessed block.

A plain, single ``compare()`` run never populates ``Change.evolution``
beyond its ``NOT_EVALUATED`` default (see
:mod:`abicheck.policy.finding_evolution`'s module docstring), so this
block's counts read ``not_evaluated: <total>`` and ``resolved: []``
everywhere until a caller with real chain context (``workflows/history.py``
and future siblings) applies the primitive first -- stated explicitly, per
ADR-067 D3's convention, rather than the field being silently absent from
the report.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..policy.evidence_status import FindingEvolution

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..checker_types import Change, DiffResult


@dataclass(frozen=True)
class ResolvedFindingEntry:
    """One ``FindingEvolution.RESOLVED`` finding, JSON-safe.

    Deliberately not the full ``reporter._change_to_dict`` shape (severity,
    gate contribution, impact assessment, ...): a resolved finding is a
    historical fact about a *previous* comparison, not a finding this run's
    policy classified, so re-running that machinery over it would either be
    wrong (classifying it against the wrong pair of snapshots) or misleading
    (implying this run itself scored it). Just enough to identify which
    finding went away and where.
    """

    finding_id: str
    kind: str
    symbol: str
    description: str
    old_value: str | None
    new_value: str | None
    source_location: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "finding_id": self.finding_id,
            "kind": self.kind,
            "symbol": self.symbol,
            "description": self.description,
            "old_value": self.old_value,
            "new_value": self.new_value,
            "source_location": self.source_location,
        }


@dataclass(frozen=True)
class FindingEvolutionSummary:
    """Frozen, plain-value summary -- the one struct every renderer formats."""

    #: ``(evolution value, count)`` pairs for every ``current.changes``
    #: entry, in :class:`~abicheck.checker_policy.FindingEvolution`
    #: declaration order (matching ``DispositionAudit.counts``'s own
    #: convention) so two reports of the same run read identically. Never
    #: omits a state with a zero count -- see the module docstring.
    counts: tuple[tuple[str, int], ...]
    #: Findings from an earlier comparison in the chain that no longer
    #: appear in this one (``DiffResult.resolved_findings``).
    resolved: tuple[ResolvedFindingEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "counts": dict(self.counts),
            "resolved": [r.to_dict() for r in self.resolved],
        }

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> FindingEvolutionSummary:
        """Rebuild a summary from :meth:`to_dict`'s own output -- one wire
        shape, round-tripped, same rationale as ``DispositionAudit.from_dict``."""
        counts = d.get("counts") or {}
        return cls(
            counts=tuple((str(k), int(v)) for k, v in counts.items()),
            resolved=tuple(
                ResolvedFindingEntry(
                    finding_id=str(row["finding_id"]),
                    kind=str(row["kind"]),
                    symbol=str(row.get("symbol", "")),
                    description=str(row.get("description", "")),
                    old_value=row.get("old_value"),
                    new_value=row.get("new_value"),
                    source_location=row.get("source_location"),
                )
                for row in d.get("resolved") or ()
            ),
        )


def _resolved_entry(change: Change, finding_id: str) -> ResolvedFindingEntry:
    kind = getattr(change, "kind", None)
    return ResolvedFindingEntry(
        finding_id=finding_id,
        kind=kind.value if kind is not None else "",
        symbol=getattr(change, "symbol", ""),
        description=getattr(change, "description", ""),
        old_value=getattr(change, "old_value", None),
        new_value=getattr(change, "new_value", None),
        source_location=getattr(change, "source_location", None),
    )


def compute_finding_evolution_summary(result: DiffResult) -> FindingEvolutionSummary:
    """Resolve *result*'s already-recorded evolution facts. Decides nothing;
    only reads ``Change.evolution``/``DiffResult.resolved_findings``.

    ``getattr`` throughout, for the same reason
    ``disposition_audit.compute_disposition_audit`` reads its inputs that
    way: a report path may hand this a duck-typed stand-in rather than a
    real ``DiffResult``/``Change``.
    """
    from ..finding_identity import report_finding_id

    counts: dict[str, int] = {e.value: 0 for e in FindingEvolution}
    for change in getattr(result, "changes", None) or ():
        evolution = getattr(change, "evolution", FindingEvolution.NOT_EVALUATED)
        value = (
            evolution.value
            if isinstance(evolution, FindingEvolution)
            else str(evolution)
        )
        counts[value] = counts.get(value, 0) + 1

    resolved = tuple(
        _resolved_entry(change, report_finding_id(change))
        for change in getattr(result, "resolved_findings", None) or ()
    )
    # `RESOLVED` never appears on a `changes` entry (there is no current-side
    # `Change` for it, per `FindingEvolution.RESOLVED`'s own docstring), so
    # the loop above can never populate it -- the count instead comes from
    # `resolved` directly, keeping `counts` a true state distribution rather
    # than one state permanently pinned at zero regardless of reality.
    counts[FindingEvolution.RESOLVED.value] = len(resolved)

    return FindingEvolutionSummary(
        counts=tuple((e.value, counts[e.value]) for e in FindingEvolution),
        resolved=resolved,
    )


def add_finding_evolution(d: dict[str, object], result: DiffResult) -> None:
    """Attach the ``finding_evolution`` block to a JSON report mapping *d*.

    Unconditional, like ``add_disposition_audit`` -- a plain single
    comparison still owes every consumer this block, stating
    ``not_evaluated`` explicitly rather than omitting the field (ADR-067
    D3's convention, reused rather than reinvented here -- see the module
    docstring).
    """
    d["finding_evolution"] = compute_finding_evolution_summary(result).to_dict()
