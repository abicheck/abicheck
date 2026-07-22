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
    final_exit_code,
    resolve_effective_depth,
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


class TestResolveEffectiveDepth:
    @pytest.mark.parametrize("depth", ["binary", "headers"])
    def test_binary_and_headers_never_degrade(self, depth):
        effective, coverage = resolve_effective_depth(depth, evidence_ok=False)
        assert effective == depth
        assert coverage == {"state": "complete", "reasons": []}

    @pytest.mark.parametrize("depth", ["build", "source"])
    def test_build_and_source_stay_when_evidence_ok(self, depth):
        effective, coverage = resolve_effective_depth(depth, evidence_ok=True)
        assert effective == depth
        assert coverage["state"] == "complete"

    @pytest.mark.parametrize("depth", ["build", "source"])
    def test_build_and_source_degrade_to_headers_without_evidence(self, depth):
        effective, coverage = resolve_effective_depth(
            depth, evidence_ok=False, degraded_reason="wrapper_pack_empty_for_target"
        )
        assert effective == "headers"
        assert coverage == {
            "state": "degraded",
            "reasons": ["wrapper_pack_empty_for_target"],
        }

    def test_default_reason_when_none_given(self):
        _, coverage = resolve_effective_depth("source", evidence_ok=False)
        assert coverage["reasons"] == ["evidence_not_available"]


class TestAugmentReport:
    def _base_compare_report(self, verdict="BREAKING", exit_code=4):
        return {
            "report_schema_version": "2.12",
            "library": "libpvxs",
            "verdict": verdict,
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
            self._base_compare_report(),
            name="libpvxs",
            profile_id="linux-x86_64-gcc13-release",
            baseline_channel="accepted-main",
            requested_depth="source",
            gate_mode="local",
            evidence_ok=True,
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

    def test_dual_writes_compatibility_verdict_matching_legacy_casing(self):
        out = augment_report(
            self._base_compare_report(verdict="BREAKING"),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            evidence_ok=True,
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
            evidence_ok=True,
        )
        assert out["policy_gate_decision"] == "fail"

        clean = augment_report(
            self._base_compare_report(verdict="COMPATIBLE", exit_code=0),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            evidence_ok=True,
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
                evidence_ok=True,
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
            evidence_ok=True,
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
        }
        out = augment_report(
            scan_report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
            evidence_ok=True,
        )
        assert out["exit_code"] == 0
        assert out["compatibility_verdict"] == "BREAKING"

    def test_analysis_cli_error_populates_operational_errors(self):
        out = augment_report(
            {"verdict": OPERATIONAL_ERROR_VERDICT, "error": "bad flag combination"},
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            evidence_ok=True,
        )
        assert out["verdict"] == "ERROR"
        assert "compatibility_verdict" not in out
        assert out["operational_errors"] == [
            {"kind": "analysis_error", "message": "bad flag combination"}
        ]

    def test_rejects_unknown_gate_mode(self):
        with pytest.raises(ValueError):
            augment_report(
                self._base_compare_report(),
                name="libpvxs",
                profile_id="p",
                baseline_channel="c",
                requested_depth="headers",
                gate_mode="bogus",
                evidence_ok=True,
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
            evidence_ok=True,
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
        assert report["check_id"] == report["target_id"]


class TestBuildBootstrapReport:
    def test_shape_is_never_a_compatibility_verdict(self):
        report = build_bootstrap_report(
            name="libpvxs",
            profile_id="p",
            baseline_channel="release-contract",
            requested_depth="headers",
            resolve_message="no baseline set exists yet.",
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
