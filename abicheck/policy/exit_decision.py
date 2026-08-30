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

"""The canonical ``ExitDecision`` — CLI cleanup phase two, PR G1.

``docs/contribute/plans/cli-cleanup-phase-two.md``'s PR 4 records that a
single-pair `compare` invocation's exit code is folded from
several independently-computed, orthogonal contributions today
(`severity.compute_exit_code`/`severity.legacy_exit_code`,
`contract_coverage_exit.fold_coverage_exit`,
`analysis_assurance.fold_analysis_assurance_exit`), each folded in with
`max()` at the call site (`cli._exit_with_severity_or_verdict`) rather than
through one shared, explainable object. That is fine for computing *a*
number, but leaves no answer to "why is this exit 1" when more than one axis
independently contributes -- a caller has to re-derive the answer from
several separately-read report fields.

``ExitDecision``/:func:`resolve_exit_decision` is that explainable object,
built additively (PR G1: no CLI behaviour change, no flag removed) as the
first step toward PR 4/G2's actual algorithm-selector removal. It wraps the
*existing* fold -- `max()` over the axes below -- rather than replacing it,
so every call site that adopts it keeps today's exit code bit-for-bit; what
it adds is `reasons`, the set of axes whose own contribution equals the
final code (and therefore genuinely explains it, as opposed to a lower
contribution that never determined the result).

**Deliberately scoped to the axes that already coexist on one
`DiffResult`-backed report today** -- the compatibility gate (legacy verdict
or severity-aware), contract coverage, and analysis assurance. Three more
axes the reviewed plan also names -- `not_comparable`, a release's
removed-required-library policy, and `scan`'s budget-overflow/evidence-
contract-error floors -- are raised through different code paths today
(`sys.exit` before a coherent `DiffResult` exists, or a release/scan-specific
fold with its own, mode-dependent precedence against the compatibility
gate).

``docs/contribute/adr/064-canonical-gate-algorithm-and-exit-decision.md``
("ADR-064") is the settled design for those three axes and for PR 4/G2's
actual `--exit-code-scheme` removal. This module implements ADR-064's
*additive* first stage only: :func:`resolve_scan_exit_decision` and
:func:`resolve_release_exit_decision` below are pure functions reproducing
today's exact precedence for `scan` and the directory/package release
fan-out respectively (verified against `scan_engine.py`/
`cli_compare_release_helpers.py` at the commit that added them) -- neither
is wired into any call site's *actually returned* exit code yet, exactly
the same "wraps the existing behaviour, doesn't call it yet" scoping PR G1
itself used for the three axes above. Wiring these into the report's `exit`
block (`scan --against`'s nested `diff.exit`, the release fan-out's own
summary), and the atomic `--exit-code-scheme` removal itself, are ADR-064's
second stage and remain open.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..checker_types import DiffResult
    from .severity import SeverityConfig


class ExitReason(str, Enum):
    """Which orthogonal axis a resolved :class:`ExitDecision` traces to.

    A member appears in :attr:`ExitDecision.reasons` only when its own
    contribution equals the decision's final :attr:`ExitDecision.code` --
    i.e. it is one of the axes that actually determined the result, not
    merely one that happened to be non-zero. A coverage floor of ``1``
    sitting underneath a compatibility gate's ``4`` explains nothing about
    why the exit is ``4``; it stays out of ``reasons`` for that decision
    (its own finding/failure still appears elsewhere in the report --
    ``reasons`` is about the exit code specifically, not about hiding the
    axis).
    """

    COMPATIBILITY_GATE = "compatibility_gate"
    SCOPED_GATE = "scoped_gate"
    CONTRACT_COVERAGE = "contract_coverage"
    ANALYSIS_ASSURANCE = "analysis_assurance"
    CLEAN = "clean"
    #: `scan --against` only. A maintainer-promoted `--crosscheck KEY=error`
    #: finding (`scan_engine._crosscheck_severity_exit`) raised the exit code
    #: past what the three compatibility/coverage/assurance axes would have
    #: produced on their own. :func:`resolve_exit_decision` *does* model
    #: this as a real fourth contribution
    #: (`ExitDecision.crosscheck_promotion_contribution`) when a caller
    #: passes one in -- `resolve_compare_exit_decision` (native `compare`)
    #: never does, since crosscheck promotion has no meaning outside
    #: `scan --against`, so it is always `0`/absent there. The scan-only
    #: half that stays true is *when* the contribution is known:
    #: `scan_engine._promote_published_gate` re-resolves the whole decision
    #: through `resolve_exit_decision` (with the crosscheck contribution
    #: filled in) only *after* the fact, once a promotion actually fires --
    #: mirroring how that same function already patches the persisted
    #: `severity` block for the identical reason -- a published `exit`
    #: block that still named `compatibility_gate` for a code the
    #: crosscheck promotion actually produced would be exactly the kind of
    #: "explains nothing about why the exit is N" trap this enum exists to
    #: avoid.
    PROMOTED_CROSSCHECK = "promoted_crosscheck"

    #: `scan` only (ADR-037 D5). A pinned, non-`auto` `--depth`/
    #: `--source-method` had no source evidence to satisfy it
    #: (`scan_engine._EvidenceContractError`) -- raised during evidence
    #: collection, before a candidate/baseline comparison is even attempted.
    #: Dominates every other axis below it in ADR-064's precedence order,
    #: since none of them were ever computed for this run.
    EVIDENCE_CONTRACT_ERROR = "evidence_contract_error"
    #: `scan` only. `--budget` overflowed (`scan_engine._BudgetOverflow`).
    #: Checked *after* a `not_comparable` result may already have been
    #: decided for the same run, and -- per ADR-064's "budget dominates
    #: not-comparable" rule, reproducing `scan_engine.run_scan_core`'s own
    #: unconditional post-comparison budget check -- discards that result
    #: rather than losing to it.
    BUDGET_OVERFLOW = "budget_overflow"
    #: OLD and NEW (or, for a release, at least one library pair) were not
    #: extracted under a comparable profile/scope contract (ADR-050 D2), so
    #: no verdict was ever produced. Dominates the compatibility gate and
    #: removed-required-library axes below it, but never `scan`'s own
    #: `BUDGET_OVERFLOW` (see that reason's own docstring). No `DiffResult`
    #: exists for this outcome, so contract-coverage/analysis-assurance are
    #: never computed either -- every other contribution is `0` when this
    #: reason applies.
    NOT_COMPARABLE = "not_comparable"
    #: A directory/package release comparison only. `--fail-on-removed-
    #: library` is set and at least one library was removed between
    #: releases. Its rank relative to the compatibility gate is
    #: **mode-dependent, not fixed** -- see
    #: :func:`resolve_release_exit_decision`'s own docstring and ADR-064's
    #: "Removed-required-library is mode-dependent" section for the exact
    #: switch this reason's precedence reproduces.
    REMOVED_REQUIRED_LIBRARY = "removed_required_library"


@dataclass(frozen=True)
class ExitDecision:
    """One comparison's fully explainable exit code.

    ``code`` is exactly ``max(compatibility_contribution,
    contract_coverage_contribution, analysis_assurance_contribution,
    crosscheck_promotion_contribution)`` -- the first three are the identical
    value today's ad hoc fold chain in ``cli._exit_with_severity_or_verdict``
    already produces, computed once here instead of via three separately-
    called functions; the fourth exists only for `scan --against`'s own
    maintainer-promoted `--crosscheck KEY=error` finding and is always `0`
    for a native `compare` report (see :class:`ExitReason.PROMOTED_
    CROSSCHECK`). ``reasons`` names every axis tied for that maximum; see
    :class:`ExitReason` for why a lower, non-winning contribution is
    excluded.
    """

    code: int
    reasons: tuple[ExitReason, ...]
    compatibility_contribution: int
    contract_coverage_contribution: int
    analysis_assurance_contribution: int
    #: `scan --against` only -- what a maintainer-promoted `--crosscheck
    #: KEY=error` finding contributes (`0` for every other caller, and for
    #: a scan run where no promotion fired). A *fourth* axis, not a
    #: bolt-on mutation of `code`/`reasons` after the fact (Codex review,
    #: fresh evidence): `scan_engine._promote_published_gate` used to patch
    #: only those two fields, leaving the three contributions above
    #: summing to less than the new `code` -- silently breaking this
    #: class's own documented invariant that `code == max(the
    #: contributions)`, and also never adding `PROMOTED_CROSSCHECK` to
    #: `reasons` on an exact tie (a hand-rolled strict `>` check, unlike
    #: this function's own tie-inclusive fold). Modeling it as a real
    #: contribution lets `_promote_published_gate` reconstruct the whole
    #: decision through :func:`resolve_exit_decision` instead of hand-
    #: patching two of its five fields.
    crosscheck_promotion_contribution: int = 0

    def to_dict(self) -> dict[str, object]:
        """JSON-serializable form, for the report's ``exit`` block."""
        return {
            "code": self.code,
            "reasons": [r.value for r in self.reasons],
            "compatibility_contribution": self.compatibility_contribution,
            "contract_coverage_contribution": self.contract_coverage_contribution,
            "analysis_assurance_contribution": self.analysis_assurance_contribution,
            "crosscheck_promotion_contribution": self.crosscheck_promotion_contribution,
        }


