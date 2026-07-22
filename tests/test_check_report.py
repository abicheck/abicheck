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

"""Unit tests for ``abicheck/buildsource/check_report.py`` (G30 P1.3,
ADR-047 §7).

Pure-Python tests over hand-authored report dicts -- no compiler, no real
``abicheck compare``/``check-target`` run needed. See
``tests/test_action_check_target.py`` for the bash/CLI-level orchestration
this module's logic backs.
"""

from __future__ import annotations

import pytest

from abicheck.buildsource.check_report import (
    BOOTSTRAP_VERDICT,
    OPERATIONAL_ERROR_VERDICT,
    augment_report,
    build_bootstrap_report,
    build_check_id,
    build_operational_error_report,
    derive_effective_depth,
    final_exit_code,
    validate_identifier,
)


class TestValidateIdentifier:
    def test_accepts_safe_charset(self):
        validate_identifier("target", "libpvxs")
        validate_identifier("target", "libpvxs-Ioc.v2")

    @pytest.mark.parametrize("value", ["", "@bad", "has space", "has#hash", "has@at"])
    def test_rejects_unsafe_charset(self, value):
        with pytest.raises(ValueError):
            validate_identifier("target", value)


class TestBuildCheckId:
    def test_shape(self):
        check_id = build_check_id(
            "libpvxs", "linux-x86_64-gcc13-release", "accepted-main", "source"
        )
        assert check_id == "libpvxs@linux-x86_64-gcc13-release#accepted-main@source"

    def test_unconditional_depth_suffix_disambiguates_shadow_checks(self):
        """ADR-047 §7: two checks differing only in requested_depth must not collide."""
        header_id = build_check_id("libpvxs", "p", "accepted-main", "headers")
        source_id = build_check_id("libpvxs", "p", "accepted-main", "source")
        assert header_id != source_id

    def test_rejects_bad_depth(self):
        with pytest.raises(ValueError):
            build_check_id("libpvxs", "p", "c", "bogus-depth")

    def test_rejects_unsafe_component(self):
        with pytest.raises(ValueError):
            build_check_id("lib@pvxs", "p", "c", "headers")

    def test_rejects_unsafe_profile(self):
        with pytest.raises(ValueError):
            build_check_id("libpvxs", "p@bad", "c", "headers")

    def test_rejects_unsafe_channel(self):
        with pytest.raises(ValueError):
            build_check_id("libpvxs", "p", "c#bad", "headers")


class TestDeriveEffectiveDepth:
    """ADR-047 §7's authoritative-signal design: read the depth actually
    achieved straight from the compare/scan report's own output, never from
    a caller-supplied heuristic (Codex review: an earlier collect-facts-
    producer-based heuristic misreported a real build/source-depth result
    achieved via a direct --build-info/--sources input, with no producer
    step at all, as "degraded")."""

    @pytest.mark.parametrize("depth", ["binary", "headers", "build", "source"])
    def test_compare_report_matching_depth_is_complete(self, depth):
        report = {"old_evidence_depth": depth, "new_evidence_depth": depth}
        effective, coverage = derive_effective_depth(report, depth)
        assert effective == depth
        assert coverage == {"state": "complete", "reasons": []}

    def test_compare_report_takes_shallower_side(self):
        report = {"old_evidence_depth": "source", "new_evidence_depth": "headers"}
        effective, coverage = derive_effective_depth(report, "source")
        assert effective == "headers"
        assert coverage == {
            "state": "degraded",
            "reasons": ["compare_achieved_headers"],
        }

    def test_compare_report_shallower_than_requested_degrades(self):
        report = {"old_evidence_depth": "headers", "new_evidence_depth": "headers"}
        effective, coverage = derive_effective_depth(report, "source")
        assert effective == "headers"
        assert coverage["state"] == "degraded"
        assert coverage["reasons"] == ["compare_achieved_headers"]

    def test_compare_report_deeper_than_requested_is_honestly_reported(self):
        """Achieving more than requested isn't a degradation -- report the
        real depth, don't artificially cap it down to the request."""
        report = {"old_evidence_depth": "source", "new_evidence_depth": "source"}
        effective, coverage = derive_effective_depth(report, "binary")
        assert effective == "source"
        assert coverage["state"] == "complete"

    def test_scan_report_level_depth_used_when_no_compare_fields(self):
        report = {"level": {"depth": "build", "source_method": "s4"}}
        effective, coverage = derive_effective_depth(report, "build")
        assert effective == "build"
        assert coverage["state"] == "complete"

    def test_scan_report_shallower_than_requested_degrades(self):
        report = {"level": {"depth": "headers"}}
        effective, coverage = derive_effective_depth(report, "source")
        assert effective == "headers"
        assert coverage == {"state": "degraded", "reasons": ["scan_achieved_headers"]}

    def test_no_depth_signal_falls_back_to_requested_as_unknown(self):
        effective, coverage = derive_effective_depth({}, "source")
        assert effective == "source"
        assert coverage == {
            "state": "unknown",
            "reasons": ["no_depth_signal_in_report"],
        }

    def test_malformed_level_field_is_treated_as_no_signal(self):
        effective, coverage = derive_effective_depth({"level": "not-a-dict"}, "headers")
        assert effective == "headers"
        assert coverage["state"] == "unknown"

    def test_non_string_evidence_depth_fields_are_ignored(self):
        report = {"old_evidence_depth": 1, "new_evidence_depth": None}
        effective, coverage = derive_effective_depth(report, "headers")
        assert coverage["state"] == "unknown"
        assert effective == "headers"

    def test_rejects_bad_requested_depth(self):
        with pytest.raises(ValueError):
            derive_effective_depth({}, "bogus")


