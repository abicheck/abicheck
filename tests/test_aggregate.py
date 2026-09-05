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
    AGGREGATE_SCHEMA_VERSION,
    AggregateError,
    CoverageStatus,
    ExpectedTargets,
    OnMissingRequired,
    OnUnexpectedTarget,
    aggregate_reports_dir,
    parse_check_id,
    parse_report_verdict,
    target_id_from_path,
)
from abicheck.change_registry_types import Verdict

try:
    import jsonschema
except ImportError:  # pragma: no cover - exercised only when jsonschema absent
    jsonschema = None

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


def _write_not_comparable_report(
    d: Path,
    target_id: str,
    *,
    kind: str = "scope_mismatch",
    message: str = "scope drift",
    prefix: str = "abi-report-",
) -> Path:
    """A native compare/compare-release ADR-050 D2 not_comparable report:
    a real JSON ``null`` for ``verdict`` (not merely an absent key) plus a
    structured ``reason``. ``_write_report`` can't express this shape --
    its own ``verdict`` param omits the key entirely when given ``None``."""
    path = d / f"{prefix}{target_id}.json"
    path.write_text(
        json.dumps(
            {
                "report_schema_version": "2.17",
                "library": "libfoo.so",
                "verdict": None,
                "reason": {"kind": kind, "message": message},
            }
        )
    )
    return path


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

    def test_operational_error_verdict_blocks_at_4(self, tmp_path: Path):
        # A compare-release operational failure (top-level verdict "ERROR", not
        # a Verdict enum member) must stay a blocking exit-4 gate, not decay to
        # a verdictless/unavailable coverage gap.
        _write_report(tmp_path, LINUX, "ERROR")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].analyzed
        assert r.exit_code() == 4
        assert LINUX in r.blocking_targets
        assert r.targets[0].gate is not None
        assert r.targets[0].gate.blocking_categories == ("operational_error",)

    def test_operational_error_blocks_even_under_warn(self, tmp_path: Path):
        # Under --on-missing-required warn a coverage gap is advisory, but an
        # operational ERROR is a real per-report failure and must still block.
        _write_report(tmp_path, LINUX, "ERROR")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX, WINDOWS),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert r.exit_code() == 4

    def test_operational_error_on_optional_target_still_blocks(self, tmp_path: Path):
        # Even an *optional* target that operationally errored is a hard failure
        # (it ran and failed), unlike an optional target that simply never ran.
        _write_report(tmp_path, MACOS, "ERROR")
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, optional=(MACOS,)))
        assert r.exit_code() == 4


class TestNotComparable:
    """ADR-050 D2: a native compare/compare-release ``verdict: null`` report
    (schema 2.17) must never be silently folded into the same "unavailable"
    (coverage-gap-only, skippable) bucket a report that simply never arrived
    gets — it is definitive evidence the comparison couldn't be trusted, not
    an unknown, so it blocks unconditionally (CLAUDE.md M1-... / G32 Phase A)."""

    def test_blocks_at_4_and_is_marked_analyzed(self, tmp_path: Path):
        _write_not_comparable_report(tmp_path, LINUX)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert r.targets[0].analyzed
        assert r.targets[0].compatibility_verdict == Verdict.BREAKING
        assert r.exit_code() == 4
        assert LINUX in r.blocking_targets
        assert r.targets[0].gate is not None
        assert r.targets[0].gate.blocking_categories == ("not_comparable",)
        assert r.targets[0].reason is not None
        assert "scope_mismatch" in r.targets[0].reason
        assert "scope drift" in r.targets[0].reason

    def test_blocks_even_under_warn(self, tmp_path: Path):
        _write_not_comparable_report(tmp_path, LINUX)
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX, WINDOWS),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert r.exit_code() == 4

    def test_blocks_on_optional_target(self, tmp_path: Path):
        _write_not_comparable_report(tmp_path, MACOS)
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, optional=(MACOS,)))
        assert r.exit_code() == 4

    def test_blocks_unconditionally_in_discovered_only_mode(self, tmp_path: Path):
        # discovered_only has no coverage axis at all (there is no expected
        # set to detect a gap against) -- a not_comparable report must still
        # fold into exit_code() here, since it is not a coverage concern.
        _write_not_comparable_report(tmp_path, LINUX, kind="profile_mismatch")
        r = aggregate_reports_dir(tmp_path, discovered_only=True)
        assert r.discovered_only
        assert r.exit_code() == 4
        assert not r.passed

    def test_profile_mismatch_kind_is_preserved(self, tmp_path: Path):
        _write_not_comparable_report(
            tmp_path, LINUX, kind="profile_mismatch", message="dep.h changed"
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert "profile_mismatch" in r.targets[0].reason
        assert "dep.h changed" in r.targets[0].reason

    def test_distinct_from_a_missing_report(self, tmp_path: Path):
        # No report file at all for LINUX -- genuinely unavailable, still a
        # *coverage* gap (advisory under warn), unlike not_comparable above.
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert not r.targets[0].analyzed
        assert r.targets[0].gate is None
        assert r.exit_code() == 0

    def test_render_text_shows_reason(self, tmp_path: Path):
        _write_not_comparable_report(tmp_path, LINUX)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        text = r.render_text()
        assert "not_comparable" in text
        assert "scope drift" in text

    def test_to_dict_carries_reason_and_category(self, tmp_path: Path):
        _write_not_comparable_report(tmp_path, LINUX)
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        d = r.to_dict()
        target = d["targets"][0]
        assert target["compatibility_verdict"] == "BREAKING"
        assert target["gate"]["blocking_categories"] == ["not_comparable"]
        assert "reason" in target


class TestCheckTargetAdvisorySentinelReasons:
    """``NO_BASELINE``/``NEW_TARGET`` (``actions/check-target``'s bootstrap/
    allow-new-target advisory sentinels, neither a real ``Verdict`` member)
    both fall through to an unavailable, unanalyzed ``TargetReport`` -- same
    as any other verdictless report -- but each gets its own human-readable
    ``reason`` instead of the generic "report carried no ABI verdict", so a
    reader of the aggregate JSON/text can tell an intentionally-tolerated
    new-library first release (or a legitimate bootstrap pass) apart from a
    malformed/corrupt report (Codex review)."""

    def test_new_target_reason_is_distinct_from_generic(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "NEW_TARGET")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert not r.targets[0].analyzed
        assert r.targets[0].reason is not None
        assert "new_target" in r.targets[0].reason
        assert r.targets[0].reason != "report carried no ABI verdict"

    def test_bootstrap_reason_is_distinct_from_generic(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "NO_BASELINE")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert not r.targets[0].analyzed
        assert r.targets[0].reason is not None
        assert "bootstrap" in r.targets[0].reason
        assert r.targets[0].reason != "report carried no ABI verdict"

    def test_malformed_verdictless_report_keeps_generic_reason(self, tmp_path: Path):
        # Control case: an ordinary verdictless/malformed report (no
        # verdict key at all) must still get the pre-existing generic
        # reason -- this fix must not change that fallback.
        _write_report(tmp_path, LINUX, None)
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert r.targets[0].reason == "report carried no ABI verdict"

    def test_new_target_reason_reaches_render_text(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "NEW_TARGET")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert "new_target" in r.render_text()

    def test_new_target_reason_reaches_to_dict(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "NEW_TARGET")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_missing_required=OnMissingRequired.WARN,
        )
        target = r.to_dict()["targets"][0]
        assert "new_target" in target["reason"]


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

    def test_discovered_only_with_expected_is_an_error(self, tmp_path: Path):
        # The public helper must not silently drop the expected set (which
        # would disable its required-coverage/commit checks) — reject the
        # conflicting combination instead.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        with pytest.raises(AggregateError):
            aggregate_reports_dir(
                tmp_path, expected=_expect(LINUX, WINDOWS), discovered_only=True
            )


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
            {"targets": [{"id": LINUX}], "head_sha": 123},  # non-string head_sha
            {"targets": [{"id": LINUX}], "head_sha": ""},  # empty head_sha
            {"targets": [{"id": LINUX}], "aggregate_manifest_version": 1},  # not str
            {
                "targets": [{"id": LINUX}],
                "aggregate_manifest_version": "x.y",
            },  # not num
            {"targets": [{"id": LINUX}], "aggregate_manifest_version": "99.0"},  # newer
            # CodeRabbit review, fresh evidence: only the prefix before the
            # first "." was ever parsed, so a bare major ("2"), a
            # non-numeric minor ("2.x"), and a three-component version
            # ("2.0.1") all previously passed this check silently despite
            # the manifest contract requiring exactly "MAJOR.MINOR".
            {"targets": [{"id": LINUX}], "aggregate_manifest_version": "2"},
            {"targets": [{"id": LINUX}], "aggregate_manifest_version": "2.x"},
            {"targets": [{"id": LINUX}], "aggregate_manifest_version": "2.0.1"},
        ],
    )
    def test_manifest_rejects_malformed(self, bad):
        with pytest.raises(AggregateError):
            ExpectedTargets.from_manifest_data(bad)

    def test_manifest_accepts_current_version(self):
        exp = ExpectedTargets.from_manifest_data(
            {"aggregate_manifest_version": "1.0", "targets": [{"id": LINUX}]}
        )
        assert exp.targets == {LINUX: True}

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