def resolve_exit_decision(
    *,
    compatibility_contribution: int,
    contract_coverage_contribution: int = 0,
    analysis_assurance_contribution: int = 0,
    crosscheck_promotion_contribution: int = 0,
    compatibility_reason: ExitReason = ExitReason.COMPATIBILITY_GATE,
) -> ExitDecision:
    """Fold the axis contributions below into one explainable decision.

    *compatibility_contribution* is the caller's own pre-computed
    compatibility-gate exit code -- either `severity.legacy_exit_code`
    (verdict-based, no severity config in effect) or
    `severity.compute_exit_code` (a severity map is in effect), or (Codex
    review) the *pre-fold* `--used-by`/`--required-symbol(s)` scoped gate
    contribution -- never an already-folded value, or a tie with coverage/
    assurance would be silently hidden behind whichever reason this slot is
    labelled. This function does not choose between the unscoped
    algorithms; that selection is exactly what PR 4/G2 still has to
    consolidate into one automatic algorithm.
    *contract_coverage_contribution*/*analysis_assurance_contribution*
    default to ``0`` (their "never asked the question" value, matching
    `contract_coverage_exit.coverage_exit_floor`/`analysis_assurance.
    analysis_assurance_exit_contribution`'s own fail-open defaults) so a
    caller with neither `--contract` nor `--require-complete-analysis` in
    effect can omit both. *compatibility_reason* names which
    :class:`ExitReason` this slot represents -- `COMPATIBILITY_GATE` by
    default, or `SCOPED_GATE` when the caller's compatibility contribution
    is the scoped application/plugin-host gate rather than the full-library
    one; every other axis's reason is unaffected either way.
    *crosscheck_promotion_contribution* defaults to ``0`` (every caller but
    `scan_engine._promote_published_gate`, which is the only place a
    maintainer-promoted `--crosscheck KEY=error` finding's own exit
    contribution is known) -- see :class:`ExitDecision`'s own field
    docstring for why this has to be a real axis rather than a post-hoc
    patch to `code`/`reasons`.
    """
    contributions = {
        compatibility_reason: compatibility_contribution,
        ExitReason.CONTRACT_COVERAGE: contract_coverage_contribution,
        ExitReason.ANALYSIS_ASSURANCE: analysis_assurance_contribution,
        ExitReason.PROMOTED_CROSSCHECK: crosscheck_promotion_contribution,
    }
    code = max(contributions.values())
    if code == 0:
        reasons: tuple[ExitReason, ...] = (ExitReason.CLEAN,)
    else:
        reasons = tuple(
            reason
            for reason, contribution in contributions.items()
            if contribution == code
        )
    return ExitDecision(
        code=code,
        reasons=reasons,
        compatibility_contribution=compatibility_contribution,
        contract_coverage_contribution=contract_coverage_contribution,
        analysis_assurance_contribution=analysis_assurance_contribution,
        crosscheck_promotion_contribution=crosscheck_promotion_contribution,
    )