class TestAugmentReport:
    def _base_compare_report(
        self, verdict="BREAKING", exit_code=4, old_depth="headers", new_depth="headers"
    ):
        return {
            "report_schema_version": "2.12",
            "library": "libpvxs",
            "verdict": verdict,
            "old_evidence_depth": old_depth,
            "new_evidence_depth": new_depth,
            "severity": {
                "config": {},
                "categories": {},
                "exit_code": exit_code,
                "blocking": exit_code != 0,
                "blocking_categories": ["abi_breaking"] if exit_code else [],
            },
        }

    def test_writes_identity_fields(self):
        out = augment_report(
            self._base_compare_report(old_depth="source", new_depth="source"),
            name="libpvxs",
            profile_id="linux-x86_64-gcc13-release",
            baseline_channel="accepted-main",
            requested_depth="source",
            gate_mode="local",
        )
        assert (
            out["check_id"] == "libpvxs@linux-x86_64-gcc13-release#accepted-main@source"
        )
        assert out["target_id"] == out["check_id"]
        assert out["profile_id"] == "linux-x86_64-gcc13-release"
        assert out["baseline_channel"] == "accepted-main"
        assert out["requested_depth"] == "source"
        assert out["effective_depth"] == "source"
        assert out["check_evidence_coverage"] == {"state": "complete", "reasons": []}
        assert out["report_schema_version"] != "2.12"  # bumped to the current version

    def test_degrades_effective_depth_from_real_report_signal(self):
        """The Codex-flagged bug: a producer-less build/source check (direct
        --build-info/--sources, no collect-facts composition) must not be
        misreported as degraded just because no producer step ran -- the
        real signal comes from the report itself."""
        out = augment_report(
            self._base_compare_report(old_depth="source", new_depth="source"),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="source",
            gate_mode="local",
        )
        assert out["effective_depth"] == "source"
        assert out["check_evidence_coverage"]["state"] == "complete"

    def test_dual_writes_compatibility_verdict_matching_legacy_casing(self):
        out = augment_report(
            self._base_compare_report(verdict="BREAKING"),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["verdict"] == "BREAKING"
        assert out["compatibility_verdict"] == "BREAKING"

    def test_policy_gate_decision_reflects_real_exit_code(self):
        out = augment_report(
            self._base_compare_report(verdict="BREAKING", exit_code=4),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["policy_gate_decision"] == "fail"

        clean = augment_report(
            self._base_compare_report(verdict="COMPATIBLE", exit_code=0),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert clean["policy_gate_decision"] == "pass"

    def test_local_and_deferred_never_neutralize_severity(self):
        for gate_mode in ("local", "deferred"):
            out = augment_report(
                self._base_compare_report(verdict="BREAKING", exit_code=4),
                name="libpvxs",
                profile_id="p",
                baseline_channel="c",
                requested_depth="headers",
                gate_mode=gate_mode,
            )
            assert out["severity"]["exit_code"] == 4
            assert out["severity"]["blocking"] is True
            # The real finding must still be visible, unmutated.
            assert out["policy_gate_decision"] == "fail"

    def test_advisory_neutralizes_severity_but_keeps_real_finding_visible(self):
        """ADR-047 §7's third required sub-task: an advisory cell with a real
        BREAKING compatibility_verdict must not raise aggregate's computed
        exit_code() -- so the persisted severity block must read clean."""
        out = augment_report(
            self._base_compare_report(verdict="BREAKING", exit_code=4),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["severity"]["exit_code"] == 0
        assert out["severity"]["blocking"] is False
        assert out["severity"]["blocking_categories"] == []
        # Real finding stays visible in the new, richer fields:
        assert out["compatibility_verdict"] == "BREAKING"
        assert out["policy_gate_decision"] == "fail"
        assert out["verdict"] == "BREAKING"

    def test_advisory_neutralizes_scan_exit_code(self):
        scan_report = {
            "scan_schema_version": "1.1",
            "verdict": "BREAKING",
            "exit_code": 4,
            "level": {"depth": "headers"},
        }
        out = augment_report(
            scan_report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["exit_code"] == 0
        assert out["compatibility_verdict"] == "BREAKING"

    def test_scan_report_with_no_severity_block_defaults_pass(self):
        scan_report = {
            "scan_schema_version": "1.1",
            "verdict": "COMPATIBLE",
            "exit_code": 0,
            "level": {"depth": "headers"},
        }
        out = augment_report(
            scan_report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["policy_gate_decision"] == "pass"

    def test_malformed_severity_exit_code_treated_as_pass(self):
        report = self._base_compare_report()
        report["severity"]["exit_code"] = "not-an-int"
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["policy_gate_decision"] == "pass"

    def test_malformed_scan_exit_code_treated_as_pass(self):
        report = {
            "scan_schema_version": "1.1",
            "verdict": "COMPATIBLE",
            "exit_code": "not-an-int",
            "level": {"depth": "headers"},
        }
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["policy_gate_decision"] == "pass"

    def test_analysis_cli_error_populates_operational_errors(self):
        out = augment_report(
            {"verdict": OPERATIONAL_ERROR_VERDICT, "error": "bad flag combination"},
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["verdict"] == "ERROR"
        assert "compatibility_verdict" not in out
        assert out["operational_errors"] == [
            {"kind": "analysis_error", "message": "bad flag combination"}
        ]

    def test_advisory_neutralize_is_a_no_op_when_report_has_no_gate_block(self):
        out = augment_report(
            {"verdict": OPERATIONAL_ERROR_VERDICT, "error": "usage error"},
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert "severity" not in out
        assert "exit_code" not in out

    def test_analysis_cli_error_with_no_message_gets_a_generic_one(self):
        out = augment_report(
            {"verdict": OPERATIONAL_ERROR_VERDICT},
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["operational_errors"] == [
            {"kind": "analysis_error", "message": "the analysis step failed"}
        ]

    def test_existing_operational_errors_are_not_overwritten(self):
        out = augment_report(
            self._base_compare_report(verdict="COMPATIBLE", exit_code=0),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["operational_errors"] == []

    def test_existing_publication_is_not_overwritten(self):
        report = self._base_compare_report()
        report["publication"] = {"state": "failed", "channels": []}
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["publication"] == {"state": "failed", "channels": []}

    def test_optional_identity_fields_omitted_when_none(self):
        out = augment_report(
            self._base_compare_report(),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert "project" not in out
        assert "head_sha" not in out
        assert "base_ref" not in out
        assert "action_version" not in out

    def test_optional_identity_fields_set_when_provided(self):
        out = augment_report(
            self._base_compare_report(),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            project="epics-base/pvxs",
            head_sha="deadbeef",
            base_ref="main",
            action_version="abicheck/abicheck@v1",
        )
        assert out["project"] == "epics-base/pvxs"
        assert out["head_sha"] == "deadbeef"
        assert out["base_ref"] == "main"
        assert out["action_version"] == "abicheck/abicheck@v1"

    def test_rejects_unknown_gate_mode(self):
        with pytest.raises(ValueError):
            augment_report(
                self._base_compare_report(),
                name="libpvxs",
                profile_id="p",
                baseline_channel="c",
                requested_depth="headers",
                gate_mode="bogus",
            )

    def test_does_not_mutate_input(self):
        original = self._base_compare_report()
        snapshot = dict(original)
        augment_report(
            original,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert original == snapshot


class TestBuildOperationalErrorReport:
    def test_shape(self):
        report = build_operational_error_report(
            name="libpvxs",
            profile_id="p",
            baseline_channel="accepted-main",
            requested_depth="headers",
            resolve_outcome="wrong_profile",
            resolve_message="baseline built for a different profile.",
            project="epics-base/pvxs",
            head_sha="deadbeef",
            base_ref="main",
            tool_version="abicheck 0.x.y",
            action_version="abicheck/abicheck@v1",
        )
        assert report["verdict"] == OPERATIONAL_ERROR_VERDICT
        assert "severity" not in report
        assert report["compatibility_verdict"] is None
        assert report["policy_gate_decision"] == "fail"
        assert report["operational_errors"] == [
            {
                "kind": "wrong_profile",
                "message": "baseline built for a different profile.",
            }
        ]
        assert report["publication"] == {"state": "skipped", "channels": []}
        assert report["project"] == "epics-base/pvxs"
        assert report["head_sha"] == "deadbeef"
        assert report["base_ref"] == "main"
        assert report["tool_version"] == "abicheck 0.x.y"
        assert report["action_version"] == "abicheck/abicheck@v1"
        assert report["check_id"] == report["target_id"]

    def test_optional_fields_omitted_when_not_given(self):
        report = build_operational_error_report(
            name="libpvxs",
            profile_id="p",
            baseline_channel="accepted-main",
            requested_depth="headers",
            resolve_outcome="not_found",
            resolve_message="no baseline set exists.",
        )
        assert "project" not in report
        assert "head_sha" not in report
        assert "base_ref" not in report
        assert "tool_version" not in report
        assert "action_version" not in report


class TestBuildBootstrapReport:
    def test_shape_is_never_a_compatibility_verdict(self):
        report = build_bootstrap_report(
            name="libpvxs",
            profile_id="p",
            baseline_channel="release-contract",
            requested_depth="headers",
            resolve_message="no baseline set exists yet.",
            project="epics-base/pvxs",
            head_sha="deadbeef",
            base_ref="main",
            tool_version="abicheck 0.x.y",
            action_version="abicheck/abicheck@v1",
        )
        assert report["verdict"] == BOOTSTRAP_VERDICT
        assert report["verdict"] not in {
            "NO_CHANGE",
            "COMPATIBLE",
            "COMPATIBLE_WITH_RISK",
            "API_BREAK",
            "BREAKING",
            "ERROR",
        }
        assert report["baseline_bootstrap"] is True
        assert report["operational_errors"] == []
        assert report["policy_gate_decision"] == "pass"
        assert report["message"] == "no baseline set exists yet."
        assert report["project"] == "epics-base/pvxs"
        assert report["tool_version"] == "abicheck 0.x.y"

    def test_optional_fields_omitted_when_not_given(self):
        report = build_bootstrap_report(
            name="libpvxs",
            profile_id="p",
            baseline_channel="release-contract",
            requested_depth="headers",
            resolve_message="no baseline set exists yet.",
        )
        assert "project" not in report
        assert "head_sha" not in report
        assert "base_ref" not in report
        assert "tool_version" not in report
        assert "action_version" not in report


class TestFinalExitCode:
    def test_local_reflects_real_exit_code(self):
        assert final_exit_code("local", real_exit_code=4, operational_error=False) == 4
        assert final_exit_code("local", real_exit_code=0, operational_error=False) == 0

    @pytest.mark.parametrize("gate_mode", ["deferred", "advisory"])
    def test_deferred_and_advisory_never_fail_on_a_real_finding(self, gate_mode):
        assert (
            final_exit_code(gate_mode, real_exit_code=4, operational_error=False) == 0
        )

    @pytest.mark.parametrize("gate_mode", ["local", "deferred", "advisory"])
    def test_operational_error_always_fails_regardless_of_gate_mode(self, gate_mode):
        assert final_exit_code(gate_mode, real_exit_code=0, operational_error=True) == 1

    def test_rejects_unknown_gate_mode(self):
        with pytest.raises(ValueError):
            final_exit_code("bogus", real_exit_code=0, operational_error=False)