class TestErrorReleaseReportFindings:
    """`compare-release` sets the top-level verdict to ERROR when one library
    fails, but `_format_release_json` still emits `bundle_findings`/
    `matrix_findings` for whatever did complete (Codex review). Dropping them
    lost real evidence from the profile most likely to differ."""

    _GCC = "libfoo@linux-gcc14#release@headers"
    _CLANG = "libfoo@linux-clang20#release@headers"
    _MATRIX_FINDING = {
        "kind": "build_config_changed",
        "symbol": "x",
        "description": "flag drift",
        "old_value": None,
        "new_value": None,
    }

    @staticmethod
    def _write(d, target_id: str, payload: dict) -> None:
        (d / f"abi-report-{target_id}.json").write_text(json.dumps(payload))

    def _expect_both(self):
        return ExpectedTargets.from_lists([self._GCC, self._CLANG], [])

    def test_findings_survive_an_operational_error(self, tmp_path):
        self._write(
            tmp_path,
            self._GCC,
            {"verdict": "ERROR", "matrix_findings": [dict(self._MATRIX_FINDING)]},
        )
        self._write(tmp_path, self._CLANG, {"verdict": "COMPATIBLE", "changes": []})
        r = aggregate_reports_dir(tmp_path, expected=self._expect_both())
        (entry,) = r.finding_matrix
        assert entry.kinds == ("build_config_changed",)
        assert entry.affected_profiles == ("linux-gcc14",)

    def test_an_errored_report_still_clears_nobody(self, tmp_path):
        """It can convict its own profile and nothing more: a run that failed
        cannot have accounted for everything."""
        self._write(tmp_path, self._GCC, {"verdict": "ERROR", "matrix_findings": []})
        self._write(
            tmp_path,
            self._CLANG,
            {
                "verdict": "BREAKING",
                "changes": [dict(self._MATRIX_FINDING, severity="error")],
            },
        )
        r = aggregate_reports_dir(tmp_path, expected=self._expect_both())
        (entry,) = r.finding_matrix
        assert entry.affected_profiles == ("linux-clang20",)
        assert entry.unaffected_profiles == ()
        assert entry.undetermined_profiles == ("linux-gcc14",)

    def test_the_operational_error_gate_is_unchanged(self, tmp_path):
        """Reading findings must not soften the blocking exit an ERROR report
        already forces."""
        self._write(
            tmp_path,
            self._GCC,
            {"verdict": "ERROR", "matrix_findings": [dict(self._MATRIX_FINDING)]},
        )
        r = aggregate_reports_dir(
            tmp_path, expected=ExpectedTargets.from_lists([self._GCC], [])
        )
        assert r.exit_code() == 4


class TestRendering:
    def test_schema_version_is_pinned(self):
        """The one place the version is written as a literal.

        Every other assertion compares against `AGGREGATE_SCHEMA_VERSION`,
        which makes them structural but vacuous as version checks — they
        compare the constant with itself. This pin is what makes a bump
        deliberate: it fails until someone updates it in the same change
        that updates `aggregate_report.schema.json`, its published mirror,
        and the docs (CodeRabbit review).
        """
        assert AGGREGATE_SCHEMA_VERSION == "1.8"

    def test_json_schema_shape(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        d = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS)).to_dict()
        assert d["aggregate_schema_version"] == AGGREGATE_SCHEMA_VERSION
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

    def test_text_pass_under_warn_does_not_claim_coverage_complete(
        self, tmp_path: Path
    ):
        # A required target is missing but --on-missing-required warn lets the
        # gate pass; the Gate line must NOT claim required coverage is complete.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX, WINDOWS),
            on_missing_required=OnMissingRequired.WARN,
        )
        assert r.passed
        text = r.render_text()
        assert "no gate-blocking findings" in text
        assert "required coverage complete" not in text
        assert "(advisory)" in text  # the coverage gap is still surfaced

    def test_text_fail_names_unexpected_targets(self, tmp_path: Path):
        # Under --on-unexpected-target fail, the failure reason names the
        # offending unexpected target(s), not just the exit code.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, MACOS, "COMPATIBLE")  # unexpected, but clean
        text = aggregate_reports_dir(
            tmp_path,
            expected=_expect(LINUX),
            on_unexpected_target=OnUnexpectedTarget.FAIL,
        ).render_text()
        assert f"unexpected target(s) present: {MACOS}" in text


