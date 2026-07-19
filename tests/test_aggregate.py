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

Grounded on the fan-out/fan-in review contract: an expected target with no
report is *unavailable* (unknown), never compatible; compatibility, gate, and
coverage are three orthogonal axes (ADR-042); a coverage gap is exit 1, never
an ABI-break exit 4.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.aggregate import (
    AggregateError,
    CoverageStatus,
    ExpectedTargets,
    OnMissingRequired,
    OnUnexpectedTarget,
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


def _expect(*required: str, optional: tuple[str, ...] = ()) -> ExpectedTargets:
    return ExpectedTargets.from_lists(list(required), list(optional))


class TestHelpers:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("abi-report-linux-x86_64.json", "linux-x86_64"),
            ("linux-x86_64.json", "linux-x86_64"),
        ],
    )
    def test_target_id_from_path(self, name: str, expected: str):
        assert target_id_from_path(Path(name)) == expected

    def test_parse_report_verdict(self):
        assert parse_report_verdict({"verdict": "BREAKING"}) is Verdict.BREAKING
        assert parse_report_verdict({}) is None
        assert parse_report_verdict({"verdict": "NONSENSE"}) is None


class TestAcceptanceTable:
    """The fan-out/fan-in acceptance scenarios."""

    def test_both_clean(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS))
        assert r.coverage is CoverageStatus.COMPLETE
        assert r.passed
        assert r.exit_code() == 0

    def test_real_abi_break_exits_4(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS))
        assert r.exit_code() == 4
        assert r.compatibility_verdict is Verdict.BREAKING

    def test_source_break_exits_2(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "API_BREAK")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.exit_code() == 2

    def test_missing_required_is_unavailable_and_exits_1_not_4(self, tmp_path: Path):
        # THE core case: a clean linux report + a missing required windows must
        # NOT pass green, and the coverage gap must be exit 1 (a build that
        # never ran), never an ABI-break exit 4.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS))
        assert r.coverage is CoverageStatus.PARTIAL
        assert WINDOWS in r.missing_required
        assert r.exit_code() == 1
        assert not r.passed

    def test_full_outage_missing_dir_is_coverage_not_usage_error(self, tmp_path: Path):
        r = aggregate_reports_dir(tmp_path / "nope", expected=_expect(LINUX, WINDOWS))
        assert r.coverage is CoverageStatus.EMPTY
        assert r.compatibility_verdict is None
        assert r.exit_code() == 1

    def test_regression_plus_missing_no_synthetic_break(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS))
        assert {t.target_id for t in r.analyzed} == {LINUX}
        assert r.exit_code() == 4  # linux's real break, not a windows synthetic one

    def test_unreadable_and_verdictless_reports_are_unavailable(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, None)  # no verdict key
        (tmp_path / f"abi-report-{WINDOWS}.json").write_text("{ bad json")
        (tmp_path / f"abi-report-{MACOS}.json").write_text("[1,2,3]")  # not an object
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS, MACOS))
        assert not any(t.analyzed for t in r.targets)
        assert r.coverage is CoverageStatus.EMPTY


