# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""ADR-049 D1's per-finding gate contribution from already-resolved inputs.

The one rule a report counts "gating" by. :func:`gate_contribution_for_change`
resolves a finding's verdict/category and applies it; a report projection
that already holds both (a :class:`~abicheck.report.finding.ReportFinding`)
calls :func:`resolved_gate_contribution` directly, so the
JSON ``gate_contribution`` field and every Markdown/digest/PR-comment gating
total come from the same function (SVS field report: a review group of
contract-excluded breaks rendered "6 gating finding(s)" over JSON
``gate_contribution: 0``).
"""

from __future__ import annotations

from datetime import date

from ..model.change_catalog.kinds import HasKind
from ..model.contract_finding_relevance import is_evaluated
from ..model.evidence_status import is_cross_source_resolved
from .severity import (
    _CATEGORY_EXIT_CODES,
    IssueCategory,
    KindSets,
    SeverityConfig,
    SeverityLevel,
    Verdict,
    classify_effective_change,
    effective_verdict_for_change,
    legacy_exit_code,
)


def gate_contribution_for_change(
    change: HasKind,
    config: SeverityConfig | None,
    *,
    policy: str | None = None,
    kind_sets: KindSets | None = None,
    policy_file: object | None = None,
    today: date | None = None,
) -> int:
    """What one finding contributes to the process exit code (ADR-049 D1).

    Resolves only the input *config*'s scheme reads -- the effective verdict
    for the legacy scheme, the issue category for the severity scheme -- and
    applies :func:`resolved_gate_contribution`'s rule. An excluded finding
    returns before either is resolved.
    """
    if not is_evaluated(change) or is_cross_source_resolved(change):
        return 0
    if config is None:
        verdict = effective_verdict_for_change(
            change,
            policy=policy,
            kind_sets=kind_sets,
            policy_file=policy_file,
            today=today,
        )
        return _contribution(None, verdict, None)
    category = classify_effective_change(
        change,
        policy=policy,
        kind_sets=kind_sets,
        policy_file=policy_file,
        today=today,
    )
    return _contribution(config, None, category)


def resolved_gate_contribution(
    change: object,
    config: SeverityConfig | None,
    *,
    verdict: Verdict | None = None,
    category: IssueCategory | None = None,
) -> int:
    """The gate contribution of *change*, from an already-resolved
    *verdict* (legacy scheme, *config* ``None``) or *category* (severity
    scheme). The one rule every consumer counts "gating" by -- the JSON
    ``gate_contribution`` field and every report's gating totals alike:

    - a ``NOT_EVALUATED`` finding (contract-proven out of scope, or
      unresolved) contributes ``0`` -- it is not on the compatibility axis;
    - a ``RESOLVED`` cross-source finding (plan F-9) contributes ``0``, the
      same reason :func:`~abicheck.policy.severity.gate_eligible_changes` excludes it;
    - under the severity scheme, the category's exit code when that
      category is configured ``error``, else ``0`` -- what
      :func:`compute_exit_code` folds with ``max``;
    - under the legacy scheme, the verdict's legacy exit code; both mappings
      are monotone in the same verdict order, so folding with ``max``
      reproduces :func:`legacy_exit_code` of the worst evaluated verdict.

    Never a new gate: every value is one :func:`compute_exit_code` or
    :func:`legacy_exit_code` already produces for the same finding.
    """
    if not is_evaluated(change) or is_cross_source_resolved(change):
        return 0
    return _contribution(config, verdict, category)


def _contribution(
    config: SeverityConfig | None,
    verdict: Verdict | None,
    category: IssueCategory | None,
) -> int:
    """The scheme half of the rule, for a finding already known to be gate-eligible."""
    if config is None:
        return 0 if verdict is None else legacy_exit_code(verdict)
    if category is None or config.level_for(category) != SeverityLevel.ERROR:
        return 0
    return _CATEGORY_EXIT_CODES[category]
