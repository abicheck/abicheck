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
``_load_report_file`` report shape.

Split out of ``load.py`` (architecture/debt.yaml's ``no_growth`` line-count
ceiling for that file) the same way ``disposition_axis.py``/``scope_axis.py``
already split their own axis-specific loading logic out of it -- a pure code
move, no behavior change. ``load_no_baseline_report`` is called from
``_load_report_file`` for exactly one already-established discriminator
(``data.get("no_baseline") is True``), with every input this shape's own
report loading needs passed in rather than re-derived.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .contracts import _LoadedReport, _malformed_gate_report
from .disposition_axis import disposition_audit_block
from .gate import (
    COVERAGE_INCOMPLETE_EXIT,
    GateInfo,
    _analysis_assurance_exit,
    _contract_coverage_declared,
    _contract_coverage_exit,
    _contract_coverage_incomplete,
    _is_schema_valid_run_outcome,
)
from .reconcile import parse_report_findings
from .scope_axis import scope_completeness_exit, scope_completeness_incomplete


def load_no_baseline_report(
    data: Mapping[str, Any],
    *,
    target_id: str,
    head_sha: str | None,
    path: Path,
    effective_config_digest: str | None,
) -> _LoadedReport:
    """One no-baseline audit document's own ``_LoadedReport``.

    `compare --no-baseline`'s own audit document (ADR-068 D2) always
    carries `verdict: null` too, but for a wholly different reason than
    every null-verdict branch in `_load_report_file`: it isn't unresolved
    or refused, it *completed*, and by design reports no compatibility
    verdict at all -- there is no baseline to compare against. Checked by
    the caller before the native ADR-050 D2 `reason.kind` branch (an audit
    document carries no `reason` key at all, so that branch's own
    `isinstance(reason_obj, dict)` guard silently falls through) and before
    the generic `parse_report_verdict` fallback further down -- without
    that ordering a completed, gating audit (`--severity-preset` opted it
    into the AUDIT_GATE axis, exit 3) loaded as `gate=None`, the same
    "report carried no ABI verdict" shape an unavailable/missing report
    gets. That silently discarded a real gating finding for an
    optional/`on_missing_required: warn` target, and misclassified a
    required one as EMPTY coverage rather than a completed, gate-worthy
    result (Codex review, fresh evidence).

    `exit_axes` (`report/no_baseline_document.py`) is the one place this
    shape publishes each orthogonal axis's own raw contribution separately
    from the already-folded top-level `exit_code` -- read from there rather
    than the top-level `exit_code`, whose 0/1/3/7 audit scheme is not
    `GateInfo`'s documented 0/1/2/4 compare scheme (the same reason the
    scan-abort branch in `_load_report_file` floors onto
    `COVERAGE_INCOMPLETE_EXIT` rather than passing through scan's own raw
    exit code). `audit_gate`/`evidence_contract` are the two axes that
    represent *this report's own* gate-worthy result (a real
    BREAKING/API_BREAK-classified candidate-side finding, or an
    unsatisfiable evidence contract); `contract_coverage`/
    `analysis_assurance`/`incomplete_scope`/`no_comparison_completed` are
    already folded through the caller's own generic, shape-agnostic readers
    (`_contract_coverage_exit` etc., which already read this shape's
    identically-named root fields correctly) and must not be double-counted
    here.
    """
    raw_exit_axes = data.get("exit_axes")
    exit_axes = raw_exit_axes if isinstance(raw_exit_axes, Mapping) else {}

    def _axis(name: str) -> int:
        raw = exit_axes.get(name)
        return raw if isinstance(raw, int) and not isinstance(raw, bool) else 0

    audit_gate_axis = _axis("audit_gate")
    evidence_contract_axis = _axis("evidence_contract")
    audit_blocking_categories: set[str] = set()
    if audit_gate_axis:
        audit_blocking_categories.add("audit_gate")
    if evidence_contract_axis:
        audit_blocking_categories.add("evidence_contract_error")
    gate_exit_code = COVERAGE_INCOMPLETE_EXIT if audit_blocking_categories else 0
    # A pinned evidence contract this audit could not satisfy
    # (`run_outcome.operational: evidence_contract_error`, exit 7) means
    # no valid analysis ran at all -- the identical operational-failure
    # signal `check_report._classify_verdict` already checks before
    # exempting a no-baseline report from operational-error status.
    # Without checking it here too, this branch marked such a report
    # `completed_without_compatibility_verdict=True` unconditionally,
    # so the aggregate reported "complete coverage"/"audit completed"
    # for a run whose own producer says it produced no valid result
    # (Codex review, fresh evidence) -- the blocking gate alone doesn't
    # correct that: an operational failure and a real gating finding
    # are different facts, and a required-but-optional-gate report
    # would have shown neither.
    # `report/no_baseline.py::_run_outcome` always emits a full, schema-valid
    # `RunOutcome.to_dict()` block -- so a missing or schema-invalid one here
    # is itself a sign of a malformed/hand-authored document, not merely "an
    # audit that predates this field" (unlike the two-sided-report reader
    # this mirrors, `_run_outcome_gate_and_operational`, which does have a
    # genuinely-absent legacy case to fall back to). Checking only
    # `run_outcome.operational` in isolation -- as this used to -- let a
    # `run_outcome` missing every OTHER required key (or missing outright)
    # still read `operationally_failed=False` and fall through to marking
    # the report `completed_without_compatibility_verdict=True`: a malformed
    # envelope was silently treated as a genuine completed audit (Codex
    # review, fresh evidence). Fails closed the same way `load.py`'s own
    # `_malformed_gate_report` branches do, rather than reusing
    # `_run_outcome_gate_and_operational`'s exception-raising shape, since
    # this shape's `run_outcome.gate` is always `PolicyGateDecision.NONE` by
    # construction (`_run_outcome`'s own docstring) and carries no useful
    # gate/operational pair to fold in either case.
    run_outcome_raw = data.get("run_outcome")
    if not isinstance(run_outcome_raw, Mapping) or not _is_schema_valid_run_outcome(
        run_outcome_raw
    ):
        return _malformed_gate_report(
            target_id,
            data.get("library"),
            head_sha,
            path,
            "no-baseline audit's run_outcome is missing or schema-invalid",
        )
    operational_status = run_outcome_raw.get("operational")
    operationally_failed = operational_status not in ("none", "")
    # `analysis_assurance_exit_contribution`, the dedicated root key
    # `_analysis_assurance_exit()` reads, is a two-sided-report-only
    # field this shape never emits (its own assurance contribution
    # lives only in `exit_axes.analysis_assurance` and inside the full
    # `run_outcome.assurance` block) -- read both and take the max
    # rather than silently under-reporting whichever this report
    # doesn't carry.
    return _LoadedReport(
        target_id=target_id,
        verdict=None,
        gate=GateInfo(
            exit_code=gate_exit_code,
            blocking=gate_exit_code != 0,
            blocking_categories=tuple(sorted(audit_blocking_categories)),
            from_report=True,
        ),
        library=data.get("library"),
        head_sha=head_sha,
        # A completed run, not an unavailable/unresolved one -- no
        # "reason" a coverage report should surface as a gap. Except
        # when the audit itself failed operationally: that IS a gap
        # worth surfacing, the same way every other operational
        # sentinel branch in this module populates `reason`.
        reason=(
            f"audit did not complete: {operational_status}"
            if operationally_failed
            else None
        ),
        path=path,
        contract_coverage_exit=_contract_coverage_exit(data),
        contract_coverage_incomplete=_contract_coverage_incomplete(data),
        contract_coverage_declared=_contract_coverage_declared(data),
        analysis_assurance_exit=max(
            _analysis_assurance_exit(data), _axis("analysis_assurance")
        ),
        scope_completeness_exit=scope_completeness_exit(data),
        scope_completeness_incomplete=scope_completeness_incomplete(data),
        disposition_audit=disposition_audit_block(data),
        # `changes` is always `[]` for this shape (ADR-068 D2) --
        # `parse_report_findings` reads that as a real, complete, empty
        # compatibility change set (correctly: an audit has none, as a
        # known fact, not an unknown), so it is safe to call unchanged
        # rather than special-cased to `None`.
        findings=parse_report_findings(data),
        effective_config_digest=effective_config_digest,
        completed_without_compatibility_verdict=not operationally_failed,
    )
