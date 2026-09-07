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

"""The ``compare --no-baseline`` report (ADR-068 D2, plan §6 Phase 2e).

Builds the JSON/Markdown report for a ``--no-baseline`` audit directly,
rather than through the legacy ``reporter.to_json`` chokepoint every
two-sided ``compare`` report renders through: that module is unclassified
(ADR-061), so a migrated-layer caller (``frontends``, this module's own
``report`` layer) cannot reach it at all
(``check_architecture.py``'s ``unclassified-import`` gate) -- and a
``--no-baseline`` report is trivial enough (an empty change set, by
construction; see :mod:`abicheck.workflows.no_baseline_compare`) that
reusing the two-sided severity/change-rendering machinery would be a much
larger dependency for no real benefit. This is the ADR-061 ``report/``
owner's job either way: "Add a report field, report schema, or output
format".
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..policy.outcome import OperationalStatus, PolicyGateDecision, RunOutcome
from ..policy.scope_completeness import resolve_scope_decision
from .comparison_scope import build_comparison_scope_section

if TYPE_CHECKING:
    from ..policy.scope_completeness import ScopeDecision
    from ..workflows.no_baseline_compare import NoBaselineCompareResult

__all__ = [
    "no_baseline_exit_code",
    "no_baseline_json_report",
    "no_baseline_markdown_report",
]

#: Independent of ``reporter.REPORT_SCHEMA_VERSION`` (the two-sided report's
#: own schema) -- this report carries a different, much smaller shape (no
#: ``changes``/``summary``/``severity`` blocks worth versioning the same
#: way), so it gets its own counter rather than borrowing one that promises
#: a shape this report doesn't have.
NO_BASELINE_REPORT_SCHEMA_VERSION = "1.0"


def _scope_decision(result: NoBaselineCompareResult) -> ScopeDecision:
    return resolve_scope_decision(result.acquisition, policy=None)


def _run_outcome(result: NoBaselineCompareResult) -> RunOutcome:
    """ADR-068 D2: OLD is ``declared_absent``, so this run never carries a
    compatibility contribution -- ``compatibility``/``gate``/``operational``
    read exactly as they would for a report that never ran a real
    comparison at all (:class:`~abicheck.policy.outcome.RunOutcome`'s own
    documented convention), while ``scope`` is decided the same way a
    two-sided run's would be."""
    return RunOutcome(
        compatibility=None,
        assurance=getattr(result.diff, "analysis_assurance", None),
        gate=PolicyGateDecision.NONE,
        operational=OperationalStatus.NONE,
        scope=_scope_decision(result).completeness,
    )


def no_baseline_exit_code(
    result: NoBaselineCompareResult, *, require_complete_analysis: bool = False
) -> int:
    """The whole exit-code contribution of a ``--no-baseline`` run.

    The compatibility axis contributes nothing (ADR-068 D2) -- coverage and
    analysis-assurance still apply exactly as they would for a two-sided
    run, and the completeness axis (D6/D7) reads ``0`` for the same reason
    ``no_comparison_completed``/``is_incomplete`` never fire for a
    ``declared_absent`` member -- all folded via the same ``max`` discipline
    every other orthogonal axis in this codebase uses (never an inline
    ``sys.exit`` computation of its own).
    """
    from ..analysis_assurance import analysis_assurance_exit_contribution
    from ..policy.contract_coverage_exit import coverage_exit_floor

    scope_decision = _scope_decision(result)
    coverage = coverage_exit_floor(result.diff)
    assurance = analysis_assurance_exit_contribution(
        result.diff, require_complete=require_complete_analysis
    )
    return max(
        coverage,
        assurance,
        scope_decision.incomplete_scope_exit_contribution,
        scope_decision.no_comparison_completed_exit_contribution,
    )


def no_baseline_json_report(result: NoBaselineCompareResult) -> dict[str, Any]:
    """The full JSON report for a ``--no-baseline`` audit.

    Deliberately not a projection of the two-sided report's shape padded
    out with nulls: ``no_baseline: true`` marks the shape up front, there is
    no ``old_version``/``old_file`` (OLD was never supplied, not merely
    empty), and ``verdict``/``changes`` read as the audit they are -- no
    verdict, an empty change set by construction (see the self-diff
    identity argument in :mod:`abicheck.workflows.no_baseline_compare`).
    """
    diff = result.diff
    return {
        "report_schema_version": NO_BASELINE_REPORT_SCHEMA_VERSION,
        "no_baseline": True,
        "library": diff.library,
        "new_version": diff.new_version,
        "verdict": None,
        "changes": [],
        "evidence_tiers": list(diff.evidence_tiers),
        "run_outcome": _run_outcome(result).to_dict(),
        "comparison_scope": build_comparison_scope_section(_scope_decision(result)),
    }


def no_baseline_markdown_report(result: NoBaselineCompareResult) -> str:
    """The Markdown rendering of :func:`no_baseline_json_report`."""
    diff = result.diff
    member = result.acquisition.members[0]
    lines = [
        f"# ABI audit: {diff.library} (no baseline)",
        "",
        "OLD side: **declared absent** (`--no-baseline`) -- this is an audit "
        "of the candidate build alone, not a compatibility comparison. No "
        "additions, removals, or compatibility verdict are reported.",
        "",
        f"- Candidate version: `{diff.new_version or '(unspecified)'}`",
        f"- Acquisition state (OLD): `{member.state.value}`",
        f"- Evidence tiers: {', '.join(diff.evidence_tiers) or '(none recorded)'}",
    ]
    return "\n".join(lines) + "\n"