def resolve_compare_exit_decision(
    result: DiffResult,
    sev_config: SeverityConfig | None,
    scheme: str,
    *,
    require_complete_analysis: bool = False,
) -> ExitDecision:
    """:func:`resolve_exit_decision`, deriving every contribution from
    *result* the same way `cli._exit_with_severity_or_verdict` does today.

    The call site a native `compare` invocation needs: it reproduces
    that function's exact fold order (compatibility → coverage floor →
    assurance floor, each `max`-based) as one canonical resolution, so a
    caller building the report's ``exit`` block and a caller computing the
    real process exit code cannot read two different numbers for the same
    comparison.

    **`scan --against` also calls this function (CLI cleanup phase two, PR
    E), from `cli_scan_baseline._run_baseline_compare`, which nests the
    result at ``diff.exit`` rather than the report's top level -- matching
    where its own constituent `analysis_assurance_exit_contribution`/
    `contract_coverage_exit_contribution` fields already live, not
    `ScanOutcome`'s own top-level ``verdict``/``exit_code``.** That
    top-level pair folds strictly more than this function ever will for a
    scan: budget overflow, `NOT_COMPARABLE`, and a maintainer-promoted
    `--crosscheck KEY=error` finding (`scan_engine._crosscheck_severity_
    exit`) are scan-only axes raised through their own code paths, not
    modeled by this resolver (see this module's own docstring for why).
    `scan_engine._promote_published_gate` keeps the persisted ``diff.exit``
    block honest for the one of those three that can happen *after* this
    function already ran -- crosscheck promotion -- by raising its ``code``
    and re-stamping ``reasons`` to ``PROMOTED_CROSSCHECK``, the same way it
    already patches the persisted ``severity`` block. Budget overflow
    aborts before a report is built at all; `NOT_COMPARABLE` has no
    `DiffResult` for this resolver to read from, so no ``exit`` block is
    emitted for that case either.

    **`--used-by`/`--required-symbol(s)` scoped gating overrides the
    compatibility axis entirely (Codex review, fresh evidence).**
    `cli_compare_helpers._apply_scoped_gating` floors the *full-library*
    verdict/severity gate this function would otherwise compute to the
    scoped application/plugin-host contract's own result -- `cli.py` itself
    exits on the (separately, already-folded) `result.scoped_exit_code`
    directly (`sys.exit(scoped_exit_code)`), never reaching
    `_exit_with_severity_or_verdict`/this function's own compatibility-axis
    computation. An earlier revision of this function ignored that and
    derived `compatibility_contribution` from `result.verdict`/severity
    regardless -- reporting the full-library gate's code (informational-only
    under scoping) while the real process exited on the scoped one.

    **A later revision fixed that but introduced a second bug (Codex
    review): it read the already-folded `result.scoped_exit_code` as the
    compatibility contribution, so a tie with coverage/assurance was hidden
    behind a `SCOPED_GATE` reason that may not have contributed at all.**
    `result.scoped_compatibility_contribution` (`cli_compare_helpers`,
    persisted immediately before that fold runs) is the *pre-fold* scoped
    value -- passed through the same `resolve_exit_decision` every other
    caller uses, with `compatibility_reason=SCOPED_GATE`, so a genuine tie
    between the scoped gate and coverage/assurance is named correctly
    instead of always attributed to scoping.
    """
    from ..analysis_assurance import analysis_assurance_exit_contribution
    from .contract_coverage_exit import coverage_exit_floor
    from .severity import compute_exit_code, legacy_exit_code

    coverage_contribution = coverage_exit_floor(result)
    assurance_contribution = analysis_assurance_exit_contribution(
        result, require_complete=require_complete_analysis
    )

    scoped_exit_code = getattr(result, "scoped_exit_code", None)
    if scoped_exit_code is not None:
        scoped_compatibility_contribution = getattr(
            result, "scoped_compatibility_contribution", scoped_exit_code,
        )
        return resolve_exit_decision(
            compatibility_contribution=scoped_compatibility_contribution,
            contract_coverage_contribution=coverage_contribution,
            analysis_assurance_contribution=assurance_contribution,
            compatibility_reason=ExitReason.SCOPED_GATE,
        )

    if scheme == "severity":
        assert sev_config is not None
        compatibility_contribution = compute_exit_code(
            result.changes,
            sev_config,
            policy=result.policy,
            kind_sets=result._effective_kind_sets(),
            policy_file=result.policy_file,
        )
    else:
        compatibility_contribution = legacy_exit_code(result.verdict)
    return resolve_exit_decision(
        compatibility_contribution=compatibility_contribution,
        contract_coverage_contribution=coverage_contribution,
        analysis_assurance_contribution=assurance_contribution,
    )


