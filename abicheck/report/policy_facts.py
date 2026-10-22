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

"""Policy and release facts a source module needs, resolved in one report owner.

``reporter.py`` (the JSON path) and ``reporter_markdown.py`` read a
``DiffResult`` and build the structures their projections format. The facts
below are the decisions those builders depend on: the effective kind sets,
the severity gate, the run's summary counts, the release recommendation and
the ``--require-complete-analysis`` assurance block. They resolve here, so a
source module reaches the policy and semver owners only through this boundary
(the same route ``html_report.py`` already takes; see ``report/AGENTS.md``).

Nothing in this module formats output, and nothing decides anything the policy
owners did not decide: each function is a named, typed call to the owner that
makes the decision. A renderer must not import this module to make a decision
of its own; it reads the values its compute half passes it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ..policy.classification import evidence_status_for_result, impact_for
from ..policy.evaluate import effective_kind_sets, evaluate
from ..policy.gate_decision import gate_decision_for_result
from ..policy.reclassify import active_reclassify_rules
from ..policy.severity import compute_exit_code
from ..report_classifications import (
    ENVIRONMENT_DRIFT_KINDS as ENVIRONMENT_DRIFT_KINDS,
    category as category,
    kind_str as kind_str,
    severity as severity,
)
from ..report_summary import (
    build_summary,
    compatibility_metrics as compatibility_metrics,
)
from ..semver import recommend_release_for_report

if TYPE_CHECKING:
    from datetime import date

    from ..checker import DiffResult
    from ..model.change_catalog.kinds import ChangeKind, HasKind
    from ..model.evidence_status import EvidenceStatus
    from ..model.policy_file_protocol import ReclassifyRuleProtocol
    from ..policy.evaluate import ClassifiedDiff
    from ..policy.severity import GateDecision, KindSets, SeverityConfig
    from ..report_summary import ReportSummary
    from ..semver import ReleaseRecommendation
    from .finding import ReportFinding


@dataclass(frozen=True, slots=True)
class PolicyFacts:
    """The kind sets and severity gate one comparison resolves to."""

    kind_sets: KindSets
    gate: GateDecision | None


@dataclass(frozen=True, slots=True)
class StatFacts:
    """Every policy and release fact the summary-only ``--stat`` JSON needs.

    *assurance* is ``(block, exit_contribution)`` when the run carries an
    analysis-assurance block, else ``None`` -- the two values are only
    meaningful together, and the exit contribution is only computed when the
    block exists.
    """

    policy: PolicyFacts
    summary: ReportSummary
    release_recommendation: ReleaseRecommendation
    assurance: tuple[dict[str, Any], int] | None


def kind_sets_for(result: DiffResult) -> KindSets:
    """The effective (breaking, api_break, compatible, risk) kind sets for *result*."""
    return effective_kind_sets(result)


def policy_facts_for(
    result: DiffResult,
    severity_config: SeverityConfig | None,
    *,
    today: date | None = None,
) -> PolicyFacts:
    """Kind sets plus the severity gate (``None`` when no severity config is given)."""
    return PolicyFacts(
        kind_sets=effective_kind_sets(result),
        gate=gate_decision_for_result(result, severity_config, today=today),
    )


def summary_for(
    result: DiffResult, *, findings: Sequence[ReportFinding] | None = None
) -> ReportSummary:
    """The run's canonical summary counts.

    *findings* is an envelope's already-resolved ``ReportFinding`` sequence when
    the caller has one; ``None`` lets :func:`build_summary` resolve it itself.
    """
    if findings is None:
        return build_summary(result)
    return build_summary(result, findings=findings)


def release_recommendation_for(result: DiffResult) -> ReleaseRecommendation:
    """The release recommendation under *result*'s stated versioning policy."""
    return recommend_release_for_report(result)


def stat_facts_for(
    result: DiffResult,
    severity_config: SeverityConfig | None,
    *,
    require_complete_analysis: bool = False,
) -> StatFacts:
    """Resolve every fact :func:`abicheck.reporter.to_stat_json` consumes."""
    from ..policy.analysis_assurance import (
        analysis_assurance_exit_contribution,
        analysis_assurance_report_dict,
    )

    block = analysis_assurance_report_dict(result)
    assurance = None
    if block is not None:
        assurance = (
            block,
            analysis_assurance_exit_contribution(
                result, require_complete=require_complete_analysis
            ),
        )
    return StatFacts(
        policy=policy_facts_for(result, severity_config),
        summary=summary_for(result),
        release_recommendation=release_recommendation_for(result),
        assurance=assurance,
    )


def classification_for(result: DiffResult) -> ClassifiedDiff:
    """The run's findings classified under its own ``policy``/``policy_file``."""
    return evaluate(result)


def evidence_status_of(
    change: HasKind, evidence_tiers: Sequence[str] = ()
) -> EvidenceStatus | None:
    """The evidence status one change carries, given the run's evidence tiers."""
    return evidence_status_for_result(change, evidence_tiers)


def impact_text_for(kind: ChangeKind, evidence_status: EvidenceStatus | None) -> str:
    """The impact sentence for *kind*, with the evidence caveat *evidence_status* implies."""
    return impact_for(kind, evidence_status)


def reclassify_rules_in_effect(
    rules: Sequence[ReclassifyRuleProtocol], today: date | None
) -> list[ReclassifyRuleProtocol]:
    """The reclassify rules not yet past their ``expires`` date on *today*."""
    return active_reclassify_rules(rules, today)


def gate_exit_code_for(result: DiffResult, severity_config: SeverityConfig) -> int:
    """The exit code *result* scores to under *severity_config*, on its own policy."""
    return compute_exit_code(
        result.changes,
        severity_config,
        policy=result.policy,
        kind_sets=effective_kind_sets(result),
        policy_file=result.policy_file,
    )