class TestGateVsVerdict:
    """ADR-042: the gate is each report's own decision, never the verdict."""

    def test_compatible_but_policy_blocked_addition_fails(self, tmp_path: Path):
        # verdict COMPATIBLE, but the report's own gate says blocking (exit 1).
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            severity={
                "exit_code": 1,
                "blocking": True,
                "blocking_categories": ["addition"],
            },
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.compatibility_verdict is Verdict.COMPATIBLE  # honest compat axis
        assert not r.passed  # but the gate fails
        assert r.exit_code() == 1
        assert LINUX in r.blocking_targets

    def test_breaking_but_demoted_gate_passes(self, tmp_path: Path):
        # verdict BREAKING, but the report's gate was demoted to non-blocking.
        _write_report(
            tmp_path, LINUX, "BREAKING", severity={"exit_code": 0, "blocking": False}
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.compatibility_verdict is Verdict.BREAKING  # honest compat axis
        assert r.passed  # gate honours the report's own decision
        assert r.exit_code() == 0

    def test_legacy_report_without_gate_block_falls_back_to_verdict(
        self, tmp_path: Path
    ):
        _write_report(tmp_path, LINUX, "BREAKING")  # no severity block
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.exit_code() == 4
        target = r.targets[0]
        assert target.gate is not None and target.gate.from_report is False

    @pytest.mark.parametrize(
        "bad_severity",
        [
            {"exit_code": "1"},  # exit_code is a string, not an int
            {"exit_code": True},  # bool is not a valid exit code
            {"exit_code": 3},  # not one of {0,1,2,4}
            {"exit_code": 1, "blocking": "yes"},  # blocking is not a boolean
            {"exit_code": 0, "blocking": True},  # blocking contradicts exit_code
            {"exit_code": 4, "blocking": False},  # blocking contradicts exit_code
            {"exit_code": 1, "blocking_categories": "addition"},  # not a list
            {"exit_code": 1, "blocking_categories": [1, 2]},  # not strings
            {"blocking": True},  # exit_code missing entirely
            "not-an-object",  # severity is not even a dict
        ],
    )
    def test_malformed_gate_block_fails_closed(self, tmp_path: Path, bad_severity):
        # A *present but corrupt* gate block must NOT silently revert to the
        # (possibly greener) legacy verdict path — the target becomes
        # unavailable (unknown), so a required target failing this way is a
        # coverage gap, never a green pass.
        _write_report(tmp_path, LINUX, "COMPATIBLE", severity=bad_severity)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert not r.targets[0].analyzed
        assert "malformed" in (r.targets[0].reason or "")
        assert r.exit_code() == 1  # required coverage gap, not a green 0

    def test_absent_gate_block_still_legacy_falls_back(self, tmp_path: Path):
        # The distinction: NO severity block at all is an old/policy-less
        # report and legacy-falls-back to the verdict mapping.
        _write_report(tmp_path, LINUX, "BREAKING")  # no severity key
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].gate is not None
        assert r.targets[0].gate.from_report is False
        assert r.exit_code() == 4

    def test_scan_report_top_level_exit_code_is_the_gate(self, tmp_path: Path):
        # A scan report records its gate as a top-level exit_code, not a
        # severity block; keyed on scan_schema_version.
        (tmp_path / "abi-report-linux-x86_64.json").write_text(
            json.dumps(
                {"verdict": "API_BREAK", "scan_schema_version": "1.0", "exit_code": 2}
            )
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].gate is not None
        assert r.targets[0].gate.from_report is True
        assert r.exit_code() == 2

    def test_scan_report_with_non_int_exit_code_fails_closed(self, tmp_path: Path):
        (tmp_path / "abi-report-linux-x86_64.json").write_text(
            json.dumps(
                {
                    "verdict": "COMPATIBLE",
                    "scan_schema_version": "1.0",
                    "exit_code": "2",
                }
            )
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert not r.targets[0].analyzed
        assert r.exit_code() == 1  # required coverage gap, not a green pass

    def test_scan_budget_overflow_folds_to_exit_1(self, tmp_path: Path):
        # scan's exit 5 (budget overflow) is a non-ABI scan failure — it still
        # blocks, but folds to exit 1, never a fake ABI-break 4.
        (tmp_path / "abi-report-linux-x86_64.json").write_text(
            json.dumps(
                {"verdict": "COMPATIBLE", "scan_schema_version": "1.0", "exit_code": 5}
            )
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.exit_code() == 1


class TestCoveragePolicy:
    def test_missing_required_warn_lets_gate_decide(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX, WINDOWS),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert r.coverage is CoverageStatus.PARTIAL
        assert not r.coverage_blocking
        assert r.exit_code() == 0  # clean findings + warn → pass

    def test_missing_optional_never_fails(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, optional=(MACOS,)))
        assert r.coverage is CoverageStatus.COMPLETE
        assert r.exit_code() == 0


class TestUnexpectedTargets:
    def test_include_gates_unexpected_findings(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "BREAKING")  # not expected
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_unexpected_target=OnUnexpectedTarget.INCLUDE,
        )
        assert r.coverage is CoverageStatus.COMPLETE  # expected set is fine
        assert MACOS in {t.target_id for t in r.unexpected_targets}
        assert r.exit_code() == 4  # macos's break IS gated under include

    def test_warn_does_not_gate_unexpected(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "BREAKING")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_unexpected_target=OnUnexpectedTarget.WARN,
        )
        assert r.exit_code() == 0
        assert MACOS in {t.target_id for t in r.unexpected_targets}

    def test_ignore_drops_unexpected(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "BREAKING")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_unexpected_target=OnUnexpectedTarget.IGNORE,
        )
        assert r.unexpected_targets == ()
        assert r.exit_code() == 0

    def test_fail_on_any_unexpected(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "COMPATIBLE")  # clean, but unexpected
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_unexpected_target=OnUnexpectedTarget.FAIL,
        )
        assert r.exit_code() == 1

    def test_fail_on_unreadable_unexpected(self, tmp_path: Path):
        # An unexpected report that never parsed to a verdict (unreadable /
        # verdictless) is still a target outside the expected set — under
        # `fail`, its mere presence fails the gate even though it has no gate
        # of its own to contribute an exit code.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        (tmp_path / "abi-report-macos-arm64.json").write_text("{ not json")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_unexpected_target=OnUnexpectedTarget.FAIL,
        )
        assert MACOS in {t.target_id for t in r.unexpected_targets}
        assert not any(t.analyzed for t in r.unexpected_targets)
        assert r.exit_code() == 1

    def test_included_unexpected_break_shows_in_compatibility(self, tmp_path: Path):
        # An unexpected BREAKING report that drives the exit code under
        # `include` must not be hidden behind a "compatible" compatibility
        # summary — the compat axis has to see gated unexpected targets too.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "BREAKING")  # not expected
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_unexpected_target=OnUnexpectedTarget.INCLUDE,
        )
        assert r.exit_code() == 4
        assert r.compatibility_verdict is Verdict.BREAKING
        assert r.to_dict()["compatibility"]["verdict"] == "BREAKING"
        assert "No ABI regressions" not in r.render_text()
        assert MACOS in r.render_text()