def _dominant_decision(code: int, reason: ExitReason) -> ExitDecision:
    """One axis fully determines the outcome; the others were never
    computed for this run (there is no `DiffResult` to compute them from),
    so every other contribution is `0` -- not "evaluated and came out
    clean," genuinely "not asked."
    """
    return ExitDecision(
        code=code,
        reasons=(reason,),
        compatibility_contribution=0,
        contract_coverage_contribution=0,
        analysis_assurance_contribution=0,
    )


def resolve_scan_exit_decision(
    *,
    evidence_contract_error: bool = False,
    budget_overflow: bool = False,
    not_comparable: bool = False,
    evidence_contract_error_code: int = 1,
    budget_overflow_code: int = 5,
    not_comparable_code: int = 6,
) -> ExitDecision | None:
    """ADR-064's outer precedence layer for `scan`, ahead of
    :func:`resolve_compare_exit_decision`'s gate/coverage/assurance fold.

    Reproduces `scan_engine.run_scan_core`'s exact raise/check order:
    `_EvidenceContractError` aborts during evidence collection, before a
    candidate/baseline comparison is even attempted, so it dominates
    everything (`evidence_contract_error` wins even if the caller also
    passes `budget_overflow=True`/`not_comparable=True` -- those axes were
    never reached). `_BudgetOverflow` is raised by `_check_scan_budget`,
    called *unconditionally* after a `not_comparable` result may already
    have been decided by the same run's baseline compare -- and that call
    discards the already-decided result rather than losing to it (the
    `ScanOutcome`/report is never constructed once `_BudgetOverflow`
    propagates), so `budget_overflow` wins over `not_comparable` here too,
    not the other way around. Returns `None` when none of the three axes
    apply, meaning the comparison actually ran and the caller should
    resolve an ordinary :class:`ExitDecision` for it instead (via
    :func:`resolve_compare_exit_decision`/:func:`resolve_exit_decision`).

    This is pure, additive logic (ADR-064's first stage) -- it is not yet
    called from `scan_engine.py`/`cli_scan_baseline.py`, so no existing
    call site's actually-returned exit code changes because this function
    exists. The `*_code` keyword arguments default to `scan`'s own real
    numbers (1/5/6) but are accepted explicitly rather than hard-coded, so
    a future caller for a different command with the same three axes (none
    is known today) is not forced to share `scan`'s numbering, per
    ADR-064's "numbers are not unified across commands" rule.
    """
    if evidence_contract_error:
        return _dominant_decision(
            evidence_contract_error_code, ExitReason.EVIDENCE_CONTRACT_ERROR
        )
    if budget_overflow:
        return _dominant_decision(budget_overflow_code, ExitReason.BUDGET_OVERFLOW)
    if not_comparable:
        return _dominant_decision(not_comparable_code, ExitReason.NOT_COMPARABLE)
    return None


