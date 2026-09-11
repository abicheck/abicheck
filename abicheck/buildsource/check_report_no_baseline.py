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

"""``compare --no-baseline``'s own audit document (ADR-068 D2) as a
``check_report.py``/``augment_report`` input.

Split out of ``check_report.py`` (architecture/debt.yaml's ``no_growth``
line-count ceiling for that file), the same way ``check_report_run_outcome.py``
already split its own sibling responsibility out of it -- a pure code move,
no behavior change. Each function here mirrors the exact call-site shape its
one caller in ``check_report.py`` already had.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..policy.outcome import OperationalStatus


def no_baseline_effective_depth(
    report: Mapping[str, Any],
) -> tuple[str, str] | None:
    """``(achieved_depth, source)`` from a no-baseline audit's own
    ``run_outcome.assurance.effective_depth`` -- ``None`` when this report
    isn't a no-baseline audit, or carries no usable depth signal there.

    `compare --no-baseline`'s own audit document (ADR-068 D2) carries
    neither `old_evidence_depth`/`new_evidence_depth` (there is no OLD
    side) nor a `level` block (that's legacy scan's own shape) -- its
    achieved depth is recorded only here, the same `AnalysisAssurance.
    to_dict()` block a two-sided compare report nests there too (Codex
    review, fresh evidence). Without this, every completed audit fell
    through to `derive_effective_depth`'s "neither signal present" case:
    reported `state: unknown` unconditionally (even a perfectly on-depth
    audit), and -- because that branch trusts the *request* verbatim as
    the reported `effective_depth` -- a pinned `depth: source` audit that
    only actually reached `headers` was stamped `effective_depth: source`,
    silently claiming the depth it asked for rather than the one it got.

    Callers validate the returned depth string against their own
    ``_DEPTH_RANK`` (this module stays free of that table's ownership).
    """
    run_outcome = report.get("run_outcome")
    assurance = run_outcome.get("assurance") if isinstance(run_outcome, dict) else None
    audit_depth = (
        assurance.get("effective_depth") if isinstance(assurance, dict) else None
    )
    if isinstance(audit_depth, str):
        return audit_depth, "audit"
    return None


def neutralize_no_baseline_axes(report: dict[str, Any]) -> None:
    """Zero a no-baseline audit's own ``exit_axes.audit_gate``/
    ``.contract_coverage``/``.analysis_assurance`` in place -- and recompute
    the document's own top-level ``exit_code`` to match -- for
    ``gate-mode: advisory``.

    A `compare --no-baseline` audit's own AUDIT_GATE axis (ADR-068's
    2026-09-10 amendment) is a THIRD way this report can drive a blocking
    gate, structurally invisible to `_neutralize_gate`'s own `severity`/
    `run_outcome.gate` zeroing: `run_outcome.gate` for this shape is
    always `PolicyGateDecision.NONE` regardless of AUDIT_GATE
    (`report/no_baseline.py::_run_outcome` -- that field tracks the
    two-sided compatibility gate, not this candidate-side one), and there
    is no `severity` block either. The signal lives only in
    `exit_axes.audit_gate`, which `aggregate.load._load_report_file` reads
    directly to build this shape's own `GateInfo` -- so leaving it
    unneutralized left an advisory audit's real gating finding still
    blocking the trailing aggregate job, exactly the failure mode every
    other axis in the caller exists to prevent (Codex review, fresh
    evidence). `exit_axes.analysis_assurance` gets the identical treatment
    for the identical reason (Codex review, second round, fresh evidence):
    this shape carries no dedicated root `analysis_assurance_exit_
    contribution` key at all (unlike a two-sided report), so the generic
    contract-coverage-block loop the caller already runs -- which already
    zeroes that dedicated key -- finds nothing to act on; the aggregate's
    own audit loader reads `exit_axes.analysis_assurance` directly (via
    `max()` against that always-absent dedicated key), so an unneutralized
    value there still gated an explicitly advisory
    `require-complete-analysis: true` audit.

    `exit_axes.contract_coverage` (Codex review, third round, fresh
    evidence) mirrors the *value* the caller's generic loop already zeroes
    at the dedicated root `contract_coverage_exit_contribution` key this
    shape also carries (`report/no_baseline.py` serializes the identical
    `coverage_exit_floor(diff)` result under both names) -- but the generic
    loop only rewrites that one key, leaving this copy stale. Left
    unzeroed, the persisted document read self-contradictory --
    `contract_coverage_exit_contribution: 0` beside `exit_axes.
    contract_coverage: 1` -- and, since the document's own top-level
    `exit_code` was computed once at construction time as `max(exit_axes.
    values())` and never recomputed here, an advisory audit's *persisted*
    `exit_code` stayed at its original nonzero value even after every axis
    inside `exit_axes` was neutralized. Recomputing it from the
    already-updated `updated_axes` below closes both gaps in one place --
    the aggregate's own gate was never affected (it reads the dedicated
    root key, not `exit_axes`/`exit_code`), but a consumer reading this
    document's own canonical exit fields directly was.

    `exit_axes.evidence_contract`/`.incomplete_scope`/
    `.no_comparison_completed` are deliberately left untouched -- each is a
    comparison-never-completed-style failure, not a compatibility/
    assurance-style finding advisory mode neutralizes, the same distinction
    the caller's own exit-block loop draws for its own five "never
    completed" contributions.
    """
    exit_axes = report.get("exit_axes")
    if isinstance(exit_axes, dict):
        updated_axes = dict(exit_axes)
        for axis in ("audit_gate", "contract_coverage", "analysis_assurance"):
            if axis in updated_axes:
                updated_axes[axis] = 0
        report["exit_axes"] = updated_axes
        if "exit_code" in report:
            report["exit_code"] = max(updated_axes.values(), default=0)


def classify_no_baseline_verdict(
    out: dict[str, Any], report: Mapping[str, Any], run_outcome: Mapping[str, Any]
) -> bool:
    """Handle ``_classify_verdict``'s no-baseline-audit branch in place.

    Returns ``True`` when *report* is a no-baseline audit document and this
    function has already set ``out["operational_errors"]`` (the caller
    returns immediately); ``False`` otherwise (the caller falls through to
    its own legacy-verdict/operational-error branches unchanged).

    `compare --no-baseline`'s own audit document (`report/no_baseline.py`)
    always carries `verdict: null` -- ADR-068 D2, "an audit reports no
    additions, removals, or compatibility verdict at all," so the caller's
    `raw_verdict` is unconditionally `None` here, never a legacy
    compatibility verdict. Checked via the same `no_baseline` discriminator
    `action/run.sh`'s own `no_baseline_audit` report query uses, before
    either of the caller's other checks -- without this, `None` matches
    neither `LEGACY_VERDICT_VALUES` nor `OPERATIONAL_ERROR_VERDICT` and
    fell through to the generic `scan_guard_triggered` branch,
    misclassifying every no-baseline audit -- including a clean,
    zero-finding one -- as an operational failure. `final_exit_code()`
    treats any `operational_errors` entry as unconditional exit 1, ignoring
    `gate-mode: advisory`/`deferred` entirely, so this silently failed
    every `check-target` single-build audit using the audit-only shape
    (Codex review, fresh evidence). There is intentionally no
    `compatibility_verdict` set here (D2 again): an audit has none, and
    leaving the key unset is the truthful answer, not a degraded one.
    """
    if report.get("no_baseline") is not True:
        return False
    # A no-baseline audit is not immune to operational failure -- a
    # pinned evidence contract it could not satisfy (`run_outcome.
    # operational: evidence_contract_error`, exit 7) means no valid
    # analysis ran at all, same as any other operational sentinel the
    # caller recognizes. Checked before the unconditional "no error"
    # exemption below: without this, `gate-mode: advisory`/`deferred`
    # turned a failed audit into a quiet exit 0, since no valid
    # analysis completed to report a candidate-side finding from
    # (Codex review, fresh evidence).
    operational_status = run_outcome.get("operational")
    if isinstance(operational_status, str) and operational_status not in (
        OperationalStatus.NONE.value,
        "",
    ):
        msg = report.get("error") or f"audit did not complete: {operational_status}"
        out["operational_errors"] = [{"kind": operational_status, "message": str(msg)}]
        return True
    out.setdefault("operational_errors", [])
    return True


def has_own_no_baseline_schema_version(report: Mapping[str, Any]) -> bool:
    """Whether *report* already carries (or is) a no-baseline audit
    document, so ``_stamp_schema_version`` must leave it alone.

    A `compare --no-baseline` audit document carries its own
    `audit_report_schema_version` counter, in its own namespace
    (`report/no_baseline_document.py`) -- deliberately not
    `report_schema_version`, since the packaged `compare_report.schema.
    json` tells consumers to accept any version sharing its MAJOR
    component, so stamping an audit into that field would offer a
    *different* document under the compare report's identity (the exact
    reasoning that field's own docstring gives). Recognised by its own
    `no_baseline` discriminator too, not only by the schema-version key's
    presence, so a stray hand-authored document missing that key is still
    left alone (Codex review, fresh evidence: an unguarded audit document
    previously got `report_schema_version` stamped onto it unconditionally).
    """
    return "audit_report_schema_version" in report or report.get("no_baseline") is True
