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
``check_report.py``/``augment_report`` input -- a sibling of
``tests/test_check_report.py`` split out for the same file-size reason as
``tests/test_aggregate_no_baseline_audit.py``: architecture/debt.yaml's
``no_growth`` line-count ceiling for the parent test file.
"""

from __future__ import annotations

from abicheck.buildsource.check_report import (
    augment_report,
    derive_effective_depth,
    final_exit_code,
)


class TestDeriveEffectiveDepthNoBaseline:
    def test_no_baseline_audit_reads_depth_from_run_outcome_assurance(self):
        """Codex review, fresh evidence: `compare --no-baseline`'s own audit
        document has neither `old_evidence_depth`/`new_evidence_depth` (no
        OLD side) nor a `level` block (legacy scan's own shape) -- its
        achieved depth lives only in `run_outcome.assurance.
        effective_depth`, the same `AnalysisAssurance.to_dict()` block a
        two-sided compare report nests there too."""
        report = {
            "no_baseline": True,
            "run_outcome": {"assurance": {"effective_depth": "source"}},
        }
        effective, coverage = derive_effective_depth(report, "source")
        assert effective == "source"
        assert coverage == {"state": "complete", "reasons": []}

    def test_no_baseline_audit_shallower_than_requested_degrades_honestly(self):
        """The sharper bug: without this signal, a pinned depth: source
        audit that only reached headers fell through to the "no signal"
        branch, which reports the *requested* depth verbatim -- silently
        claiming the depth it asked for, not the one it got."""
        report = {
            "no_baseline": True,
            "run_outcome": {"assurance": {"effective_depth": "headers"}},
        }
        effective, coverage = derive_effective_depth(report, "source")
        assert effective == "headers"
        assert coverage == {
            "state": "degraded",
            "reasons": ["audit_achieved_headers"],
        }

    def test_no_baseline_audit_with_malformed_assurance_is_no_signal(self):
        report = {"no_baseline": True, "run_outcome": {"assurance": "not-a-dict"}}
        effective, coverage = derive_effective_depth(report, "headers")
        assert coverage["state"] == "unknown"
        assert effective == "headers"


class TestAugmentReportNoBaseline:
    def _no_baseline_report(self, *, findings=(), exit_code=0, operational=None):
        """A minimal real `compare --no-baseline` audit document
        (`report/no_baseline.py::_document_json`'s own shape)."""
        report = {
            "audit_report_schema_version": "1.3",
            "no_baseline": True,
            "library": "libpvxs",
            "new_version": "1.2.3",
            "verdict": None,
            "old_acquisition_state": "declared_absent",
            "changes": [],
            "findings": list(findings),
            "exit_code": exit_code,
        }
        if operational is not None:
            report["run_outcome"] = {"operational": operational}
        return report

    def test_no_baseline_audit_is_not_an_operational_error(self):
        """Codex review, fresh evidence: a `compare --no-baseline` audit
        report's `verdict` is always `null` -- neither a legacy compatibility
        verdict nor the `"ERROR"` sentinel -- so it previously fell through
        to the generic `scan_guard_triggered` operational-error branch,
        which `final_exit_code()` fails unconditionally regardless of
        gate-mode. A clean, zero-finding audit must classify as no
        operational error at all."""
        out = augment_report(
            self._no_baseline_report(),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out.get("operational_errors") == []
        assert (
            final_exit_code(
                "advisory",
                real_exit_code=0,
                operational_error=bool(out["operational_errors"]),
            )
            == 0
        )

    def test_no_baseline_audit_that_failed_its_evidence_contract_is_operational(
        self,
    ) -> None:
        """Codex review, fresh evidence: a no-baseline audit is not immune
        to operational failure -- a pinned evidence contract it could not
        satisfy (`run_outcome.operational: evidence_contract_error`, exit
        7) means no valid analysis ran at all. The unconditional "no
        baseline -> no operational error" exemption previously swallowed
        this too, so `gate-mode: advisory`/`deferred` turned a genuinely
        failed audit into a quiet exit 0."""
        out = augment_report(
            self._no_baseline_report(
                exit_code=7, operational="evidence_contract_error"
            ),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="source",
            gate_mode="advisory",
        )
        assert out.get("operational_errors")
        assert out["operational_errors"][0]["kind"] == "evidence_contract_error"
        assert (
            final_exit_code(
                "advisory",
                real_exit_code=0,
                operational_error=bool(out["operational_errors"]),
            )
            == 1
        )

    def test_no_baseline_audit_with_operational_none_is_unaffected(self) -> None:
        """A real (but not literally absent) `run_outcome.operational:
        none` must still read as clean -- only a non-`none` value is a
        real operational failure."""
        out = augment_report(
            self._no_baseline_report(operational="none"),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out.get("operational_errors") == []

    def test_no_baseline_audit_with_a_real_finding_is_still_not_operational(self):
        """A gating candidate-side finding is a real, reportable result --
        not an operational failure -- so `operational_errors` stays empty
        even when the audit found something (the AUDIT_GATE/exit-3 axis is
        orthogonal to this classification, handled entirely by the real
        exit code the caller supplies)."""
        out = augment_report(
            self._no_baseline_report(
                findings=[{"kind": "symbol_removed", "severity": "breaking"}],
                exit_code=3,
            ),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out.get("operational_errors") == []
        assert "compatibility_verdict" not in out

    def test_no_baseline_audit_keeps_its_own_schema_version_field(self):
        """The audit's own `audit_report_schema_version` (`report/
        no_baseline_document.py`'s own namespace, deliberately not
        `report_schema_version` -- stamping the compare report's schema
        counter onto it would offer a different document under the compare
        report's identity) must be left untouched, and the compare-report
        `report_schema_version` field must never be added to it."""
        out = augment_report(
            self._no_baseline_report(),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["audit_report_schema_version"] == "1.3"
        assert "report_schema_version" not in out

    def test_advisory_neutralizes_a_no_baseline_audits_exit_axes_audit_gate(self):
        """Codex review, fresh evidence: a no-baseline audit's AUDIT_GATE
        axis is a THIRD way this report can drive a blocking gate,
        structurally invisible to the `run_outcome.gate` zeroing above --
        that field is always `PolicyGateDecision.NONE` for this shape
        regardless of AUDIT_GATE (it tracks the two-sided compatibility
        gate, not this candidate-side one). The signal lives only in
        `exit_axes.audit_gate`, which `aggregate.load._load_report_file`
        reads directly -- so an advisory check-target run with a real
        gating finding still blocked the trailing aggregate job before
        this fix. `evidence_contract` is deliberately NOT neutralized: it
        is a comparison-never-completed-style failure, not a compatibility
        finding, the same distinction the exit-block loop draws for its
        own five "never completed" contributions."""
        out = augment_report(
            self._no_baseline_report(
                exit_code=3,
                findings=[{"kind": "symbol_removed", "severity": "breaking"}],
            )
            | {"exit_axes": {"audit_gate": 3, "evidence_contract": 0}},
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["exit_axes"]["audit_gate"] == 0

    def test_advisory_does_not_neutralize_the_evidence_contract_axis(self):
        out = augment_report(
            self._no_baseline_report(exit_code=7)
            | {"exit_axes": {"audit_gate": 0, "evidence_contract": 7}},
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="source",
            gate_mode="advisory",
        )
        assert out["exit_axes"]["evidence_contract"] == 7

    def test_advisory_neutralizes_a_no_baseline_audits_exit_axes_analysis_assurance(
        self,
    ) -> None:
        """Codex review, second round, fresh evidence: a no-baseline audit
        carries no dedicated root `analysis_assurance_exit_contribution`
        key at all (unlike a two-sided report), so the generic
        contract-coverage-block neutralization loop -- which already zeroes
        that dedicated key -- finds nothing to act on for this shape. The
        aggregate's own audit loader reads `exit_axes.analysis_assurance`
        directly, so leaving it unneutralized still gated an explicitly
        advisory `require-complete-analysis: true` audit."""
        out = augment_report(
            self._no_baseline_report(exit_code=1)
            | {
                "exit_axes": {
                    "audit_gate": 0,
                    "analysis_assurance": 1,
                    "evidence_contract": 0,
                }
            },
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["exit_axes"]["analysis_assurance"] == 0