def resolve_release_exit_decision(
    *,
    not_comparable: bool,
    severity_scheme_active: bool,
    verdict_or_severity_contribution: int,
    removed_required_library: bool = False,
    contract_coverage_contribution: int = 0,
    not_comparable_code: int = 16,
    removed_required_library_code: int = 8,
) -> ExitDecision:
    """ADR-064's precedence for a directory/package release comparison,
    reproducing `cli_compare_release_helpers._exit_compare_release` exactly
    -- including the one asymmetry that function's own two branches encode,
    which a flat `max()` over contributions cannot express (this is exactly
    why ADR-064 calls removed-required-library's rank "mode-dependent, not
    a fixed slot" rather than folding it into `resolve_exit_decision`):

    - `not_comparable` dominates everything, in both schemes (mirrors
      native `compare`'s own `16`, and release's `_exit_compare_release`
      checking `worst_verdict == "not_comparable"` first, unconditionally).
    - **Severity-aware scheme** (`severity_scheme_active=True`):
      `removed_required_library` wins *outright* over the aggregated
      verdict/severity code -- including over `contract_coverage_
      contribution`, which is never folded in when removed-library fires.
      This is a real, pre-existing asymmetry in today's code (the severity
      branch's `sys.exit(8)` for a removed library runs before the coverage
      floor is even read), not a simplification introduced here.
    - **Legacy scheme** (`severity_scheme_active=False`): the opposite
      priority -- a nonzero *verdict_or_severity_contribution* (an
      `API_BREAK`/`BREAKING` verdict, or the release's own operational
      `ERROR` sentinel floored to `4`) wins outright over
      removed-required-library, which is checked only once that
      contribution is `0`. `contract_coverage_contribution` folds in via
      `max()` alongside whichever of the two decided the code, exactly as
      `_exit_compare_release`'s own `max(code, contract_coverage_exit_
      contribution)` calls do.

    *verdict_or_severity_contribution* is the caller's own already-computed
    aggregated code for the active scheme -- for severity, `max(
    severity_exit_code, 4 if worst_verdict == "ERROR" else 0)`; for legacy,
    `4` if `worst_verdict == "ERROR"` else `legacy_exit_code(Verdict[
    worst_verdict])`. This function does not compute either fold itself,
    matching `resolve_compare_exit_decision`'s own convention of taking an
    already-resolved compatibility contribution rather than re-deriving one.

    Pure, additive logic (ADR-064's first stage): not yet called from
    `cli_compare_release_helpers.py`, so no existing release comparison's
    actually-returned exit code changes because this function exists.
    """
    if not_comparable:
        return _dominant_decision(not_comparable_code, ExitReason.NOT_COMPARABLE)

    if severity_scheme_active:
        if removed_required_library:
            return _dominant_decision(
                removed_required_library_code, ExitReason.REMOVED_REQUIRED_LIBRARY
            )
        return resolve_exit_decision(
            compatibility_contribution=verdict_or_severity_contribution,
            contract_coverage_contribution=contract_coverage_contribution,
        )

    # Legacy scheme: a nonzero verdict/ERROR contribution wins outright,
    # ahead of removed-required-library -- checked only once it is 0.
    if verdict_or_severity_contribution != 0:
        return resolve_exit_decision(
            compatibility_contribution=verdict_or_severity_contribution,
            contract_coverage_contribution=contract_coverage_contribution,
        )
    if removed_required_library:
        return _dominant_decision(
            removed_required_library_code, ExitReason.REMOVED_REQUIRED_LIBRARY
        )
    return resolve_exit_decision(
        compatibility_contribution=0,
        contract_coverage_contribution=contract_coverage_contribution,
    )
