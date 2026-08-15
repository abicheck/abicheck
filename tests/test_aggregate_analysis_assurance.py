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

"""P0.4's analysis-assurance axis (``--require-complete-analysis``) as a
gate ``abicheck aggregate`` folds -- the exact sibling of
``tests/test_aggregate.py``'s own ``TestContractCoverageAxis``, for the
other orthogonal exit-floor axis.

A sibling file, not a class inside ``test_aggregate.py``: that file is at
1973 lines, close enough to the AGENTS.md 2000-line hard cap that a full
mirrored test class would exceed it -- the same reason
``tests/test_scan_analysis_assurance.py`` split out of
``tests/test_analysis_assurance.py`` earlier in this same area. Carries its
own small copies of ``test_aggregate.py``'s helpers/constants rather than
importing them, matching this repo's own established convention for these
split sibling modules (see ``test_action_*``'s documented rationale in
``CLAUDE.md`` "M1-1").

Before this, ``cli_scan_baseline.py`` computed and folded the
``--require-complete-analysis`` exit floor into a scan's own exit code, but
never persisted the contribution into the report itself -- so a scan whose
compatibility/severity gate read a clean ``0`` while the analysis-assurance
axis independently floored the *real* exit to ``1`` fed ``aggregate`` a
green result for that target, since ``GateInfo.from_scan_report`` reads
only the nested compatibility gate (Codex review, PR #780).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.aggregate import ExpectedTargets, aggregate_reports_dir

LINUX = "linux-x86_64"


def _write_report(
    d: Path,
    target_id: str,
    verdict: str | None,
    *,
    prefix: str = "abi-report-",
    severity: dict | None = None,
    **extra,
) -> Path:
    payload: dict[str, object] = dict(extra)
    if verdict is not None:
        payload["verdict"] = verdict
    if severity is not None:
        payload["severity"] = severity
    path = d / f"{prefix}{target_id}.json"
    path.write_text(json.dumps(payload))
    return path


def _expect(*required: str) -> ExpectedTargets:
    return ExpectedTargets.from_lists(list(required), [])


class TestAnalysisAssuranceAxis:
    def test_an_incomplete_target_raises_a_clean_aggregate_to_one(
        self, tmp_path: Path
    ) -> None:
        _write_report(
            tmp_path, LINUX, "COMPATIBLE", analysis_assurance_exit_contribution=1
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.analysis_assurance_exit == 1
        assert result.analysis_assurance_targets == (LINUX,)
        assert result.exit_code() == 1
        assert not result.passed

    def test_it_never_lowers_a_real_break(self, tmp_path: Path) -> None:
        # `max`, not replacement -- the orthogonality rule this axis shares
        # with contract-coverage: neither may demote an ABI break to
        # "warnings only".
        _write_report(
            tmp_path,
            LINUX,
            "BREAKING",
            severity={"exit_code": 4, "blocking": True},
            analysis_assurance_exit_contribution=1,
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.analysis_assurance_exit == 1
        assert result.exit_code() == 4

    def test_a_report_without_the_key_contributes_nothing(self, tmp_path: Path) -> None:
        # Every pre-1.17-scan/pre-P0.4 report, and every run without
        # --require-complete-analysis: this is what keeps existing matrices
        # unchanged.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.analysis_assurance_exit == 0
        assert result.exit_code() == 0

    def test_the_axis_fires_even_when_the_severity_gate_reads_clean(
        self, tmp_path: Path
    ) -> None:
        # The exact scenario Codex's review reproduced: a demoted/absent
        # severity contribution (0) alongside an independent
        # analysis-assurance floor of 1 -- GateInfo.from_scan_report reads
        # only the nested compatibility gate, so this axis must be folded
        # separately for the aggregate to see the real failure at all.
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            severity={"exit_code": 0, "blocking": False},
            analysis_assurance_exit_contribution=1,
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.analysis_assurance_exit == 1
        assert result.exit_code() == 1

    def test_a_scan_report_carries_the_field_inside_its_diff_block(
        self, tmp_path: Path
    ) -> None:
        # `cli_scan_baseline._baseline_summary` writes the contribution into
        # the summary that becomes `ScanOutcome.to_dict()['diff']`, so a
        # scan report nests it one level down, same as
        # `contract_coverage_exit_contribution`.
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.17",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 1,
                    "diff": {"analysis_assurance_exit_contribution": 1},
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.analysis_assurance_exit == 1
        assert result.analysis_assurance_targets == (LINUX,)

    def test_a_target_report_pins_the_field_in_to_dict(self, tmp_path: Path) -> None:
        _write_report(
            tmp_path, LINUX, "COMPATIBLE", analysis_assurance_exit_contribution=1
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        payload = result.to_dict()
        assert payload["targets"][0]["analysis_assurance_exit"] == 1

    def test_the_top_level_summary_block_names_the_incomplete_target(
        self, tmp_path: Path
    ) -> None:
        # Mirrors the pre-existing "contract_coverage" top-level block
        # exactly, for the sibling axis.
        _write_report(
            tmp_path, LINUX, "COMPATIBLE", analysis_assurance_exit_contribution=1
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        payload = result.to_dict()
        assert payload["analysis_assurance"] == {
            "exit_contribution": 1,
            "incomplete_targets": [LINUX],
        }
        text = result.render_text()
        assert "Analysis assurance:" in text
        assert f"incomplete on {LINUX}" in text

    def test_a_clean_target_is_not_listed_or_rendered(self, tmp_path: Path) -> None:
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.to_dict()["analysis_assurance"] == {
            "exit_contribution": 0,
            "incomplete_targets": [],
        }
        assert "Analysis assurance:" not in result.render_text()

    def test_reported_separately_from_contract_coverage(self, tmp_path: Path) -> None:
        # Both axes contribute 1 and answer different questions -- neither
        # reader may collapse into the other.
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            contract_coverage_exit_contribution=1,
            analysis_assurance_exit_contribution=1,
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 1
        assert result.analysis_assurance_exit == 1
        assert result.exit_code() == 1

    def test_an_unavailable_target_contributes_nothing(self, tmp_path: Path) -> None:
        # A missing report is already a coverage gap on this aggregate's own
        # axis; inventing an analysis-assurance floor for it would
        # double-count the same absence (mirrors the contract-coverage
        # axis's identical rule).
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.analysis_assurance_exit == 0

    @pytest.mark.parametrize(  # noqa: PT006 - single unnamed param, matches sibling
        "bad",
        ["1", True, -1, None, {"exit": 1}, 2, 4],
        ids=["str", "bool", "negative", "null", "object", "two", "four"],
    )
    def test_a_malformed_contribution_fails_open(self, tmp_path: Path, bad) -> None:
        _write_report(
            tmp_path, LINUX, "COMPATIBLE", analysis_assurance_exit_contribution=bad
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.analysis_assurance_exit == 0
        assert result.exit_code() == 0
