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

    def test_scan_outcome_assurance_only_exit_1_reads_gate_none_end_to_end(self):
        """Codex review (P2), end-to-end through the real writer: a legacy-
        scheme `scan --against --require-complete-analysis` whose own
        compatibility is clean but whose assurance is incomplete folds to a
        top-level exit_code of 1 the identical way an incomplete contract
        coverage does -- the writer must read the report's own declared
        `analysis_assurance_exit_contribution` and emit gate: none, not
        addition_quality."""
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
            diff_summary={"analysis_assurance_exit_contribution": 1},
        )
        report = outcome.to_dict()
        assert report["run_outcome"]["gate"] == "none"
        assert report["run_outcome"]["operational"] == "none"

    def test_abort_report_rejects_an_out_of_scheme_compatibility_contribution(self):
        """Codex review, fresh evidence: a legacy scan may carry a valid
        root `exit_code` (4) alongside an invalid, out-of-scheme nested
        `exit.compatibility_contribution` (99, outside {0, 1, 2, 4}) --
        accepting it unchecked normalized straight to `gate: none`,
        silently turning a real BREAKING scan nonblocking. It must instead
        fall back to the root verdict/exit code, same as a missing value."""
        from abicheck.policy.outcome import run_outcome_dict_for_scan

        report = {
            "scan_schema_version": "1.24",
            "exit": {"code": 4, "compatibility_contribution": 99},
        }
        outcome = run_outcome_dict_for_scan("BREAKING", 4, report=report)
        assert outcome["gate"] == "abi_breaking"
