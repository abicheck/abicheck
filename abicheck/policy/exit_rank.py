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

"""The one rank table both ``compare`` exit ladders delegate to (ADR-064).

A ``compare`` run's exit is the ordinary contribution fold
(:func:`~.exit_decision.resolve_exit_decision`, a tie-inclusive ``max``),
unless one of the *dominant* axes below fires. Dominant axes do not fold:
the highest-ranked active one decides ``code`` and is the only reason named.
:data:`COMPARE_EXIT_RANK` is that order, and :func:`resolve_ranked` is the
only place it is applied:

- the scalar pair ladder
  (``exit_decision_precedence.resolve_compare_exit_decision_with_abort_axes``,
  and the comparability refusal through
  ``resolve_compare_not_comparable_exit_decision``);
- the directory/package release ladder
  (``exit_decision_precedence.resolve_release_exit_decision``).

The ladders differ only in which axes they can switch on. The release
ladder's mode-dependent removed-library rule (always active under the
severity scheme; under legacy only once the verdict/operational fold is
``0``) decides *whether* that axis is active, never *where* it ranks.

Known gap ``scalar-and-release-compare-still-fold-the-exit-code-in-two``:
``budget_overflow`` has no release input, so a one-member release cannot
express the scalar's exit ``5``. That is the one cell the two ladders
disagree on at N=1; it awaits a maintainer ruling under ADR-064 and this
table does not decide it.

This module is a leaf: it imports only :mod:`.exit_decision`, so both
ladder modules can import it without a cycle.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import NamedTuple

from .exit_decision import ExitDecision, ExitReason

#: Which of :class:`ExitDecision`'s four ADR-064 fields corresponds to each
#: dominant :class:`ExitReason`. `_dominant_decision` uses this so exactly
#: one of the four is ever set (to `code`), keeping `code == max()` over
#: every field literally true rather than merely true "by convention."
_DOMINANT_FIELD = {
    ExitReason.EVIDENCE_CONTRACT_ERROR: "evidence_contract_error_contribution",
    ExitReason.BUDGET_OVERFLOW: "budget_overflow_contribution",
    ExitReason.NOT_COMPARABLE: "not_comparable_contribution",
    ExitReason.REMOVED_REQUIRED_LIBRARY: "removed_required_library_contribution",
}


def _dominant_decision(
    code: int,
    reason: ExitReason,
    *,
    prior: ExitDecision | None = None,
    compatibility_contribution: int = 0,
    contract_coverage_contribution: int = 0,
    analysis_assurance_contribution: int = 0,
    evidence_contract_error_contribution: int = 0,
    operational_error_contribution: int = 0,
    incomplete_scope_contribution: int = 0,
    no_comparison_completed_contribution: int = 0,
    additions_review_contribution: int = 0,
    unused_rules_contribution: int = 0,
) -> ExitDecision:
    """One of ADR-064's four axes overrides whatever the ordinary
    gate/coverage/assurance fold would otherwise have decided.

    Sets *reason*'s own dedicated field (`_DOMINANT_FIELD`) to *code* --
    the only one of the four ADR-064 fields this decision ever sets to
    nonzero -- so `ExitDecision`'s own `code == max()` invariant holds
    literally, not merely "by convention," even though this decision was
    reached by an early-return override rather than a flat fold.

    *prior* is the ordinary :class:`ExitDecision` already computed for this
    run before the dominant axis fired, when the caller has one available
    (e.g. `scan`'s budget check runs *after* a comparable baseline compare
    already built a full decision) -- its four PR-G1 contributions are
    carried through so a report reader can see what the gate/coverage/
    assurance/crosscheck axes actually were, even though they did not
    decide `code`. The three `*_contribution` keywords are a narrower
    escape hatch for a caller that has one or more already-computed
    *values* available but no full prior `ExitDecision` object to pass
    (the release fan-out's own removed-required-library and
    not-comparable branches, which receive the aggregated verdict/severity
    code and coverage floor as bare ints). Leave everything at its `0`
    default -- genuinely "not asked," not "evaluated and came out clean"
    -- only when nothing was computed at all (no `DiffResult` exists yet:
    `scan`'s evidence-contract error).

    Every dominant `code` this module produces (`1`/`5`/`6`/`8`/`16`) is
    chosen so it always exceeds any contribution the ordinary fold or a
    release's own severity/coverage axes can produce (`0`-`4`) -- so
    carrying a prior/raw value through here can never make `code` stop
    being the maximum contribution -- **enforced**, not merely assumed:
    every caller's *_code default is safe today, but
    :func:`resolve_scan_exit_decision` also accepts a caller-supplied custom
    code (for a future command with the same axes but different numbering,
    per ADR-064's "numbers are not unified across commands" rule), and a
    custom code that does not exceed a preserved prior/raw contribution
    would silently produce an object violating `ExitDecision`'s own
    `code == max(contributions)` invariant and drop a genuinely tied axis
    from `reasons` (Codex review, fresh evidence, with the exact
    counter-example: a custom code of `1` alongside a preserved
    compatibility contribution of `4`). Fail loudly instead.
    """
    dominant_field = _DOMINANT_FIELD[reason]
    # One mapping feeds both the guard's `preserved` tuple and the built
    # `ExitDecision`, so the two cannot disagree about which axes it carries
    # (two earlier revisions listed them separately and dropped a field).
    raw: dict[str, int] = {
        "compatibility_contribution": compatibility_contribution,
        "contract_coverage_contribution": contract_coverage_contribution,
        "analysis_assurance_contribution": analysis_assurance_contribution,
        "evidence_contract_error_contribution": evidence_contract_error_contribution,
        "operational_error_contribution": operational_error_contribution,
        "incomplete_scope_contribution": incomplete_scope_contribution,
        "no_comparison_completed_contribution": no_comparison_completed_contribution,
        "additions_review_contribution": additions_review_contribution,
        "unused_rules_contribution": unused_rules_contribution,
    }
    # A field that is *also* the dominant axis is not a preserved value: it
    # is set to `code` below. Generic, where this was a hand-written special
    # case for the one field that then applied --
    # `evidence_contract_error_contribution` is both preservable and
    # dominant-capable.
    if dominant_field in raw:
        raw[dominant_field] = 0
    preserved: tuple[int, ...]
    if prior is not None:
        # Every contribution *prior* carries except the dominant one, read
        # off the dataclass: a hand-written list here dropped each axis added
        # after it was written (the additions-review axis first).
        preserved = tuple(
            getattr(prior, f.name)
            for f in dataclasses.fields(prior)
            if f.name not in ("code", "reasons") and f.name != dominant_field
        )
    else:
        preserved = tuple(raw.values())
    if code <= max(preserved, default=0):
        raise ValueError(
            f"{reason.value}'s code ({code}) must strictly exceed every "
            f"preserved contribution {preserved} -- a custom code that "
            "only ties or falls below one would either violate "
            "ExitDecision's own code == max(contributions) invariant "
            "(if lower) or silently drop a genuinely tied axis from "
            "`reasons` (if equal)"
        )
    if prior is not None:
        return dataclasses.replace(
            prior, code=code, reasons=(reason,), **{dominant_field: code}
        )
    contributions = dict(raw)
    contributions[dominant_field] = code
    return ExitDecision(code=code, reasons=(reason,), **contributions)


class RankedAxis(NamedTuple):
    """One dominant axis in :data:`COMPARE_EXIT_RANK`."""

    reason: ExitReason
    #: The axis's canonical ``compare`` exit code (ADR-064). A ladder passes
    #: the code it actually resolved to :func:`resolve_ranked`; this is the
    #: number every current producer passes.
    code: int
    #: Whether the axis's own contribution is still recorded when a
    #: higher-ranked axis decides the code. Recorded today: the evidence
    #: contract under ``not_comparable``/removed library (the release ladder
    #: reported it, Codex review). Not recorded: the removed library under
    #: ``not_comparable``, and the budget overflow under the evidence contract
    #: (the scalar ladder dropped it). Kept as found, not ruled on.
    recorded_when_outranked: bool


#: Dominant axes, highest rank first. Codes: ``docs/reference/exit-codes.md``.
COMPARE_EXIT_RANK: tuple[RankedAxis, ...] = (
    RankedAxis(ExitReason.NOT_COMPARABLE, 16, recorded_when_outranked=False),
    RankedAxis(ExitReason.REMOVED_REQUIRED_LIBRARY, 8, recorded_when_outranked=False),
    RankedAxis(ExitReason.EVIDENCE_CONTRACT_ERROR, 7, recorded_when_outranked=True),
    RankedAxis(ExitReason.BUDGET_OVERFLOW, 5, recorded_when_outranked=False),
)


def resolve_ranked(
    active: Mapping[ExitReason, int], *, ordinary: ExitDecision
) -> ExitDecision:
    """Apply :data:`COMPARE_EXIT_RANK` to *active* over *ordinary*.

    *active* maps each dominant axis that fired to the exit code the caller
    resolved for it; a missing key or a ``0`` value means the axis did not
    fire. *ordinary* is the run's contribution fold. With no active axis,
    *ordinary* is the decision. Otherwise the highest-ranked active axis
    decides ``code`` and is the only reason; *ordinary*'s contributions, and
    each outranked active axis marked ``recorded_when_outranked``, are kept
    for explainability. A key that is not a ranked axis raises ``ValueError``.
    """
    ranked = {axis.reason for axis in COMPARE_EXIT_RANK}
    unknown = [reason for reason in active if reason not in ranked]
    if unknown:
        raise ValueError(f"not a ranked dominant axis: {unknown}")
    fired = [axis for axis in COMPARE_EXIT_RANK if active.get(axis.reason, 0)]
    if not fired:
        return ordinary
    winner, *outranked = fired
    recorded = {
        _DOMINANT_FIELD[axis.reason]: active[axis.reason]
        for axis in outranked
        if axis.recorded_when_outranked
    }
    prior = dataclasses.replace(
        ordinary, code=ordinary.code, reasons=ordinary.reasons, **recorded
    )
    return _dominant_decision(active[winner.reason], winner.reason, prior=prior)
