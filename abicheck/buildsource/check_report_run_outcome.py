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

"""ADR-063 Phase 7's ``run_outcome`` block for ``check_report.py``'s three
synthetic report builders, plus the legacy-report backfill ``augment_
report`` needs before it stamps an older report with the current schema
version.

Split out of ``check_report.py`` purely to keep that already-near-the-cap
module from growing (mirrors ``check_report_exit_backfill.py``'s own
split-for-size precedent) -- each of ``build_operational_error_report()``/
``build_bootstrap_report()``/``build_new_target_report()`` represents a run
that never computed a real compatibility verdict at all, so
``compatibility``/``assurance``/``gate`` all stay at their honest "nothing
computed" values and only the one axis the builder actually represents
(``operational`` for a resolve-baseline failure, ``lifecycle`` for a
bootstrap/new-target pass) is populated.
"""

from __future__ import annotations

from typing import Any

from ..policy.outcome import (
    OperationalStatus,
    PolicyGateDecision,
    RunOutcome,
    TargetLifecycle,
    policy_gate_decision_for_exit_code,
    run_outcome_dict_for_release,
    run_outcome_dict_for_scan,
)

__all__ = ["backfill_run_outcome", "synthetic_run_outcome"]


def synthetic_run_outcome(
    *,
    operational: OperationalStatus = OperationalStatus.NONE,
    lifecycle: TargetLifecycle = TargetLifecycle.EXISTING,
) -> dict[str, Any]:
    return RunOutcome(
        compatibility=None,
        assurance=None,
        gate=PolicyGateDecision.NONE,
        operational=operational,
        lifecycle=lifecycle,
    ).to_dict()


def backfill_run_outcome(out: dict[str, Any]) -> None:
    """Synthesize ``run_outcome`` in place for an older report that never
    carried one, before ``check_report._stamp_schema_version`` upgrades its
    marker to the current schema (Codex review, fresh evidence) -- the
    sibling gap ``check_report_exit_backfill.backfill_exit_block_fields``
    already closes for the ``exit`` block itself. A no-op when ``out``
    already carries ``run_outcome`` (a report produced by an already-
    upgraded writer).

    Dispatches on the same report-shape markers ``check_report.
    _stamp_schema_version`` itself keys on: a scan report (``scan_schema_
    version``) reuses :func:`~abicheck.policy.outcome.run_outcome_dict_for_
    scan` exactly the way ``GateInfo.from_scan_report`` would read it back;
    a release/bundle report (``libraries``+``old_dir``) reuses
    :func:`~abicheck.policy.outcome.run_outcome_dict_for_release`, with its
    own ``compatibility_contribution`` fallback to ``severity.exit_code``
    or the legacy verdict mapping when the original ``exit`` block never
    carried that key (this function must run *before* ``check_report.
    augment_report``'s own call to ``backfill_exit_block_fields``, which
    would otherwise default it to 0 -- indistinguishable from a real
    confirmed-clean report -- before this function ever sees it). Neither
    call site can reach ``cli_compare_release_helpers._release_completed_
    compatibility_verdict``'s own ERROR/not_comparable-excluding precision
    (that helper lives in a `frontends`-classified module this leaf may not
    import) -- an older release report's raw top-level ``verdict`` is used
    directly, same fail-open imprecision `Verdict.__call__` already handles
    by falling back to ``compatibility=None``. A native compare-shaped
    report (neither of the above) derives ``gate`` from whichever exit code
    is available (``severity.exit_code`` if present, else the legacy
    verdict mapping) and ``compatibility`` from ``verdict`` directly.
    """
    if "run_outcome" in out:
        return
    raw_verdict = out.get("verdict")
    if "scan_schema_version" in out:
        exit_code = out.get("exit_code")
        report: dict[str, Any] = out
        if "exit" not in out:
            # `cli_scan._emit_scan_abort_report`'s own persisted JSON shape
            # (pre-1.24, before that writer carried `run_outcome` itself)
            # nests the abort's preserved exit decision under `diff.exit`,
            # not the top-level `exit` key `scan_report_abort_compatibility_
            # contribution` reads -- that top-level shape is only
            # `service_scan.ScanResult.report`'s own, typed-API-only
            # envelope (Codex review, fresh evidence: without this, a
            # legacy BUDGET_OVERFLOW/EVIDENCE_CONTRACT_ERROR report's
            # already-found ABI break was silently lost on backfill, since
            # the reader found nothing at the top-level key it expected).
            diff = out.get("diff")
            nested_exit = diff.get("exit") if isinstance(diff, dict) else None
            if isinstance(nested_exit, dict):
                report = {**out, "exit": nested_exit}
        out["run_outcome"] = run_outcome_dict_for_scan(
            str(raw_verdict) if raw_verdict is not None else "",
            exit_code
            if isinstance(exit_code, int) and not isinstance(exit_code, bool)
            else 0,
            report=report,
        )
        return
    from ..change_registry_types import Verdict
    from ..policy.severity import legacy_exit_code

    compatibility: Verdict | None
    try:
        compatibility = Verdict(raw_verdict) if isinstance(raw_verdict, str) else None
    except ValueError:
        compatibility = None

    if "libraries" in out and "old_dir" in out:
        # `exit`, when present, is only trusted for `compatibility_
        # contribution` if that specific key survived -- this runs before
        # `backfill_exit_block_fields` (Codex review, fresh evidence: that
        # backfill unconditionally defaults every missing `*_contribution`
        # key, including this one, to 0, indistinguishable from a real
        # confirmed-clean report; calling this first sees the true original
        # shape). Falls back to `severity.exit_code`, then the legacy
        # verdict mapping -- never the backfilled 0 -- so a legacy BREAKING
        # release report with no exit/severity block still gets gate:
        # abi_breaking instead of silently passing aggregation as clean.
        exit_decision = out.get("exit")
        if (
            not isinstance(exit_decision, dict)
            or "compatibility_contribution" not in exit_decision
        ):
            severity = out.get("severity")
            sev_exit = severity.get("exit_code") if isinstance(severity, dict) else None
            if not isinstance(sev_exit, int) or isinstance(sev_exit, bool):
                sev_exit = (
                    legacy_exit_code(compatibility) if compatibility is not None else 0
                )
            exit_decision = {
                **(exit_decision if isinstance(exit_decision, dict) else {}),
                "compatibility_contribution": sev_exit,
            }
        out["run_outcome"] = run_outcome_dict_for_release(
            str(raw_verdict) if raw_verdict is not None else "NO_CHANGE",
            exit_decision,
        )
        return

    severity = out.get("severity")
    exit_code = severity.get("exit_code") if isinstance(severity, dict) else None
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        exit_code = legacy_exit_code(compatibility) if compatibility is not None else 0
    out["run_outcome"] = RunOutcome(
        compatibility=compatibility,
        assurance=None,
        gate=policy_gate_decision_for_exit_code(exit_code),
        operational=OperationalStatus.NONE,
        lifecycle=TargetLifecycle.EXISTING,
    ).to_dict()
