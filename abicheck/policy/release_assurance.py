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

"""ADR-071: ``assurance.require_complete`` for a release fan-out.

A directory/package ``compare`` has one
:class:`~abicheck.analysis_assurance.AnalysisAssurance` per compared member,
never one for the run. This module answers the two questions a release has
about that set, and only those:

* **What is the release's assurance status?** The worst member status under
  :data:`ASSURANCE_STATUS_ORDER` -- ``complete`` iff *every* member's is.
* **Does it block?** :func:`release_assurance_exit_contribution` -- ``0``
  unless ``assurance.require_complete`` is on *and* some member fell short,
  then ``1``, folded with ``max`` by the release exit resolver exactly like
  ADR-049 Phase 7's coverage axis and ADR-065 D6's scope axis. One shape,
  not three.

Both come back together as one :class:`ReleaseAssuranceDecision`
(:func:`resolve_release_assurance_decision`) for the same reason ADR-065's
``resolve_scope_decision`` bundles its own: the status a reader sees and the
number that gated them must not be derived twice, or a report can contradict
its own exit code (ADR-071 D5).

The fold is ``max`` and only ever ``max`` (D2): a member's incomplete
analysis is not maskable by a complete sibling, and adding a member can
only raise the release's contribution. Over a single member it is the
identity, which is what makes a one-member package agree with the scalar
path (D1, ``AGENTS.md``'s "One model, any cardinality").

A leaf by construction: it takes the already-extracted ``(name, status,
notes)`` of each member rather than ``AnalysisAssurance`` objects.
``abicheck/analysis_assurance.py`` is a flat-root module that itself imports
from ``policy``, so a ``policy -> analysis_assurance`` import would be a real
cycle; taking plain values also matches how
``resolve_release_exit_decision`` already reads its per-member axes off plain
``library_results`` dicts.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ASSURANCE_STATUS_ORDER",
    "CLI_MITIGATION",
    "INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION",
    "MemberAssurance",
    "ReleaseAssuranceDecision",
    "release_assurance_diagnostic",
    "release_assurance_exit_contribution",
    "release_assurance_status",
    "resolve_release_assurance_decision",
]

#: The ``0``/``1`` floor an incomplete analysis imposes -- the same value
#: ``analysis_assurance.INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION`` uses for a
#: single pair, restated here rather than imported for the cycle reason in
#: this module's docstring. ``tests/test_release_assurance.py`` pins the two
#: to the same number so they cannot drift.
INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION = 1

#: ADR-071 D5's total order, best first. ``complete`` is strictly best and
#: every other status is a shortfall, because that is exactly what the exit
#: fold already treats them as (``analysis_assurance_exit_contribution``
#: floors on ``status != "complete"``) -- a release whose aggregate status
#: read ``complete`` while its contribution was ``1`` would be a report
#: contradicting its own exit code. ``not_requested`` therefore ranks below
#: ``complete`` despite not being a *failure*: no depth was requested, so
#: nothing vouches for the evidence either.
ASSURANCE_STATUS_ORDER: tuple[str, ...] = (
    "complete",
    "not_requested",
    "partial",
    "failed",
    "not_comparable",
)

#: Where an unrecognized status sorts: worse than every known one. A member
#: carrying a status this build does not know about is not evidence of a
#: complete analysis (fail closed), and ranking it last means a future
#: vocabulary addition degrades to "shortfall" rather than to "clean".
_UNKNOWN_RANK = len(ASSURANCE_STATUS_ORDER)


def _rank(status: str) -> int:
    """*status*'s position in :data:`ASSURANCE_STATUS_ORDER`, or
    :data:`_UNKNOWN_RANK` for a status this build does not know."""
    try:
        return ASSURANCE_STATUS_ORDER.index(status)
    except ValueError:
        return _UNKNOWN_RANK


@dataclass(frozen=True)
class MemberAssurance:
    """One compared member's assurance fact, as the fold needs it.

    *name* is the member's library key (what the report names, and what a
    user would pass to compare that library individually); *status* its own
    ``AnalysisAssurance.status``; *notes* that block's own notes, carried so
    the diagnostic can say *why* a member fell short rather than only that
    it did (ADR-071 D6).
    """

    name: str
    status: str
    notes: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """Whether this member's own analysis was complete."""
        return self.status == "complete"

    def to_dict(self) -> dict[str, object]:
        """The JSON row ``report.release_assurance`` projects."""
        return {
            "library": self.name,
            "status": self.status,
            "notes": list(self.notes),
        }


def release_assurance_status(members: tuple[MemberAssurance, ...]) -> str:
    """The release's aggregate status: the worst member's (ADR-071 D5).

    ``"not_requested"`` for an empty *members* -- a release that compared
    nothing has no evidence to vouch for, and it is deliberately not
    ``"complete"``: claiming a complete analysis over zero comparisons is
    the assurance counterpart of the clean pass ADR-065 D7 refuses for a
    zero-comparison scope. (Whether the run *blocks* on that is the scope
    axis's own question, not this one's.)
    """
    if not members:
        return "not_requested"
    return max((m.status for m in members), key=_rank)


