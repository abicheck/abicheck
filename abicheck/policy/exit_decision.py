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
or severity-aware), contract coverage, and analysis assurance. Three axes
the reviewed plan also names -- `not_comparable`, a release's
removed-required-library policy, and `scan`'s budget-overflow floor -- are
raised through different code paths today (`sys.exit` before a coherent
`DiffResult` exists, or a release/scan-specific fold with its own,
mode-dependent precedence against the compatibility gate; see the plan's
"canonical `ExitDecision`" section for why removed-library's rank cannot be
a fixed slot). Folding those in is real, further work for PR G2, not
attempted here -- extending this module before that design is settled would
risk exactly the kind of partially-verified, cross-cutting change this
codebase's own conventions warn against.
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
