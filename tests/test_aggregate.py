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

"""Unit tests for abicheck.aggregate — multi-target fan-in gate.

Grounded on the fan-out/fan-in acceptance table: a target that never produced
a report is *unavailable* (unknown), never folded into the verdict as an empty,
compatible ABI. Findings and coverage are graded as orthogonal conclusions.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.aggregate import (
    CoverageStatus,
    OnMissingRequired,
    aggregate_reports_dir,
    parse_report_verdict,
    target_id_from_path,
)
from abicheck.change_registry_types import Verdict

LINUX = "linux-x86_64"
WINDOWS = "windows-x86_64"
MACOS = "macos-arm64"


def _write_report(
    d: Path,
    target_id: str,
    verdict: str | None,
    *,
    prefix: str = "abi-report-",
    **extra,
) -> Path:
    payload: dict[str, object] = dict(extra)
    if verdict is not None:
        payload["verdict"] = verdict
    path = d / f"{prefix}{target_id}.json"
    path.write_text(json.dumps(payload))
    return path


class TestHelpers:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("abi-report-linux-x86_64.json", "linux-x86_64"),
            ("linux-x86_64.json", "linux-x86_64"),
            ("abi-report-windows-x86_64-cp312.json", "windows-x86_64-cp312"),
        ],
    )
    def test_target_id_from_path(self, name: str, expected: str):
        assert target_id_from_path(Path(name)) == expected

    def test_parse_report_verdict_reads_verdict(self):
        assert parse_report_verdict({"verdict": "BREAKING"}) is Verdict.BREAKING

    @pytest.mark.parametrize(
        "payload", [{}, {"verdict": None}, {"verdict": "NONSENSE"}]
    )
    def test_parse_report_verdict_none_when_absent_or_bad(self, payload: dict):
        assert parse_report_verdict(payload) is None


class TestAcceptanceTable:
    """One test per row of the fan-out/fan-in acceptance table."""

    def test_both_targets_succeed_clean(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        assert result.coverage is CoverageStatus.COMPLETE
        assert result.findings_verdict is Verdict.COMPATIBLE
        assert result.exit_code() == 0

    def test_one_target_regression_other_clean(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        assert result.coverage is CoverageStatus.COMPLETE
        assert result.findings_verdict is Verdict.BREAKING
        assert result.exit_code() == 4

    def test_clean_target_plus_missing_required_is_not_compatible(self, tmp_path: Path):
        # THE core case: the fragile heredoc would print "all compatible" and
        # exit 0. Here the missing required target is unavailable → gate fails.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        assert result.coverage is CoverageStatus.PARTIAL
        assert result.findings_verdict is Verdict.COMPATIBLE  # only over analyzed
        assert WINDOWS in {t.target_id for t in result.unavailable}
        assert result.exit_code() == 4  # coverage gate fails under default policy

    def test_regression_plus_missing_required_no_synthetic_break(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        # Windows contributes NO synthetic finding — only Linux's real break.
        assert {t.target_id for t in result.analyzed} == {LINUX}
        assert result.findings_verdict is Verdict.BREAKING

    def test_both_builds_missing_no_abi_verdict(self, tmp_path: Path):
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        assert result.coverage is CoverageStatus.EMPTY
        assert result.findings_verdict is None  # nothing analyzed → no verdict
        assert result.exit_code() == 4  # a gate cannot pass with zero evidence

    def test_source_break_maps_to_exit_2(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "API_BREAK")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        assert result.findings_verdict is Verdict.API_BREAK
        assert result.exit_code() == 2

    def test_unreadable_report_is_unavailable_not_a_break(self, tmp_path: Path):
        # A present-but-verdict-less report is unknown, not a silent pass and
        # not a synthetic break.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, None)  # no verdict key
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        windows = next(t for t in result.targets if t.target_id == WINDOWS)
        assert not windows.analyzed
        assert windows.reason is not None
        assert result.coverage is CoverageStatus.PARTIAL

    def test_corrupt_json_report_is_unavailable(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        (tmp_path / f"abi-report-{WINDOWS}.json").write_text("{ not valid json")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        windows = next(t for t in result.targets if t.target_id == WINDOWS)
        assert not windows.analyzed
        assert "unreadable" in (windows.reason or "")

    def test_non_object_json_report_is_unavailable(self, tmp_path: Path):
        # Valid JSON but not an object (e.g. a bare array) → unknown, not a pass.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        (tmp_path / f"abi-report-{WINDOWS}.json").write_text("[1, 2, 3]")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        windows = next(t for t in result.targets if t.target_id == WINDOWS)
        assert not windows.analyzed
        assert result.is_partial

    def test_rerun_producing_the_report_moves_partial_to_complete(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        partial = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])
        assert partial.coverage is CoverageStatus.PARTIAL

        _write_report(tmp_path, WINDOWS, "COMPATIBLE")  # rerun uploads its report
        complete = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])
        assert complete.coverage is CoverageStatus.COMPLETE

    def test_new_unbaselined_target_is_surfaced_separately(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "COMPATIBLE")  # not in expected set
        result = aggregate_reports_dir(tmp_path, required=[LINUX])

        assert result.coverage is CoverageStatus.COMPLETE  # expected set is fine
        assert {t.target_id for t in result.unbaselined} == {MACOS}
        assert MACOS in result.render_text()  # not silently swallowed


class TestCoveragePolicy:
    def test_missing_required_defaults_to_gate_failure(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        assert result.exit_code(on_missing_required=OnMissingRequired.FAIL) == 4

    def test_missing_required_warn_lets_findings_decide(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        # Clean findings + warn policy → exit 0 even though coverage is partial.
        assert result.exit_code(on_missing_required=OnMissingRequired.WARN) == 0
        assert result.coverage is CoverageStatus.PARTIAL  # still reported

    def test_missing_required_warn_still_fails_on_real_break(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        assert result.exit_code(on_missing_required=OnMissingRequired.WARN) == 4

    def test_missing_optional_target_never_fails_coverage(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, required=[LINUX], optional=[MACOS])

        assert result.coverage is CoverageStatus.COMPLETE
        assert result.exit_code() == 0

    def test_optional_only_with_nothing_analyzed_does_not_fail(self, tmp_path: Path):
        # Only an optional target was declared and none reported: there is no
        # required coverage gap, so the gate must not fail (exit 0) even under
        # the default fail policy — an unavailable optional target never gates.
        result = aggregate_reports_dir(tmp_path, optional=[MACOS])

        assert not result.required_gap
        assert result.coverage is CoverageStatus.COMPLETE
        assert result.findings_verdict is None
        assert result.exit_code() == 0

    def test_optional_without_expect_still_aggregates_present_reports(
        self, tmp_path: Path
    ):
        # `--optional macos` with no `--expect`: a present linux BREAKING report
        # must still be aggregated (no-expect = worst-of over what's present),
        # not shunted to unbaselined because only an optional id was named.
        _write_report(tmp_path, LINUX, "BREAKING")
        result = aggregate_reports_dir(tmp_path, optional=[MACOS])

        assert result.findings_verdict is Verdict.BREAKING
        assert LINUX in {t.target_id for t in result.analyzed}
        assert not result.unbaselined
        assert result.exit_code() == 4

    def test_no_expected_set_is_pure_worst_of(self, tmp_path: Path):
        # Backward-compatible with the old heredoc: aggregate whatever is
        # present, no coverage gate.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, "API_BREAK")
        result = aggregate_reports_dir(tmp_path)

        assert result.coverage is CoverageStatus.COMPLETE
        assert result.findings_verdict is Verdict.API_BREAK
        assert result.exit_code() == 2

    def test_findings_verdict_preserves_risk_over_compatible(self, tmp_path: Path):
        # One target COMPATIBLE, another COMPATIBLE_WITH_RISK. Both are exit-0,
        # but the reported findings_verdict must surface the risk, not collapse
        # to whichever verdict sorts first.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE_WITH_RISK")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])

        assert result.findings_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert result.exit_code() == 0  # risk is non-blocking for the gate
        assert result.to_dict()["findings_verdict"] == "COMPATIBLE_WITH_RISK"
        assert "compatible-with-risk on: windows-x86_64" in result.render_text()


class TestRendering:
    def test_to_dict_round_trips_key_fields(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE", library="libfoo.so")
        result = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS])
        d = result.to_dict()

        assert d["coverage"] == "partial"
        assert d["findings_verdict"] == "COMPATIBLE"
        ids = {t["target_id"]: t for t in d["targets"]}
        assert ids[LINUX]["analyzed"] is True
        assert ids[WINDOWS]["analyzed"] is False

    def test_render_text_partial_names_unknown_target(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        text = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS]).render_text()

        assert "Partial" in text
        assert "unavailable" in text
        assert WINDOWS in text

    def test_render_text_empty_when_nothing_analyzed(self, tmp_path: Path):
        text = aggregate_reports_dir(tmp_path, required=[LINUX]).render_text()

        assert "No coverage" in text
        assert "No targets were analyzed" in text

    def test_render_text_names_regressed_targets(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        text = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS]).render_text()

        assert "BREAKING on:" in text
        assert LINUX in text

    def test_render_text_groups_mixed_regressions_by_verdict(self, tmp_path: Path):
        # linux BREAKING, windows API_BREAK: each must be listed under its own
        # verdict — the API_BREAK target must not be mislabeled BREAKING.
        _write_report(tmp_path, LINUX, "BREAKING")
        _write_report(tmp_path, WINDOWS, "API_BREAK")
        text = aggregate_reports_dir(tmp_path, required=[LINUX, WINDOWS]).render_text()

        assert f"BREAKING on: {LINUX}." in text
        assert f"API_BREAK on: {WINDOWS}." in text


class TestReportPrefix:
    def test_custom_prefix(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE", prefix="report_")
        result = aggregate_reports_dir(tmp_path, required=[LINUX], prefix="report_")
        assert result.coverage is CoverageStatus.COMPLETE

    def test_bare_filename_no_prefix(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE", prefix="")
        result = aggregate_reports_dir(tmp_path, required=[LINUX])
        assert result.coverage is CoverageStatus.COMPLETE


class TestAggregateCLI:
    """End-to-end through the actual `abicheck aggregate` command."""

    def test_missing_required_target_exits_4(self, tmp_path: Path):
        from abicheck.cli import main

        _write_report(tmp_path, LINUX, "COMPATIBLE")
        res = CliRunner().invoke(
            main, ["aggregate", "--expect", f"{LINUX},{WINDOWS}", str(tmp_path)]
        )
        assert res.exit_code == 4
        assert "Partial" in res.output
        assert WINDOWS in res.output

    def test_all_clean_exits_0(self, tmp_path: Path):
        from abicheck.cli import main

        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        res = CliRunner().invoke(
            main, ["aggregate", "--expect", f"{LINUX},{WINDOWS}", str(tmp_path)]
        )
        assert res.exit_code == 0

    def test_warn_policy_exits_0_on_missing_required(self, tmp_path: Path):
        from abicheck.cli import main

        _write_report(tmp_path, LINUX, "COMPATIBLE")
        res = CliRunner().invoke(
            main,
            [
                "aggregate",
                "--expect",
                f"{LINUX},{WINDOWS}",
                "--on-missing-required",
                "warn",
                str(tmp_path),
            ],
        )
        assert res.exit_code == 0
        assert "Incomplete" in res.output

    def test_json_format_is_valid(self, tmp_path: Path):
        from abicheck.cli import main

        _write_report(tmp_path, LINUX, "BREAKING")
        res = CliRunner().invoke(
            main, ["aggregate", "--expect", LINUX, "--format", "json", str(tmp_path)]
        )
        assert res.exit_code == 4
        payload = json.loads(res.output)
        assert payload["findings_verdict"] == "BREAKING"

    def test_output_to_file(self, tmp_path: Path):
        from abicheck.cli import main

        reports = tmp_path / "reports"
        reports.mkdir()
        _write_report(reports, LINUX, "COMPATIBLE")
        out = tmp_path / "result.json"
        res = CliRunner().invoke(
            main,
            [
                "aggregate",
                "--expect",
                LINUX,
                "--format",
                "json",
                "-o",
                str(out),
                str(reports),
            ],
        )
        assert res.exit_code == 0
        assert json.loads(out.read_text())["coverage"] == "complete"
