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

"""ADR-063 Phase 7's ``run_outcome`` block, as emitted by the four scan
writers: ``scan_engine.ScanOutcome.to_dict``, ``service_scan.ScanResult.
to_dict``/``ScanSetResult.to_dict``, and ``cli_scan._emit_scan_abort_
report``.

Split out of ``tests/test_run_outcome.py`` once that file crossed the
architecture gate's 1200-line test-file cap (Codex review follow-up round)
-- this class was its largest, most self-contained, so moving it here
(rather than adding a debt.yaml growth entry) keeps the parent file under
its cap without accepting new debt, mirroring ``tests/test_release_run_
outcome.py``'s own earlier split for the identical reason.
"""

from __future__ import annotations

import json

from abicheck.workflows.aggregate.gate import GateInfo


class TestScanWritersEmitStructuredFieldsTakenByTheReader:
    def _assert_structured_path_taken(self, report: dict) -> None:
        """Deletes the top-level `exit_code` (what the legacy fallback
        needs) and confirms `GateInfo.from_scan_report` still resolves --
        proof the reader took the structured path, not the fallback."""
        assert "run_outcome" in report
        stripped = dict(report)
        del stripped["exit_code"]
        gate = GateInfo.from_scan_report(stripped)
        assert gate is not None

    def test_scan_outcome_to_dict(self):
        from abicheck.buildsource.risk import RiskScore
        from abicheck.scan_engine import ScanOutcome

        outcome = ScanOutcome(
            mode="ci",
            resolved_method="s3",
            depth="headers",
            collect_mode="target",
            risk=RiskScore(total=0),
            auto=False,
            changed_path_count=0,
            changed_path_source="none",
            verdict="COMPATIBLE",
            exit_code=0,
        )
        report = outcome.to_dict()
        assert report["run_outcome"]["gate"] == "none"
        assert report["run_outcome"]["operational"] == "none"
        self._assert_structured_path_taken(report)

    def test_scan_result_to_dict(self):
        from abicheck.service_scan import ScanResult

        result = ScanResult(verdict="BREAKING", exit_code=4)
        report = result.to_dict()
        assert report["run_outcome"]["gate"] == "abi_breaking"
        self._assert_structured_path_taken(report)

    def test_scan_set_result_to_dict(self):
        from abicheck.service_scan import ScanSetResult

        result = ScanSetResult(verdict="BUDGET_OVERFLOW", exit_code=5)
        report = result.to_dict()
        assert report["run_outcome"]["operational"] == "budget_overflow"
        self._assert_structured_path_taken(report)

    def test_scan_set_result_preserves_member_evidence_error_alongside_stronger_break(
        self,
    ):
        """Codex review (P2), end-to-end: one member finds a real API break,
        a *different* member aborts with EVIDENCE_CONTRACT_ERROR --
        _aggregate_scan_set_verdict correctly reports the stronger API_BREAK
        as the set verdict, but run_outcome must still surface the member
        abort via .operational, not silently drop it."""
        from pathlib import Path

        from abicheck.service_scan import ScanArtifactResult, ScanResult, ScanSetResult

        per_artifact = [
            ScanArtifactResult(
                artifact=Path("a.so"),
                result=ScanResult(verdict="API_BREAK", exit_code=2),
            ),
            ScanArtifactResult(
                artifact=Path("b.so"),
                result=ScanResult(verdict="EVIDENCE_CONTRACT_ERROR", exit_code=1),
            ),
        ]
        result = ScanSetResult(
            verdict="API_BREAK",
            exit_code=2,
            per_artifact=per_artifact,
        )
        report = result.to_dict()
        assert report["run_outcome"]["gate"] == "potential_breaking"
        assert report["run_outcome"]["operational"] == "evidence_contract_error"

    def test_scan_set_result_preserves_completed_break_across_set_level_budget_overflow(
        self,
    ):
        """Codex review (P2), fresh evidence: when an artifact-set scan
        completes a BREAKING member and a *later* member hits
        BUDGET_OVERFLOW, _aggregate_scan_set_verdict's own step 1 correctly
        reports BUDGET_OVERFLOW/exit 5 as the SET's own verdict (an
        unfinished analysis dominates), but run_outcome must not lose the
        completed member's real break from its independent compatibility
        axis just because a different, later member never finished."""
        from pathlib import Path

        from abicheck.service_scan import ScanArtifactResult, ScanResult, ScanSetResult

        per_artifact = [
            ScanArtifactResult(
                artifact=Path("a.so"),
                result=ScanResult(verdict="BREAKING", exit_code=4),
            ),
            ScanArtifactResult(
                artifact=Path("b.so"),
                result=ScanResult(verdict="BUDGET_OVERFLOW", exit_code=5),
            ),
        ]
        result = ScanSetResult(
            verdict="BUDGET_OVERFLOW",
            exit_code=5,
            per_artifact=per_artifact,
        )
        report = result.to_dict()
        assert report["verdict"] == "BUDGET_OVERFLOW"
        assert report["run_outcome"]["compatibility"] == "BREAKING"
        assert report["run_outcome"]["gate"] == "abi_breaking"
        assert report["run_outcome"]["operational"] == "budget_overflow"

    def test_scan_set_result_preserves_clean_completed_member_across_budget_overflow(
        self,
    ):
        """Codex review (P2), fresh evidence beyond the break-only fix
        above: a completed member's own NO_CHANGE/COMPATIBLE/COMPATIBLE_
        WITH_RISK result is exit code 0, ambiguous among the three by the
        contribution alone -- without the verdict string itself,
        compatibility stayed null even though a real, clean comparison
        completed before a later member's BUDGET_OVERFLOW, contradicting
        "null means nothing was compared"."""
        from pathlib import Path

        from abicheck.service_scan import ScanArtifactResult, ScanResult, ScanSetResult

        per_artifact = [
            ScanArtifactResult(
                artifact=Path("a.so"),
                result=ScanResult(verdict="COMPATIBLE_WITH_RISK", exit_code=0),
            ),
            ScanArtifactResult(
                artifact=Path("b.so"),
                result=ScanResult(verdict="BUDGET_OVERFLOW", exit_code=5),
            ),
        ]
        result = ScanSetResult(
            verdict="BUDGET_OVERFLOW",
            exit_code=5,
            per_artifact=per_artifact,
        )
        report = result.to_dict()
        assert report["verdict"] == "BUDGET_OVERFLOW"
        assert report["run_outcome"]["compatibility"] == "COMPATIBLE_WITH_RISK"
        assert report["run_outcome"]["gate"] == "none"
        assert report["run_outcome"]["operational"] == "budget_overflow"

    def test_scan_set_result_preserves_bundle_incomplete_alongside_stronger_verdict(
        self,
    ):
        """Codex review (P2): run_scan_set's own bundle-incomplete branch
        keeps a *stronger* member's real API_BREAK/BREAKING as the reported
        verdict (never overridden to the BUNDLE_INCOMPLETE sentinel), while
        still setting bundle_incomplete=True -- run_outcome must surface the
        incomplete cross-library audit via .operational even though
        *verdict* itself never says so."""
        from abicheck.service_scan import ScanSetResult

        result = ScanSetResult(
            verdict="API_BREAK",
            exit_code=2,
            bundle_incomplete=True,
        )
        report = result.to_dict()
        assert report["run_outcome"]["gate"] == "potential_breaking"
        assert report["run_outcome"]["operational"] == "extraction_error"

    def test_scan_set_result_bundle_incomplete_end_to_end(self):
        """Codex review (P2), end-to-end: run_scan_set's own BUNDLE_INCOMPLETE
        verdict/exit_code=1 must not read as a real compatibility gate."""
        from abicheck.service_scan import ScanSetResult

        result = ScanSetResult(
            verdict="BUNDLE_INCOMPLETE",
            exit_code=1,
            bundle_incomplete=True,
        )
        report = result.to_dict()
        assert report["run_outcome"]["gate"] == "none"
        assert report["run_outcome"]["operational"] == "extraction_error"

    def test_native_cli_scan_abort_json_carries_run_outcome(self):
        """Codex review (P2): cli_scan._emit_scan_abort_report is a fourth,
        independent scan writer -- a hand-built --format json envelope for
        a budget-overflow/evidence-contract-error abort, distinct from
        ScanOutcome/ScanResult/ScanSetResult -- that claimed scan_schema_
        version 1.24 while never emitting run_outcome at all."""
        import contextlib
        import io

        from abicheck.cli_scan import _emit_scan_abort_report

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            _emit_scan_abort_report("budget_overflow", "json", None)
        report = json.loads(buf.getvalue())
        assert report["run_outcome"]["operational"] == "budget_overflow"
        self._assert_structured_path_taken(report)

    def test_scan_outcome_coverage_only_exit_1_reads_gate_none_end_to_end(self):
        """Codex review (P1), end-to-end through the real writer: a legacy-
        scheme scan whose own compatibility is clean but whose contract
        coverage is incomplete folds to a top-level exit_code of 1
        (cli_scan_baseline's own max() fold) -- the writer must read its own
        diff_summary's declared contract_coverage_exit_contribution and emit
        gate: none, not addition_quality, matching GateInfo.from_scan_
        report's identical raw-code special case."""
        from abicheck.buildsource.risk import RiskScore
        from abicheck.scan_engine import ScanOutcome

        outcome = ScanOutcome(
            mode="ci",
            resolved_method="s3",
            depth="headers",
            collect_mode="target",
            risk=RiskScore(total=0),
            auto=False,
            changed_path_count=0,
            changed_path_source="none",
            verdict="COMPATIBLE",
            exit_code=1,
            diff_summary={"contract_coverage_exit_contribution": 1},
        )
        report = outcome.to_dict()
        assert report["run_outcome"]["gate"] == "none"
        assert report["run_outcome"]["operational"] == "none"