def release_assurance_exit_contribution(
    members: tuple[MemberAssurance, ...], *, require_complete: bool
) -> int:
    """ADR-071 D1/D2/D7's ``0``/``1`` floor: ``max`` over the members'.

    ``0`` unconditionally when *require_complete* is False -- the same thing
    that keeps the scalar flag purely additive, and the reason every
    pre-existing release invocation's exit code is unchanged (D4). Otherwise
    ``1`` iff any member's status is not ``"complete"``.

    Over one member this is the identity, which is D1's whole point: a
    one-member package gates exactly as the scalar path does for that pair.
    Over zero members it is ``0``: there is no member whose analysis fell
    short, and a run that compared nothing is ADR-065 D7's axis, which
    floors it on its own grounds rather than borrowing this one's.
    """
    if not require_complete:
        return 0
    if all(m.is_complete for m in members):
        return 0
    return INCOMPLETE_ANALYSIS_EXIT_CONTRIBUTION


@dataclass(frozen=True)
class ReleaseAssuranceDecision:
    """The assurance axis for a release, *decided*: the members it folded,
    whether ``assurance.require_complete`` was in effect, the aggregate
    status, and the one ``0``/``1`` the exit fold reads.

    Resolved once per run and carried unchanged to the exit decision, the
    report projection, and the stderr notice, so no consumer re-derives one
    (``report/AGENTS.md``: a renderer decides nothing).
    """

    members: tuple[MemberAssurance, ...]
    require_complete: bool
    status: str
    exit_contribution: int

    @property
    def incomplete_members(self) -> tuple[MemberAssurance, ...]:
        """The members whose own analysis was not ``complete``.

        Real regardless of *require_complete*: the setting decides whether
        an incomplete analysis is *accepted*, never whether it happened
        (``AGENTS.md``'s "Record before disposing").
        """
        return tuple(m for m in self.members if not m.is_complete)

    @property
    def incomplete_member_count(self) -> int:
        """How many members fell short -- a plain count, not the ``0``/``1``
        floor above, so a reader sees the real extent even when the floor is
        ``0`` because the setting was off."""
        return len(self.incomplete_members)


def resolve_release_assurance_decision(
    members: tuple[MemberAssurance, ...], *, require_complete: bool
) -> ReleaseAssuranceDecision:
    """Decide the assurance axis for *members* -- the one place the status
    and the contribution are computed together (ADR-071 D5)."""
    return ReleaseAssuranceDecision(
        members=tuple(members),
        require_complete=require_complete,
        status=release_assurance_status(members),
        exit_contribution=release_assurance_exit_contribution(
            members, require_complete=require_complete
        ),
    )


#: How many member names one diagnostic group spells out before "+N more" --
#: same cap ``scope_completeness._grouped_members`` applies, so a
#: twelve-member matrix does not print twelve sentences.
_DIAGNOSTIC_NAMES_PER_STATUS = 6

#: How a user closes the gap, or accepts it.
CLI_MITIGATION = (
    "Supply the missing evidence for the named members (headers, debug info, "
    "or build data), compare one of them individually to see its full "
    "analysis_assurance block, or drop assurance.require_complete from "
    ".abicheck.yml to accept a partial analysis. -o json=... carries the "
    "release's own analysis_assurance block."
)


def release_assurance_diagnostic(
    decision: ReleaseAssuranceDecision,
    *,
    base_exit: int = 0,
    mitigation: str = CLI_MITIGATION,
) -> str | None:
    """The one stderr wording for a release's incomplete assurance, or
    ``None`` when nothing fell short or the setting was off.

    Mirrors ``scope_completeness.incomplete_scope_diagnostic`` and
    ``analysis_assurance.assurance_floor_diagnostic`` so the orthogonal-axis
    notices read as one family, and names the members and their reasons
    rather than a bare count (ADR-071 D6). *base_exit* is the compatibility
    axis's own code and decides the wording, never merely whether to speak:
    beside a real break, claiming the exit "was floored to 1" would be false.
    """
    if not decision.require_complete:
        return None
    short = decision.incomplete_members
    if not short:
        return None
    floor = decision.exit_contribution
    what = (
        f"Analysis assurance incomplete for {len(short)} of "
        f"{len(decision.members)} compared member(s) "
        f"(release status={decision.status!r}) under "
        "assurance.require_complete -- " + _grouped_members(short) + "."
    )
    if floor == 0:
        effect = "The assurance axis contributes 0 to the exit code"
    elif base_exit < floor:
        effect = f"Exit code floored to {floor}"
    elif base_exit == floor:
        effect = f"Contributes {floor} to an exit that was already {base_exit}"
    else:
        effect = (
            f"Contributes {floor}, below the compatibility axis's own exit "
            f"{base_exit}, which stands"
        )
    return f"{what} {effect} (ADR-071 release analysis-assurance axis). {mitigation}"


def _grouped_members(members: tuple[MemberAssurance, ...]) -> str:
    """``status [one shared reason]: a, b, c (+N more); status2 ...`` -- one
    group per status, names capped, the reason stated once per group when
    every member in it gives the same one."""
    groups: dict[str, list[MemberAssurance]] = {}
    for m in members:
        groups.setdefault(m.status, []).append(m)
    parts: list[str] = []
    for status, group in groups.items():
        names = [m.name for m in group[:_DIAGNOSTIC_NAMES_PER_STATUS]]
        more = len(group) - len(names)
        listed = ", ".join(names) + (f" (+{more} more)" if more > 0 else "")
        reasons = {"; ".join(m.notes) for m in group if m.notes}
        reason = next(iter(reasons)) if len(reasons) == 1 else ""
        parts.append(f"{status}{' [' + reason + ']' if reason else ''}: {listed}")
    return "; ".join(parts)
