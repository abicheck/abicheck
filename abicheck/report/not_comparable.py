# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""The ADR-050 D2 comparability-refusal report document.

``checker.compare``'s gate raises before any ``DiffResult`` exists, so this
document is not a projection of a workflow result — but it is still a report,
carries a report schema version, and is consumed by the same CI tooling, so
it is built and rendered here rather than assembled inline in a CLI frontend.
"""

from __future__ import annotations

from ..policy.outcome import (
    OperationalStatus as OperationalStatus,  # re-exported: frontends may not import `policy` directly
    PolicyGateDecision,
    RunOutcome,
    policy_gate_decision_for_exit_code as policy_gate_decision_for_exit_code,  # re-exported, same reason
    run_outcome_dict_for_scan as run_outcome_dict_for_scan,  # re-exported, same reason (cli_scan.py)
)
from ..policy.outcome_release import (
    run_outcome_dict_for_release as run_outcome_dict_for_release,  # re-exported, same reason (cli_compare_release*.py)
)
from .document import ReportDocument
from .render_json import render_json


def not_comparable_document(
    library: str,
    old_version: str,
    new_version: str,
    kind: str,
    message: str,
    *,
    report_schema_version: str,
    operational: OperationalStatus,
) -> ReportDocument:
    """Build the schema-conformant ``{"verdict": null, "reason": {...}}`` report.

    The schema version is a parameter rather than an import on purpose:
    ADR-061 D1 gives ``report`` ownership of *report* schemas, but
    ``abicheck.schemas`` is one package holding the report, aggregate, build
    evidence, and build-source-pack schemas together, so classifying it
    ``report`` would claim ownership of three schemas this layer must not own.
    Splitting that package is its own migration; until then the caller states
    the version it is emitting.

    *operational* is a required parameter for the identical reason
    (ADR-063 Phase 7): this document is the ADR-050 D2 comparability
    refusal itself, so it does not decide the axis on its own -- every real
    caller passes :attr:`~abicheck.policy.outcome.OperationalStatus.
    NOT_COMPARABLE`, but hardcoding that here would let this module silently
    claim an axis value the caller never actually asserted.
    """
    outcome = RunOutcome(
        compatibility=None,
        assurance=None,
        gate=PolicyGateDecision.NONE,
        operational=operational,
    )
    return ReportDocument.from_mapping(
        {
            "report_schema_version": report_schema_version,
            "library": library,
            "old_version": old_version,
            "new_version": new_version,
            "verdict": None,
            "reason": {"kind": kind, "message": message},
            "run_outcome": outcome.to_dict(),
        }
    )


def render_not_comparable_json(
    library: str,
    old_version: str,
    new_version: str,
    kind: str,
    message: str,
    *,
    report_schema_version: str,
    operational: OperationalStatus,
) -> str:
    """Render the refusal report as JSON."""
    return render_json(
        not_comparable_document(
            library,
            old_version,
            new_version,
            kind,
            message,
            report_schema_version=report_schema_version,
            operational=operational,
        )
    )