class TestDiscoveredOnly:
    def test_discovered_only_aggregates_present_no_coverage_gate(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "API_BREAK")
        r = aggregate_reports_dir(tmp_path, discovered_only=True)
        assert r.discovered_only
        assert r.exit_code() == 2  # worst-of over present reports
        assert r.coverage is CoverageStatus.COMPLETE  # no required set to gate

    def test_no_expected_and_not_discovered_is_an_error(self, tmp_path: Path):
        with pytest.raises(AggregateError):
            aggregate_reports_dir(tmp_path)


class TestManifestAndIdentity:
    def test_manifest_round_trip(self, tmp_path: Path):
        data = {
            "targets": [
                {"id": LINUX, "required": True},
                {"id": MACOS, "required": False},
            ]
        }
        (tmp_path / "m.json").write_text(json.dumps(data))
        exp = ExpectedTargets.from_manifest_file(tmp_path / "m.json")
        assert exp.targets == {LINUX: True, MACOS: False}

    @pytest.mark.parametrize(
        "bad",
        [
            {"targets": []},
            {"targets": [{"id": ""}]},
            {"targets": [{"id": 123}]},
            {"targets": [{"id": LINUX, "required": "yes"}]},
            {"targets": [{"id": LINUX}, {"id": LINUX}]},
            {"targets": [42]},  # non-object entry
            {"nope": 1},
            [1, 2],
        ],
    )
    def test_manifest_rejects_malformed(self, bad):
        with pytest.raises(AggregateError):
            ExpectedTargets.from_manifest_data(bad)

    def test_manifest_file_unreadable_is_error(self, tmp_path: Path):
        (tmp_path / "m.json").write_text("{ not json")
        with pytest.raises(AggregateError):
            ExpectedTargets.from_manifest_file(tmp_path / "m.json")

    def test_from_lists_empty_is_error(self):
        with pytest.raises(AggregateError):
            ExpectedTargets.from_lists([], [])

    def test_report_self_identifies_target_id(self, tmp_path: Path):
        # Filename derives "renamed", but the report's own target_id wins.
        (tmp_path / "abi-report-renamed.json").write_text(
            json.dumps({"verdict": "COMPATIBLE", "target_id": LINUX})
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].analyzed
        assert r.exit_code() == 0

    def test_duplicate_target_id_is_hard_error(self, tmp_path: Path):
        _write_report(tmp_path, "linux", "COMPATIBLE", prefix="abi-report-")
        _write_report(tmp_path, "linux", "BREAKING", prefix="")  # both -> "linux"
        with pytest.raises(AggregateError):
            aggregate_reports_dir(tmp_path, expected=_expect("linux"))

    def test_stale_head_sha_report_is_unavailable(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE", head_sha="oldsha")
        manifest = ExpectedTargets(targets={LINUX: True}, head_sha="newsha")
        r = aggregate_reports_dir(tmp_path, expected=manifest)
        assert not r.targets[0].analyzed
        assert "different commit" in (r.targets[0].reason or "")

    def test_missing_head_sha_under_pinned_manifest_is_unverifiable(
        self, tmp_path: Path
    ):
        # Manifest pins a commit but the report carries no head_sha: identity is
        # unverifiable (a delayed artifact from an older run without metadata),
        # so the target is unavailable — fail closed, not accepted as current.
        _write_report(tmp_path, LINUX, "COMPATIBLE")  # no head_sha
        manifest = ExpectedTargets(targets={LINUX: True}, head_sha="newsha")
        r = aggregate_reports_dir(tmp_path, expected=manifest)
        assert not r.targets[0].analyzed
        assert "no head_sha" in (r.targets[0].reason or "")
        assert r.exit_code() == 1  # required coverage gap

    def test_matching_head_sha_is_current(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE", head_sha="thesha")
        manifest = ExpectedTargets(targets={LINUX: True}, head_sha="thesha")
        r = aggregate_reports_dir(tmp_path, expected=manifest)
        assert r.targets[0].analyzed
        assert r.exit_code() == 0

    def test_no_pinned_sha_accepts_report_without_head_sha(self, tmp_path: Path):
        # When the manifest does NOT pin a commit, a report without head_sha is
        # accepted as usual — the identity guard is opt-in.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].analyzed


class TestRendering:
    def test_json_schema_shape(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        d = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS)).to_dict()
        assert d["aggregate_schema_version"] == "1.0"
        assert d["status"] == "fail"
        assert d["gate"]["exit_code"] == 1
        assert d["gate"]["coverage_blocking"] is True
        assert d["coverage"]["missing_required_targets"] == [WINDOWS]
        assert d["coverage"]["required_targets"] == 2
        assert d["compatibility"]["analyzed_targets"] == 1

    def test_json_includes_unexpected_targets(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "COMPATIBLE")  # unexpected
        d = aggregate_reports_dir(tmp_path, expected=_expect(LINUX)).to_dict()
        assert d["unexpected_targets"]
        assert d["unexpected_targets"][0]["unexpected"] is True

    def test_unavailable_property_names_missing_targets(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS))
        assert {t.target_id for t in r.unavailable} == {WINDOWS}

    def test_json_distinguishes_fail_and_warn(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        fail = aggregate_reports_dir(
            tmp_path, expected=_expect(LINUX, WINDOWS)
        ).to_dict()
        warn = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX, WINDOWS),
            on_missing_required=OnMissingRequired.WARN,
        ).to_dict()
        assert fail["gate"]["exit_code"] != warn["gate"]["exit_code"]

    def test_text_groups_mixed_regressions_by_verdict(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        _write_report(tmp_path, WINDOWS, "API_BREAK")
        text = aggregate_reports_dir(
            tmp_path, expected=_expect(LINUX, WINDOWS)
        ).render_text()
        assert f"BREAKING on: {LINUX}" in text
        assert f"API_BREAK on: {WINDOWS}" in text

    def test_text_preserves_risk(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE_WITH_RISK")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS))
        assert r.compatibility_verdict is Verdict.COMPATIBLE_WITH_RISK
        assert "compatible-with-risk on: windows-x86_64" in r.render_text()

    def test_text_full_outage(self, tmp_path: Path):
        text = aggregate_reports_dir(tmp_path, expected=_expect(LINUX)).render_text()
        assert "no coverage" in text
        assert "no compatibility verdict" in text

    def test_text_api_break_only(self, tmp_path: Path):
        # Only API_BREAK present (no BREAKING) — the render must name it under
        # API_BREAK without an empty BREAKING line.
        _write_report(tmp_path, LINUX, "API_BREAK")
        text = aggregate_reports_dir(tmp_path, expected=_expect(LINUX)).render_text()
        assert f"API_BREAK on: {LINUX}." in text
        assert "BREAKING on:" not in text


class TestAggregateCLI:
    def _run(self, args):
        from abicheck.cli import main

        return CliRunner().invoke(main, ["aggregate", *args])

    def test_missing_required_exits_1(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        res = self._run(["--expect", f"{LINUX},{WINDOWS}", str(tmp_path)])
        assert res.exit_code == 1
        assert "Failed" in res.output

    def test_all_clean_exits_0(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        res = self._run(["--expect", f"{LINUX},{WINDOWS}", str(tmp_path)])
        assert res.exit_code == 0

    def test_abi_break_exits_4(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        res = self._run(["--expect", LINUX, "--format", "json", str(tmp_path)])
        assert res.exit_code == 4
        assert json.loads(res.output)["compatibility"]["verdict"] == "BREAKING"

    def test_no_expected_set_is_usage_error(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        res = self._run([str(tmp_path)])
        assert res.exit_code == 64

    def test_discovered_only_flag(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        res = self._run(["--discovered-only", str(tmp_path)])
        assert res.exit_code == 0

    def test_manifest_flag(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        (tmp_path / "m.json").write_text(
            json.dumps({"targets": [{"id": LINUX, "required": True}]})
        )
        res = self._run(["--manifest", str(tmp_path / "m.json"), str(tmp_path)])
        assert res.exit_code == 0

    def test_conflicting_sources_is_usage_error(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        (tmp_path / "m.json").write_text(
            json.dumps({"targets": [{"id": LINUX, "required": True}]})
        )
        res = self._run(
            ["--manifest", str(tmp_path / "m.json"), "--expect", LINUX, str(tmp_path)]
        )
        assert res.exit_code == 64

    def test_discovered_only_conflicts_with_expect(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        res = self._run(["--discovered-only", "--expect", LINUX, str(tmp_path)])
        assert res.exit_code == 64

    def test_duplicate_target_id_usage_error(self, tmp_path: Path):
        _write_report(tmp_path, "linux", "COMPATIBLE", prefix="abi-report-")
        _write_report(tmp_path, "linux", "BREAKING", prefix="")
        res = self._run(["--expect", "linux", str(tmp_path)])
        assert res.exit_code == 64

    def test_malformed_manifest_is_usage_error(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        (tmp_path / "m.json").write_text(json.dumps({"targets": []}))
        res = self._run(["--manifest", str(tmp_path / "m.json"), str(tmp_path)])
        assert res.exit_code == 64

    def test_output_to_file(self, tmp_path: Path):
        reports = tmp_path / "r"
        reports.mkdir()
        _write_report(reports, LINUX, "COMPATIBLE")
        out = tmp_path / "out.json"
        res = self._run(
            ["--expect", LINUX, "--format", "json", "-o", str(out), str(reports)]
        )
        assert res.exit_code == 0
        assert json.loads(out.read_text())["status"] == "pass"