class TestProfileMatrix:
    """Status-review item 5: affected_profiles / profile-level dedup —
    ``check_id``-shaped ``target_id``s (ADR-047 §7:
    ``target@profile#baseline_channel@requested_depth``) group back to one
    logical target across profiles instead of showing unrelated-looking
    target rows."""

    def test_parse_check_id_roundtrips(self) -> None:
        parsed = parse_check_id("libfoo@linux-gcc14#release@headers")
        assert parsed is not None
        assert parsed.target == "libfoo"
        assert parsed.profile == "linux-gcc14"
        assert parsed.baseline_channel == "release"
        assert parsed.requested_depth == "headers"

    def test_parse_check_id_rejects_non_check_id_shaped(self) -> None:
        assert parse_check_id("linux-x86_64") is None
        assert parse_check_id("abi-report-linux-x86_64") is None

    def test_target_report_profile_id_and_base_target(self, tmp_path: Path) -> None:
        _write_report(tmp_path, "libfoo@linux-gcc14#release@headers", "BREAKING")
        r = aggregate_reports_dir(
            tmp_path, expected=_expect("libfoo@linux-gcc14#release@headers")
        )
        (t,) = r.targets
        assert t.profile_id == "linux-gcc14"
        assert t.base_target == "libfoo"

    def test_bare_target_id_has_no_profile(self, tmp_path: Path) -> None:
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        (t,) = r.targets
        assert t.profile_id is None
        assert t.base_target == LINUX
        assert r.profile_matrix == ()

    def test_profile_matrix_groups_by_base_target(self, tmp_path: Path) -> None:
        gcc = "libfoo@linux-gcc14#release@headers"
        clang = "libfoo@linux-clang20#release@headers"
        msvc = "libfoo@windows-msvc#release@headers"
        _write_report(tmp_path, gcc, "BREAKING")
        _write_report(tmp_path, clang, "BREAKING")
        _write_report(tmp_path, msvc, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(gcc, clang, msvc))
        (entry,) = r.profile_matrix
        assert entry.base_target == "libfoo"
        assert entry.profiles == ("linux-clang20", "linux-gcc14", "windows-msvc")
        assert entry.affected_profiles == ("linux-clang20", "linux-gcc14")
        assert entry.verdict_by_profile == {
            "linux-gcc14": "BREAKING",
            "linux-clang20": "BREAKING",
            "windows-msvc": "COMPATIBLE",
        }

    def test_profile_matrix_marks_unavailable_profile_not_affected(
        self, tmp_path: Path
    ) -> None:
        gcc = "libfoo@linux-gcc14#release@headers"
        msvc = "libfoo@windows-msvc#release@headers"
        _write_report(tmp_path, gcc, "BREAKING")
        # msvc never reports — unavailable, not "affected".
        r = aggregate_reports_dir(tmp_path, expected=_expect(gcc, msvc))
        (entry,) = r.profile_matrix
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.verdict_by_profile["windows-msvc"] is None

    def test_profile_matrix_json_and_text(self, tmp_path: Path) -> None:
        gcc = "libfoo@linux-gcc14#release@headers"
        clang = "libfoo@linux-clang20#release@headers"
        _write_report(tmp_path, gcc, "BREAKING")
        _write_report(tmp_path, clang, "COMPATIBLE")
        r = aggregate_reports_dir(tmp_path, expected=_expect(gcc, clang))
        d = r.to_dict()
        assert d["profile_matrix"] == [
            {
                "base_target": "libfoo",
                "profiles": ["linux-clang20", "linux-gcc14"],
                "affected_profiles": ["linux-gcc14"],
                "incomplete_profiles": [],
                "unanalyzed_profiles": [],
                "contract_incomplete_profiles": [],
                **{k: [] for k in ("analysis_incomplete_profiles", "scope_incomplete_profiles")},
                "verdict_by_profile": {
                    "linux-clang20": "COMPATIBLE",
                    "linux-gcc14": "BREAKING",
                },
            }
        ]
        text = r.render_text()
        assert "Profile matrix:" in text
        assert "libfoo: affected on linux-gcc14" in text

    def test_profile_matrix_all_clean_renders_distinctly(self, tmp_path: Path) -> None:
        gcc = "libfoo@linux-gcc14#release@headers"
        clang = "libfoo@linux-clang20#release@headers"
        _write_report(tmp_path, gcc, "COMPATIBLE")
        _write_report(tmp_path, clang, "COMPATIBLE")
        text = aggregate_reports_dir(
            tmp_path, expected=_expect(gcc, clang)
        ).render_text()
        assert "libfoo: clean on all checked profiles" in text

    def test_profile_matrix_combines_multiple_checks_per_profile_worst_wins(
        self, tmp_path: Path
    ) -> None:
        """Codex review: the same (base_target, profile) pair can have more
        than one check differing only by baseline_channel/requested_depth —
        e.g. one profile checked at both `headers` and `build` depth. A
        later, cleaner check must not silently overwrite an earlier,
        breaking one for that same profile."""
        headers_check = "libfoo@linux-gcc14#release@headers"
        build_check = "libfoo@linux-gcc14#release@build"
        other_profile = "libfoo@windows-msvc#release@headers"
        _write_report(tmp_path, headers_check, "BREAKING")
        _write_report(tmp_path, build_check, "COMPATIBLE")  # would overwrite naively
        _write_report(tmp_path, other_profile, "COMPATIBLE")
        r = aggregate_reports_dir(
            tmp_path, expected=_expect(headers_check, build_check, other_profile)
        )
        (entry,) = r.profile_matrix
        assert entry.profiles == ("linux-gcc14", "windows-msvc")
        # linux-gcc14 must still be affected -- one of its two checks broke.
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.verdict_by_profile["linux-gcc14"] == "BREAKING"
        assert entry.verdict_by_profile["windows-msvc"] == "COMPATIBLE"

    def test_profile_matrix_surfaces_incomplete_coverage_not_silently_clean(
        self, tmp_path: Path
    ) -> None:
        """Codex review: one profile has a completed, compatible headers
        check plus a missing required build-depth check for the same
        target. The missing check must not be silently dropped -- the
        profile is incomplete, not flatly "clean"."""
        headers_check = "libfoo@linux-gcc14#release@headers"
        build_check = "libfoo@linux-gcc14#release@build"  # never reports
        _write_report(tmp_path, headers_check, "COMPATIBLE")
        r = aggregate_reports_dir(
            tmp_path, expected=_expect(headers_check, build_check)
        )
        (entry,) = r.profile_matrix
        assert entry.profiles == ("linux-gcc14",)
        assert entry.affected_profiles == ()  # the one completed check was clean
        assert entry.incomplete_profiles == ("linux-gcc14",)  # but coverage isn't
        assert entry.verdict_by_profile["linux-gcc14"] == "COMPATIBLE"
        text = r.render_text()
        assert "libfoo: clean on all checked profiles" in text
        assert "[incomplete coverage on linux-gcc14]" in text

    def test_profile_matrix_incomplete_profile_can_still_be_affected(
        self, tmp_path: Path
    ) -> None:
        headers_check = "libfoo@linux-gcc14#release@headers"
        build_check = "libfoo@linux-gcc14#release@build"  # never reports
        _write_report(tmp_path, headers_check, "BREAKING")
        r = aggregate_reports_dir(
            tmp_path, expected=_expect(headers_check, build_check)
        )
        (entry,) = r.profile_matrix
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.incomplete_profiles == ("linux-gcc14",)

    def test_profile_matrix_excludes_optional_gap_from_incomplete(
        self, tmp_path: Path
    ) -> None:
        """Codex review: an unavailable *optional* check must not mark its
        profile incomplete -- that would contradict
        AggregateResult.coverage, which also only gates on required
        targets, so a required-complete/optional-missing profile would
        otherwise read as both "coverage complete" and "incomplete"."""
        headers_check = "libfoo@linux-gcc14#release@headers"
        build_check = "libfoo@linux-gcc14#release@build"  # optional, never reports
        _write_report(tmp_path, headers_check, "COMPATIBLE")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(headers_check, optional=(build_check,)),
        )
        (entry,) = r.profile_matrix
        assert entry.incomplete_profiles == ()
        assert r.coverage is CoverageStatus.COMPLETE

    def test_profile_matrix_marks_policy_blocking_compatible_profile_as_affected(
        self, tmp_path: Path
    ) -> None:
        """Codex review: verdict COMPATIBLE with a policy-blocking gate
        (e.g. an addition: error policy) must not read as "clean" in the
        profile matrix -- the aggregate gate itself fails on it."""
        check = "libfoo@linux-gcc14#release@headers"
        _write_report(
            tmp_path,
            check,
            "COMPATIBLE",
            severity={
                "exit_code": 1,
                "blocking": True,
                "blocking_categories": ["addition"],
            },
        )
        r = aggregate_reports_dir(tmp_path, expected=_expect(check))
        (entry,) = r.profile_matrix
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.verdict_by_profile["linux-gcc14"] == "COMPATIBLE"

    def test_profile_matrix_never_calls_an_unanalyzed_optional_profile_clean(
        self, tmp_path: Path
    ) -> None:
        """Codex review: a profile with only unavailable *optional* checks
        has verdict_by_profile[pid] is None, and both affected_profiles and
        incomplete_profiles empty -- it must not be rendered/reported as
        "clean", since it was never actually analyzed."""
        analyzed = "libfoo@linux-gcc14#release@headers"
        never_reports = "libfoo@windows-msvc#release@headers"  # optional
        _write_report(tmp_path, analyzed, "COMPATIBLE")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(analyzed, optional=(never_reports,)),
        )
        (entry,) = r.profile_matrix
        assert entry.unanalyzed_profiles == ("windows-msvc",)
        assert entry.affected_profiles == ()
        assert entry.incomplete_profiles == ()  # optional gap, not a coverage failure
        assert entry.verdict_by_profile["windows-msvc"] is None
        text = r.render_text()
        assert "clean on linux-gcc14" in text
        assert "no analyzed result on windows-msvc" in text
        assert "clean on all checked profiles" not in text

    def test_profile_matrix_all_unanalyzed_never_says_clean(
        self, tmp_path: Path
    ) -> None:
        """Every profile for this target is unanalyzed (all optional,
        none ever reported) -- the whole entry must say so, not "clean"."""
        never_reports = "libfoo@windows-msvc#release@headers"
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(optional=(never_reports,)),
        )
        (entry,) = r.profile_matrix
        assert entry.unanalyzed_profiles == ("windows-msvc",)
        text = r.render_text()
        assert "no analyzed result on any checked profile" in text
        assert "clean" not in text.split("Profile matrix:")[1].split("Coverage:")[0]

    def test_profile_matrix_affected_entry_still_surfaces_unanalyzed_sibling(
        self, tmp_path: Path
    ) -> None:
        """Codex review: one profile is BREAKING (affected) while a sibling
        optional profile never reported at all (unanalyzed). The affected
        branch must still surface the unanalyzed one instead of implying
        "checked on" both profiles produced a result."""
        broken = "libfoo@linux-gcc14#release@headers"
        never_reports = "libfoo@windows-msvc#release@headers"  # optional
        _write_report(tmp_path, broken, "BREAKING")
        r = aggregate_reports_dir(
            tmp_path,
            expected=_expect(broken, optional=(never_reports,)),
        )
        (entry,) = r.profile_matrix
        assert entry.affected_profiles == ("linux-gcc14",)
        assert entry.unanalyzed_profiles == ("windows-msvc",)
        text = r.render_text()
        assert "libfoo: affected on linux-gcc14" in text
        assert "no analyzed result on windows-msvc" in text


