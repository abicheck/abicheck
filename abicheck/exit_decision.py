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
single-pair `compare`/`scan --against` invocation's exit code is folded from
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
    from .checker_types import DiffResult
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
    CONTRACT_COVERAGE = "contract_coverage"
    ANALYSIS_ASSURANCE = "analysis_assurance"
    CLEAN = "clean"


@dataclass(frozen=True)
class ExitDecision:
    """One comparison's fully explainable exit code.

    ``code`` is exactly ``max(compatibility_contribution,
    contract_coverage_contribution, analysis_assurance_contribution)`` --
    the identical value today's ad hoc fold chain in
    ``cli._exit_with_severity_or_verdict`` already produces, computed once
    here instead of via three separately-called functions. ``reasons``
    names every axis tied for that maximum; see :class:`ExitReason` for why
    a lower, non-winning contribution is excluded.
    """

    code: int
    reasons: tuple[ExitReason, ...]
    compatibility_contribution: int
    contract_coverage_contribution: int
    analysis_assurance_contribution: int

    def to_dict(self) -> dict[str, object]:
        """JSON-serializable form, for the report's ``exit`` block."""
        return {
            "code": self.code,
            "reasons": [r.value for r in self.reasons],
            "compatibility_contribution": self.compatibility_contribution,
            "contract_coverage_contribution": self.contract_coverage_contribution,
            "analysis_assurance_contribution": self.analysis_assurance_contribution,
        }


def resolve_exit_decision(
    *,
    compatibility_contribution: int,
    contract_coverage_contribution: int = 0,
    analysis_assurance_contribution: int = 0,
) -> ExitDecision:
    """Fold the three already-computed axis contributions into one decision.

    *compatibility_contribution* is the caller's own pre-computed
    compatibility-gate exit code -- either `severity.legacy_exit_code`
    (verdict-based, no severity config in effect) or
    `severity.compute_exit_code` (a severity map is in effect). This
    function does not choose between the two; that selection is exactly
    what PR 4/G2 still has to consolidate into one automatic algorithm.
    *contract_coverage_contribution*/*analysis_assurance_contribution*
    default to ``0`` (their "never asked the question" value, matching
    `contract_coverage_exit.coverage_exit_floor`/`analysis_assurance.
    analysis_assurance_exit_contribution`'s own fail-open defaults) so a
    caller with neither `--contract` nor `--require-complete-analysis` in
    effect can omit both.
    """
    contributions = {
        ExitReason.COMPATIBILITY_GATE: compatibility_contribution,
        ExitReason.CONTRACT_COVERAGE: contract_coverage_contribution,
        ExitReason.ANALYSIS_ASSURANCE: analysis_assurance_contribution,
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

    The single call site a native `compare`/`scan --against` invocation
    needs: it reproduces that function's exact fold order
    (compatibility → coverage floor → assurance floor, each `max`-based) as
    one canonical resolution, so a caller building the report's ``exit``
    block and a caller computing the real process exit code cannot read
    two different numbers for the same comparison.
    """
    from .analysis_assurance import analysis_assurance_exit_contribution
    from .contract_coverage_exit import coverage_exit_floor
    from .severity import compute_exit_code, legacy_exit_code

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
        contract_coverage_contribution=coverage_exit_floor(result),
        analysis_assurance_contribution=analysis_assurance_exit_contribution(
            result, require_complete=require_complete_analysis
        ),
    )