class TestAggregateCLI:
    def _run(self, args):
        from abicheck.cli import main

        return CliRunner().invoke(main, ["aggregate", *args])

    def _manifest(self, tmp_path: Path, *ids: str, optional: tuple[str, ...] = ()):
        """Write an expected-target manifest.

        The ad-hoc ``--expect``/``--optional`` id lists these tests used to
        pass are gone: a manifest file is the one way to declare the target
        set, so the CLI cannot drift from the plan job that produced it.
        """
        path = tmp_path / "expected.json"
        path.write_text(
            json.dumps(
                {
                    "targets": [{"id": i, "required": True} for i in ids]
                    + [{"id": i, "required": False} for i in optional]
                }
            )
        )
        return path

    def test_missing_required_exits_1(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        res = self._run(
            ["--manifest", str(self._manifest(tmp_path, LINUX, WINDOWS)), str(tmp_path)]
        )
        assert res.exit_code == 1
        assert "Failed" in res.output

    def test_all_clean_exits_0(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        _write_report(tmp_path, WINDOWS, "COMPATIBLE")
        res = self._run(
            ["--manifest", str(self._manifest(tmp_path, LINUX, WINDOWS)), str(tmp_path)]
        )
        assert res.exit_code == 0

    def test_abi_break_exits_4(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "BREAKING")
        res = self._run(
            [
                "--manifest",
                str(self._manifest(tmp_path, LINUX)),
                "--format",
                "json",
                str(tmp_path),
            ]
        )
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
            [
                "--manifest",
                str(tmp_path / "m.json"),
                "--run-plan",
                str(tmp_path / "m.json"),
                str(tmp_path),
            ]
        )
        assert res.exit_code == 64

    def test_discovered_only_conflicts_with_manifest(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        res = self._run(
            [
                "--discovered-only",
                "--manifest",
                str(self._manifest(tmp_path, LINUX)),
                str(tmp_path),
            ]
        )
        assert res.exit_code == 64

    def test_duplicate_target_id_usage_error(self, tmp_path: Path):
        _write_report(tmp_path, "linux", "COMPATIBLE", prefix="abi-report-")
        _write_report(tmp_path, "linux", "BREAKING", prefix="")
        res = self._run(
            ["--manifest", str(self._manifest(tmp_path, "linux")), str(tmp_path)]
        )
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
            [
                "--manifest",
                str(self._manifest(tmp_path, LINUX)),
                "--format",
                "json",
                "-o",
                str(out),
                str(reports),
            ]
        )
        assert res.exit_code == 0
        assert json.loads(out.read_text())["status"] == "pass"

    # ── --run-plan (ADR-054: folds `run-plan to-aggregate-manifest`'s former
    # standalone command into aggregate itself) ─────────────────────────────

    def test_run_plan_flag(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text(
            json.dumps({"checks": [{"check_id": LINUX, "required": True}]})
        )
        res = self._run(["--run-plan", str(run_plan_path), str(tmp_path)])
        assert res.exit_code == 0, res.output

    def test_run_plan_flag_conflicts_with_manifest(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text(
            json.dumps({"checks": [{"check_id": LINUX, "required": True}]})
        )
        (tmp_path / "m.json").write_text(
            json.dumps({"targets": [{"id": LINUX, "required": True}]})
        )
        res = self._run(
            [
                "--run-plan",
                str(run_plan_path),
                "--manifest",
                str(tmp_path / "m.json"),
                str(tmp_path),
            ]
        )
        assert res.exit_code == 64

    def test_run_plan_flag_conflicts_with_discovered_only(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text(
            json.dumps({"checks": [{"check_id": LINUX, "required": True}]})
        )
        res = self._run(
            ["--run-plan", str(run_plan_path), "--discovered-only", str(tmp_path)]
        )
        assert res.exit_code == 64

    def test_run_plan_flag_missing_required_exits_1(self, tmp_path: Path):
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text(
            json.dumps({"checks": [{"check_id": LINUX, "required": True}]})
        )
        res = self._run(["--run-plan", str(run_plan_path), str(tmp_path)])
        assert res.exit_code == 1

    def test_run_plan_flag_empty_checks_is_usage_error(self, tmp_path: Path):
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text(json.dumps({"checks": []}))
        res = self._run(["--run-plan", str(run_plan_path), str(tmp_path)])
        assert res.exit_code == 64

    def test_run_plan_flag_malformed_json_is_usage_error(self, tmp_path: Path):
        run_plan_path = tmp_path / "run-plan.json"
        run_plan_path.write_text("not json")
        res = self._run(["--run-plan", str(run_plan_path), str(tmp_path)])
        assert res.exit_code == 64


class TestJsonSchema:
    """The `--format json` output is a versioned, published machine contract."""

    def test_schema_file_ships_and_is_well_formed(self):
        from abicheck.schemas import load_aggregate_report_schema

        schema = load_aggregate_report_schema()
        assert schema["$id"].endswith("aggregate_report.schema.json")
        assert "aggregate_schema_version" in schema["properties"]

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_real_output_validates_against_schema(self, tmp_path: Path):
        from abicheck.schemas import load_aggregate_report_schema

        # A representative document exercising every axis: an analyzed target
        # with a real gate, an unavailable required target, and a gated
        # unexpected target.
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
        _write_report(tmp_path, MACOS, "BREAKING")  # unexpected
        d = aggregate_reports_dir(tmp_path, expected=_expect(LINUX, WINDOWS)).to_dict()
        jsonschema.validate(d, load_aggregate_report_schema())
        assert d["aggregate_schema_version"] == AGGREGATE_SCHEMA_VERSION
        assert d["unexpected_targets"]

    @pytest.mark.skipif(jsonschema is None, reason="jsonschema not installed")
    def test_discovered_only_output_validates(self, tmp_path: Path):
        from abicheck.schemas import load_aggregate_report_schema

        _write_report(tmp_path, LINUX, "API_BREAK")
        d = aggregate_reports_dir(tmp_path, discovered_only=True).to_dict()
        jsonschema.validate(d, load_aggregate_report_schema())


class TestWarnAcceptanceProvenance:
    """Whether a listed-but-not-gated target is reported as such.

    Two separate claims are at stake. The aggregate may only say a target
    contributed 0 when the report actually *stated* a usable contribution —
    a coverage exit of 0 also covers "said nothing" and "said something
    unusable" (CodeRabbit review). And it must not name the *policy*
    behind that 0: `contract.unresolved=warn` and `gate-mode: advisory`
    neutralization both produce declared-0-with-failures, and this side
    cannot tell them apart (Codex review)."""

    TARGET = "libfoo"
    LINE = "not gated on"

    def _text(self, tmp_path: Path, **extra) -> str:
        _write_report(
            tmp_path,
            self.TARGET,
            "COMPATIBLE",
            contract_coverage_failures=[{"provider": "public_header"}],
            **extra,
        )
        return aggregate_reports_dir(
            tmp_path, expected=_expect(self.TARGET)
        ).render_text()

    def test_it_does_not_name_a_policy_it_cannot_verify(self, tmp_path: Path):
        text = self._text(tmp_path, contract_coverage_exit_contribution=0)
        assert "contract.unresolved=warn" not in text

    def test_a_declared_zero_is_reported_as_not_gated(self, tmp_path: Path):
        assert self.LINE in self._text(tmp_path, contract_coverage_exit_contribution=0)

    def test_a_malformed_contribution_is_not(self, tmp_path: Path):
        assert self.LINE not in self._text(
            tmp_path, contract_coverage_exit_contribution="nope"
        )

    def test_an_absent_contribution_is_not(self, tmp_path: Path):
        assert self.LINE not in self._text(tmp_path)

    def test_the_failures_are_still_listed_either_way(self, tmp_path: Path):
        # Not claiming the policy must not mean hiding the incomplete
        # evidence — the ledger is unsuppressible.
        assert "incomplete on" in self._text(tmp_path)


class TestScanCoverageIsNotACompatibilityFailure:
    """A `scan --against` report's top-level exit_code is already a fold of
    its compatibility gate and ADR-049's contract-coverage contribution, so
    reading it whole as the compatibility gate double-counted a
    coverage-only failure — the target blocked and its profile read as
    affected, where the equivalent `compare` report did not (Codex review).

    Scan emits 0/2/4/5/6 and has no native 1, which is what makes a raw 1
    attributable to the coverage axis rather than a guess."""

    CHECK = "libfoo@linux-gcc14#release@headers"

    def _run(self, tmp_path: Path, exit_code: int, contribution: int | None):
        diff: dict = {"verdict": "NO_CHANGE"}
        if contribution is not None:
            diff["contract_coverage_exit_contribution"] = contribution
        _write_report(
            tmp_path,
            self.CHECK,
            "NO_CHANGE",
            scan_schema_version="1.8",
            exit_code=exit_code,
            diff=diff,
        )
        return aggregate_reports_dir(tmp_path, expected=_expect(self.CHECK))

    def test_coverage_only_exit_does_not_block_compatibility(self, tmp_path: Path):
        res = self._run(tmp_path, 1, 1)
        assert res.blocking_targets == ()
        (entry,) = res.profile_matrix
        assert entry.affected_profiles == ()

    def test_but_it_still_raises_the_exit_on_its_own_axis(self, tmp_path: Path):
        # Moved axis, not dropped: the failure must still gate.
        res = self._run(tmp_path, 1, 1)
        assert res.exit_code() == 1
        assert res.contract_coverage_exit == 1
        (entry,) = res.profile_matrix
        assert entry.contract_incomplete_profiles == ("linux-gcc14",)

    def test_it_matches_the_equivalent_compare_report(self, tmp_path: Path):
        # The whole point: one situation, one answer, whichever command
        # produced the report.
        scan = self._run(tmp_path, 1, 1)
        other = tmp_path / "compare"
        other.mkdir()
        _write_report(
            other, self.CHECK, "NO_CHANGE", contract_coverage_exit_contribution=1
        )
        cmp_res = aggregate_reports_dir(other, expected=_expect(self.CHECK))
        assert scan.blocking_targets == cmp_res.blocking_targets
        assert scan.exit_code() == cmp_res.exit_code()
        assert (
            scan.profile_matrix[0].affected_profiles
            == cmp_res.profile_matrix[0].affected_profiles
        )

    @pytest.mark.parametrize("code", [5, 6])
    def test_a_real_scan_failure_still_blocks(self, tmp_path: Path, code: int):
        # 5 (budget overflow) and 6 (NOT_COMPARABLE) both map to 1 too, and
        # are genuine compatibility-gate failures — discriminating on the
        # raw code is what keeps them blocking.
        res = self._run(tmp_path, code, 1)
        assert res.blocking_targets == (self.CHECK,)
        assert res.profile_matrix[0].affected_profiles == ("linux-gcc14",)

    def test_an_unattributed_exit_one_still_blocks(self, tmp_path: Path):
        # Fail closed: a bare 1 with nothing claiming responsibility for it
        # is not evidence that coverage caused it.
        res = self._run(tmp_path, 1, None)
        assert res.blocking_targets == (self.CHECK,)


class TestContractCoverageProfileMatrix:
    """The profile matrix must name a profile whose only problem is contract
    coverage. Before ``contract_incomplete_profiles`` existed, such a run
    raised the aggregate exit to 1 while every list in the matrix stayed
    empty, so nothing said which profile caused it (Codex review)."""

    CHECK = "libfoo@linux-gcc14#release@headers"

    def _result(self, tmp_path: Path, **extra):
        _write_report(tmp_path, self.CHECK, "COMPATIBLE", **extra)
        return aggregate_reports_dir(tmp_path, expected=_expect(self.CHECK))

    def test_a_contract_incomplete_profile_is_named(self, tmp_path: Path):
        res = self._result(tmp_path, contract_coverage_exit_contribution=1)
        assert res.exit_code() == 1
        (entry,) = res.profile_matrix
        assert entry.contract_incomplete_profiles == ("linux-gcc14",)

    def test_it_does_not_become_affected(self, tmp_path: Path):
        # ADR-049 §7 keeps the coverage axis orthogonal to compatibility:
        # a clean verdict with a clean gate is not "affected" just because
        # its contract domain never closed, or a reader could no longer tell
        # which axis produced the exit of 1.
        res = self._result(tmp_path, contract_coverage_exit_contribution=1)
        (entry,) = res.profile_matrix
        assert entry.affected_profiles == ()
        assert entry.incomplete_profiles == ()
        assert entry.unanalyzed_profiles == ()

    def test_a_clean_profile_is_not_listed(self, tmp_path: Path):
        res = self._result(tmp_path, contract_coverage_exit_contribution=0)
        (entry,) = res.profile_matrix
        assert entry.contract_incomplete_profiles == ()
        assert res.exit_code() == 0

    def test_warn_accepted_evidence_is_still_named(self, tmp_path: Path):
        # contract.unresolved=warn zeroes the exit contribution but the
        # failures stay listed -- the profile is still short of evidence,
        # which is exactly what this field reports.
        res = self._result(
            tmp_path,
            contract_coverage_exit_contribution=0,
            contract_coverage_failures=[{"provider": "public_header"}],
        )
        (entry,) = res.profile_matrix
        assert entry.contract_incomplete_profiles == ("linux-gcc14",)
        assert res.exit_code() == 0

    def test_it_agrees_with_the_top_level_block(self, tmp_path: Path):
        # Both use one predicate; if they ever diverge a reader gets two
        # different answers to "which targets are short of evidence".
        res = self._result(tmp_path, contract_coverage_exit_contribution=1)
        (entry,) = res.profile_matrix
        assert bool(entry.contract_incomplete_profiles) is bool(
            res.contract_coverage_targets
        )

    def test_the_rendered_line_says_so(self, tmp_path: Path):
        res = self._result(tmp_path, contract_coverage_exit_contribution=1)
        text = res.render_text()
        assert "contract evidence incomplete on linux-gcc14" in text


class TestContractCoverageAxis:
    """ADR-049 Phase 7: the contract-coverage ledger gates ``aggregate`` too.

    ``compare`` and ``scan --against`` have folded the contribution since the
    phase's first slice. A matrix build that aggregates their reports and did
    *not* fold it exits ``0`` while a target inside it exited ``1`` — the
    cross-command divergence plan Section 6.4 exists to forbid.
    """

    def test_an_incomplete_target_raises_a_clean_aggregate_to_one(
        self, tmp_path: Path
    ) -> None:
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            contract_coverage_exit_contribution=1,
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 1
        assert result.contract_coverage_targets == (LINUX,)
        assert result.exit_code() == 1
        assert not result.passed

    def test_it_never_lowers_a_real_break(self, tmp_path: Path) -> None:
        # `max`, not replacement: demoting an ABI break to "warnings only"
        # because a *different* axis contributed 1 would be the exact
        # inversion the orthogonality rule forbids.
        _write_report(
            tmp_path,
            LINUX,
            "BREAKING",
            severity={"exit_code": 4, "blocking": True},
            contract_coverage_exit_contribution=1,
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 1
        assert result.exit_code() == 4

    def test_a_report_without_the_key_contributes_nothing(self, tmp_path: Path) -> None:
        # Every pre-2.26 report, and every run without --contract:
        # no contract domain was selected, so there is none to be short of
        # evidence for. This is what keeps existing matrices unchanged.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0
        assert result.exit_code() == 0

    def test_an_accepted_contribution_is_read_not_recomputed(
        self, tmp_path: Path
    ) -> None:
        # A target run under `contract.unresolved=warn` writes 0 with its
        # failures still listed. The aggregate holds none of the evidence to
        # answer the question again, and must not re-impose what that run
        # explicitly accepted.
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            contract_coverage_exit_contribution=0,
            contract_coverage_failures=[
                {"provider": "public_header", "side": "old", "suppressible": False}
            ],
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0
        assert result.exit_code() == 0
        # ...but accepting the floor is not hiding the gap (ADR-049 §6.2).
        # Deriving incompleteness from the contribution alone reported
        # `incomplete_targets: []` and printed no diagnostic at all, for a
        # matrix in which a contract domain never closed (Codex review).
        assert result.contract_coverage_targets == (LINUX,)
        assert result.to_dict()["contract_coverage"]["incomplete_targets"] == [LINUX]
        text = result.render_text()
        assert "Contract coverage:" in text
        # Reported as listed-but-not-gated, without naming the policy: the
        # same shape is produced by `gate-mode: advisory` neutralization,
        # which this side cannot distinguish (Codex review).
        assert f"not gated on {LINUX}" in text

    def test_a_target_with_no_failures_is_not_listed_as_incomplete(
        self, tmp_path: Path
    ) -> None:
        # The converse: the honest listing must not sweep in a target whose
        # domain closed, or "incomplete" would mean nothing.
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            contract_coverage_exit_contribution=0,
            contract_coverage_failures=[],
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_targets == ()
        assert "Contract coverage:" not in result.render_text()

    def test_an_accepted_scan_report_is_listed_from_its_nested_diff(
        self, tmp_path: Path
    ) -> None:
        # A `scan --against` report nests the ledger inside `diff`, the same
        # place the contribution lives -- so the presence check has to consult
        # it too, or accepting the floor hides a scan target specifically.
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.8",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 0,
                    "diff": {
                        "contract_coverage_exit_contribution": 0,
                        "contract_coverage_failures": [
                            {"provider": "export_table", "side": "new"}
                        ],
                    },
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0
        assert result.contract_coverage_targets == (LINUX,)

    @pytest.mark.parametrize(
        "bad",
        ["1", True, -1, None, {"exit": 1}, 2, 4],
        ids=["str", "bool", "negative", "null", "object", "two", "four"],
    )
    def test_a_malformed_contribution_fails_open(self, tmp_path: Path, bad) -> None:
        # Unlike the `severity` gate's fail-closed rule: an unusable value here
        # means "this report says nothing about a contract domain", and
        # blocking on it would fail every report that never asked the question.
        #
        # `2`/`4` matter beyond ordinary malformed input (Codex review): the
        # axis is a 0/1 floor, so passing a larger integer straight through
        # would make the aggregate exit 2 or 4 -- indistinguishable from a
        # source or ABI break -- off a value no valid report can carry.
        #
        # Parametrized rather than looped over per-case temp dirs: deriving a
        # directory name from the value itself produced `case-dict-{'exit':
        # 1}`, which is not a legal Windows path (WinError 267).
        _write_report(
            tmp_path, LINUX, "COMPATIBLE", contract_coverage_exit_contribution=bad
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0
        assert result.exit_code() == 0

    def test_the_axis_is_reported_separately_from_target_coverage(
        self, tmp_path: Path
    ) -> None:
        # Both contribute 1 and they answer different questions ("did every
        # required target report?" vs "could a reporting target close its
        # contract domain?"), so an exit 1 must name which one raised it.
        _write_report(
            tmp_path, LINUX, "COMPATIBLE", contract_coverage_exit_contribution=1
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        payload = result.to_dict()
        assert payload["contract_coverage"] == {
            "exit_contribution": 1,
            "incomplete_targets": [LINUX],
        }
        assert payload["coverage"]["blocking"] is False
        assert payload["targets"][0]["contract_coverage_exit"] == 1
        assert "Contract coverage:" in result.render_text()

    def test_a_scan_report_carries_the_field_inside_its_diff_block(
        self, tmp_path: Path
    ) -> None:
        # `cli_scan_baseline` writes the contribution into the summary that
        # becomes `ScanOutcome.to_dict()['diff']`, so a scan report nests it
        # one level down. Reading only the root left the aggregate failing
        # (the scan's own exit_code already folds it) while reporting
        # exit_contribution 0 and no incomplete targets -- hiding which axis
        # caused the failure (Codex review).
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.0",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 1,
                    "diff": {"contract_coverage_exit_contribution": 1},
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 1
        assert result.contract_coverage_targets == (LINUX,)
        assert result.to_dict()["contract_coverage"]["exit_contribution"] == 1

    def test_a_severity_scheme_scan_gate_survives_a_coincident_coverage_failure(
        self, tmp_path: Path
    ) -> None:
        # Codex review: `from_scan_report` separated the coverage contribution
        # by arguing scan "has no native 1" -- true until severity reached
        # `scan --against`. An error-level addition is a native compatibility
        # 1; folded with a coverage 1 it yields a top-level 1 that the raw-code
        # branch attributed entirely to coverage, returning a *passing* gate
        # and dropping the target from `blocking_targets`. The severity-scheme
        # report answers directly via its own `diff.severity` block.
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.9",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 1,
                    "contract_coverage_exit_contribution": 1,
                    "diff": {
                        "contract_coverage_exit_contribution": 1,
                        "severity": {
                            "exit_code": 1,
                            "blocking": True,
                            "blocking_categories": ["addition"],
                        },
                    },
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        # Both axes fire, independently and for their own reasons.
        assert result.contract_coverage_exit == 1
        assert LINUX in result.blocking_targets, (
            "a severity-gated scan target must keep blocking even when a "
            "coverage failure folds to the same top-level exit code"
        )
        target = next(t for t in result.targets if t.target_id == LINUX)
        assert target.gate is not None
        assert target.gate.blocking_categories == ("addition",)

    def test_a_clean_severity_scheme_scan_still_passes_the_compatibility_axis(
        self, tmp_path: Path
    ) -> None:
        # The complement: a severity gate that cleared must not be turned into
        # a compatibility failure by a coincident coverage-only exit 1 -- the
        # two axes stay orthogonal (ADR-049 §7).
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.9",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 1,
                    "contract_coverage_exit_contribution": 1,
                    "diff": {
                        "contract_coverage_exit_contribution": 1,
                        "severity": {
                            "exit_code": 0,
                            "blocking": False,
                            "blocking_categories": [],
                        },
                    },
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 1
        assert LINUX not in result.blocking_targets

    def test_a_corrupt_scan_severity_gate_fails_closed(self, tmp_path: Path) -> None:
        # A corrupt nested gate must not silently fall through to the greener
        # raw-code path -- same fail-closed rule a corrupt `compare` gate gets.
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.9",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 0,
                    "diff": {
                        "severity": {
                            "exit_code": 0,
                            "blocking": True,  # contradicts exit_code
                            "blocking_categories": [],
                        }
                    },
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        target = next(t for t in result.targets if t.target_id == LINUX)
        assert target.gate is None, "a corrupt gate must make the target unavailable"

    def test_a_non_scan_report_is_not_mined_for_a_nested_field(
        self, tmp_path: Path
    ) -> None:
        # Keyed on `scan_schema_version`, mirroring `GateInfo.from_scan_report`:
        # arbitrary JSON that happens to carry a `diff` key must not be read
        # as a contract-coverage contribution.
        _write_report(
            tmp_path,
            LINUX,
            "COMPATIBLE",
            diff={"contract_coverage_exit_contribution": 1},
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0

    def test_a_root_level_value_still_wins_for_a_scan_report(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.0",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 0,
                    "contract_coverage_exit_contribution": 0,
                    "diff": {"contract_coverage_exit_contribution": 1},
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0

    @pytest.mark.parametrize("bad_root", [True, False], ids=["true", "false"])
    def test_a_boolean_root_does_not_shadow_a_valid_nested_value(
        self, tmp_path: Path, bad_root
    ) -> None:
        # `True == 1` in Python, so a bare `raw not in (0, 1)` membership test
        # accepted a malformed boolean root as usable, skipped the nested
        # `diff` fallback, and only then rejected it on type -- reporting 0 for
        # a scan whose own exit code had already folded a 1. A string root fell
        # through correctly, which is what hid it (Codex review).
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.8",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 1,
                    "contract_coverage_exit_contribution": bad_root,
                    "diff": {"contract_coverage_exit_contribution": 1},
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 1
        assert result.contract_coverage_targets == (LINUX,)

    def test_a_valid_zero_root_still_wins_over_a_nested_value(
        self, tmp_path: Path
    ) -> None:
        # The converse: the nested block is a *fallback* for an unusable root,
        # never an override of a usable one. A run that legitimately recorded 0
        # at the root must not have a stale nested 1 re-imposed on it.
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.8",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 0,
                    "contract_coverage_exit_contribution": 0,
                    "diff": {"contract_coverage_exit_contribution": 1},
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0

    def test_a_service_scan_envelope_is_read_from_report_diff(
        self, tmp_path: Path
    ) -> None:
        # `service_scan.run_scan` returns a typed `ScanResult` whose `report`
        # field is populated from `ScanOutcome.to_dict()` -- so serializing that
        # public result nests the ledger one level deeper than `scan --against`
        # writes it. Reading only a root `diff` reported a 0 contribution and no
        # incomplete target for a scan whose own exit_code already folded a 1
        # (Codex review). Both readers share one block enumeration precisely so
        # a nesting one knows and the other does not cannot happen.
        (tmp_path / f"abi-report-{LINUX}.json").write_text(
            json.dumps(
                {
                    "scan_schema_version": "1.8",
                    "target_id": LINUX,
                    "verdict": "COMPATIBLE",
                    "exit_code": 1,
                    "report": {
                        "diff": {
                            "contract_coverage_exit_contribution": 1,
                            "contract_coverage_failures": [
                                {"provider": "public_header", "side": "old"}
                            ],
                        }
                    },
                }
            )
        )
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 1
        assert result.contract_coverage_targets == (LINUX,)

    def test_a_non_scan_report_is_not_mined_for_a_report_diff_block(
        self, tmp_path: Path
    ) -> None:
        # The deeper nesting stays keyed on `scan_schema_version` like the
        # shallower one: arbitrary JSON carrying a `report` key is not a scan.
        _write_report(tmp_path, LINUX, "COMPATIBLE")
        path = tmp_path / f"abi-report-{LINUX}.json"
        data = json.loads(path.read_text())
        data["report"] = {"diff": {"contract_coverage_exit_contribution": 1}}
        path.write_text(json.dumps(data))
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0
        assert result.contract_coverage_targets == ()

    def test_an_unavailable_target_contributes_nothing_on_this_axis(
        self, tmp_path: Path
    ) -> None:
        # A report that never arrived is already gated by the required-coverage
        # axis; inventing a contract-coverage failure for it would double-count
        # the same absence under two different names.
        result = aggregate_reports_dir(tmp_path, expected=_expect(LINUX))
        assert result.contract_coverage_exit == 0
        assert result.contract_coverage_targets == ()
        assert result.exit_code() == 1  # the required-coverage axis, not this one
