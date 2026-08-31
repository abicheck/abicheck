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
    NEW_TARGET_VERDICT,
    OPERATIONAL_ERROR_VERDICT,
    augment_report,
    build_bootstrap_report,
    build_check_id,
    build_new_target_report,
    build_operational_error_report,
    derive_effective_depth,
    final_exit_code,
    validate_identifier,
)

#: The clean `ExitDecision.to_dict()` shape, shared by every `augment_report`
#: neutralization assertion below so a future schema bump touches one spot.
_CLEAN_EXIT_BLOCK = {
    "code": 0, "reasons": ["clean"], "compatibility_contribution": 0,
    "contract_coverage_contribution": 0, "analysis_assurance_contribution": 0,
    "crosscheck_promotion_contribution": 0, "operational_error_contribution": 0,
    "evidence_contract_error_contribution": 0, "budget_overflow_contribution": 0,
    "not_comparable_contribution": 0, "removed_required_library_contribution": 0,
}


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

    def test_scan_report_gets_scan_schema_version_not_report_schema_version(self):
        """A scan report (baseline-channel: none) has its own schema marker
        and shape -- no library/old_file/summary/changes/... -- so it must
        never be stamped with report_schema_version (the *compare*-report
        schema's marker): a downstream validator selecting a schema by that
        key's presence would pick compare_report.schema.json for a report
        that structurally can never satisfy it (Codex review)."""
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
        assert "report_schema_version" not in out
        assert out["scan_schema_version"] != "1.1"  # bumped to the current version

    def test_bundle_release_report_gets_no_schema_version_stamp(self):
        """A kind: bundle / directory-package compare report (the per-library
        release fan-out's own verdict/old_dir/new_dir/libraries shape) has
        never had a schema of its own -- must not be falsely stamped with
        the single-pair compare schema's report_schema_version either
        (Codex review)."""
        bundle_report = {
            "verdict": "BREAKING",
            "old_dir": "/old",
            "new_dir": "/new",
            "libraries": [{"library": "libpvxs", "verdict": "BREAKING"}],
            "severity": {
                "config": {},
                "categories": {},
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            },
        }
        out = augment_report(
            bundle_report,
            name="pvxs-bundle",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert "report_schema_version" not in out
        assert "scan_schema_version" not in out
        # ADR-047 identity/policy-gate fields still apply regardless of shape.
        assert out["check_id"] == "pvxs-bundle@p#c@headers"
        assert out["policy_gate_decision"] == "fail"

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

    def test_analysis_exit_code_overrides_a_clean_severity_block(self):
        """The exact Codex-flagged gap: --fail-on-removed-library on a
        directory/package compare makes the CLI process exit 8 "in
        preference to the severity code" (cli_compare_release_helpers.py's
        _exit_compare_release), so a bundle report's own severity.exit_code
        can read 0/COMPATIBLE_WITH_RISK even though the real process exited
        nonzero. analysis_exit_code must win via max() so the gate doesn't
        silently pass a removed-library check the caller explicitly asked
        for."""
        report = self._base_compare_report(verdict="COMPATIBLE_WITH_RISK", exit_code=0)
        out = augment_report(
            report,
            name="libpvxs-bundle",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            analysis_exit_code=8,
        )
        assert out["policy_gate_decision"] == "fail"

    def test_removed_library_exit_code_escalates_persisted_severity(self):
        """Escalating policy_gate_decision alone isn't enough: gate-mode:
        deferred relies on check-project.yml's trailing aggregate job, and
        abicheck.aggregate.GateInfo.from_report_data reads ONLY the
        persisted severity.exit_code -- it has no way to see
        policy_gate_decision. Without also updating severity here, a
        removed-library gate on a deferred bundle check would still be
        silently missed downstream (Codex review, second pass). Escalated
        to exit_code 4 (abi_breaking) -- 8 itself isn't a legal
        severity.exit_code (aggregate.py's _VALID_GATE_EXIT is {0,1,2,4})
        and would raise _MalformedGate there."""
        report = self._base_compare_report(verdict="COMPATIBLE_WITH_RISK", exit_code=0)
        out = augment_report(
            report,
            name="libpvxs-bundle",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            analysis_exit_code=8,
        )
        assert out["severity"]["exit_code"] == 4
        assert out["severity"]["blocking"] is True
        assert "abi_breaking" in out["severity"]["blocking_categories"]

    def test_removed_library_escalation_does_not_duplicate_an_existing_category(self):
        report = self._base_compare_report(verdict="COMPATIBLE_WITH_RISK", exit_code=2)
        report["severity"]["blocking_categories"] = ["abi_breaking"]
        out = augment_report(
            report,
            name="libpvxs-bundle",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            analysis_exit_code=8,
        )
        assert out["severity"]["exit_code"] == 4
        assert out["severity"]["blocking_categories"] == ["abi_breaking"]

    def test_removed_library_escalation_does_not_downgrade_an_already_worse_severity(
        self,
    ):
        report = self._base_compare_report(verdict="BREAKING", exit_code=4)
        report["severity"]["blocking_categories"] = ["abi_breaking"]
        out = augment_report(
            report,
            name="libpvxs-bundle",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            analysis_exit_code=8,
        )
        assert out["severity"]["exit_code"] == 4
        assert out["severity"]["blocking_categories"] == ["abi_breaking"]

    def test_removed_library_escalation_only_triggers_on_exit_code_8(self):
        """Any other nonzero analysis_exit_code folds into policy_gate_decision
        (already covered above) but must NOT rewrite the severity block --
        8 is the one specific, well-known value that bypasses severity."""
        report = self._base_compare_report(verdict="COMPATIBLE", exit_code=0)
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            analysis_exit_code=64,
        )
        assert out["severity"]["exit_code"] == 0
        assert out["policy_gate_decision"] == "fail"  # still folded via max()

    def test_removed_library_escalation_is_a_no_op_without_a_severity_block(self):
        """A scan-shaped report has no severity block at all -- the
        escalation must not crash or invent one (exit 8 only ever comes
        from the release/bundle compare path, never scan, but stay
        defensive)."""
        out = augment_report(
            {
                "scan_schema_version": "1.1",
                "verdict": "COMPATIBLE",
                "exit_code": 0,
                "level": {"depth": "headers"},
            },
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            analysis_exit_code=8,
        )
        assert "severity" not in out

    def test_analysis_exit_code_of_zero_does_not_flip_a_clean_report(self):
        out = augment_report(
            self._base_compare_report(verdict="COMPATIBLE", exit_code=0),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
            analysis_exit_code=0,
        )
        assert out["policy_gate_decision"] == "pass"

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

    def test_advisory_neutralizes_a_scan_nested_severity_gate(self):
        """Codex review (P1): a severity-scheme `scan --against` (schema 1.9+)
        publishes a real gate at `diff.severity`, and
        `aggregate.GateInfo.from_scan_report` *prefers* it over the top-level
        `exit_code` this function already zeroed -- so an explicitly advisory
        check still blocked the trailing aggregate.
        """
        import copy

        from abicheck.aggregate import GateInfo

        scan_report = {
            "scan_schema_version": "1.9",
            "verdict": "COMPATIBLE",
            "exit_code": 1,
            "diff": {
                "breaking": 0,
                "severity": {
                    "exit_code": 1,
                    "blocking": True,
                    "blocking_categories": ["addition"],
                },
            },
        }
        original = copy.deepcopy(scan_report)
        out = augment_report(
            scan_report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["diff"]["severity"]["exit_code"] == 0
        assert out["diff"]["severity"]["blocking"] is False
        assert out["diff"]["severity"]["blocking_categories"] == []
        # The point of the fix: what the aggregate actually reads.
        gate = GateInfo.from_scan_report(out)
        assert gate is not None and gate.blocking is False
        # …and this module's "the caller's report is never mutated" contract
        # must survive writing through a *nested* container.
        assert scan_report == original

    def test_deferred_keeps_a_scan_nested_severity_gate(self):
        """The complement: `deferred` exists so the trailing aggregate computes
        the gate from the real value, so neutralizing there would defeat it.
        """
        from abicheck.aggregate import GateInfo

        scan_report = {
            "scan_schema_version": "1.9",
            "verdict": "COMPATIBLE",
            "exit_code": 1,
            "diff": {
                "severity": {
                    "exit_code": 1,
                    "blocking": True,
                    "blocking_categories": ["addition"],
                }
            },
        }
        out = augment_report(
            scan_report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="deferred",
        )
        gate = GateInfo.from_scan_report(out)
        assert gate is not None and gate.blocking is True
        assert gate.blocking_categories == ("addition",)

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

    def test_advisory_also_neutralizes_the_contract_coverage_axis(self):
        """ADR-049 Phase 7 added a *second* way a report raises an exit code.

        Zeroing only the compatibility gate left an advisory cell still
        driving the trailing ``aggregate`` job to exit 1 through the
        orthogonal contract-coverage axis (Codex review, reproduced end to
        end). "Advisory" has to mean "gates nothing" on every axis this
        report can contribute to, not just the one that existed when
        ``_neutralize_gate`` was written.
        """
        out = augment_report(
            {
                "verdict": "BREAKING",
                "severity": {"exit_code": 4, "blocking": True},
                "contract_coverage_exit_contribution": 1,
                "contract_coverage_failures": [{"provider": "public_header"}],
            },
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["contract_coverage_exit_contribution"] == 0
        # Not gating is not the same as hiding: the ledger is deliberately
        # unsuppressible, so the failures stay exactly as recorded.
        assert out["contract_coverage_failures"] == [{"provider": "public_header"}]

    def test_advisory_neutralizes_a_scan_report_nested_contribution(self):
        """A `scan --against` report carries these fields under `diff`.

        Zeroing only the document root left the nested contribution intact,
        and the aggregate — which explicitly reads the scan-shaped block —
        folded it straight back into the CI exit, so an advisory scan gated
        anyway (Codex review, reproduced end to end).
        """
        out = augment_report(
            {
                "scan_schema_version": "1.8",
                "exit_code": 1,
                "verdict": "NO_CHANGE",
                "diff": {
                    "verdict": "NO_CHANGE",
                    "contract_coverage_exit_contribution": 1,
                    "contract_coverage_failures": [{"provider": "public_header"}],
                },
            },
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["exit_code"] == 0
        assert out["diff"]["contract_coverage_exit_contribution"] == 0
        assert out["diff"]["contract_coverage_failures"] == [
            {"provider": "public_header"}
        ]

    def test_advisory_also_neutralizes_the_analysis_assurance_axis(self):
        """P0.4's analysis-assurance contribution is the exact sibling of
        the contract-coverage one above -- a second, independent way this
        report can raise an exit code, so it needs the identical
        neutralization or an advisory cell still drives the trailing
        aggregate to exit 1 through this axis instead."""
        out = augment_report(
            {
                "verdict": "BREAKING",
                "severity": {"exit_code": 4, "blocking": True},
                "analysis_assurance_exit_contribution": 1,
                "analysis_assurance": {"status": "partial"},
            },
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["analysis_assurance_exit_contribution"] == 0
        # Not gating is not the same as hiding: the descriptive block stays
        # exactly as recorded, same discipline as the coverage ledger.
        assert out["analysis_assurance"] == {"status": "partial"}

    def test_advisory_neutralizes_a_scan_reports_nested_assurance_contribution(self):
        """A `scan --against` report carries this field under `diff`, same
        as the contract-coverage one."""
        out = augment_report(
            {
                "scan_schema_version": "1.17",
                "exit_code": 1,
                "verdict": "NO_CHANGE",
                "diff": {
                    "verdict": "NO_CHANGE",
                    "analysis_assurance_exit_contribution": 1,
                },
            },
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["exit_code"] == 0
        assert out["diff"]["analysis_assurance_exit_contribution"] == 0

    def test_advisory_neutralizes_the_canonical_exit_block(self):
        """CLI cleanup phase two, PR G1/PR E: a real ``exit`` object (Codex
        review, reproduced end to end) -- an advisory report must publish a
        clean decision on this block too, or a consumer reading it directly
        (rather than re-deriving from ``severity``/the two contributions
        above) would treat an explicitly advisory check as blocking.
        """
        out = augment_report(
            {
                "verdict": "BREAKING",
                "severity": {"exit_code": 4, "blocking": True},
                "exit": {
                    "code": 4,
                    "reasons": ["compatibility_gate"],
                    "compatibility_contribution": 4,
                    "contract_coverage_contribution": 0,
                    "analysis_assurance_contribution": 0,
                    "crosscheck_promotion_contribution": 0,
                },
            },
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["exit"] == _CLEAN_EXIT_BLOCK

    def test_advisory_neutralizes_a_scan_reports_nested_exit_block(self):
        """A ``scan --against`` report carries this block under ``diff``,
        same as ``severity``/the two contributions -- and includes a
        maintainer-promoted crosscheck's own nonzero contribution, which an
        advisory report must also zero, not just the code/reasons."""
        out = augment_report(
            {
                "scan_schema_version": "1.18",
                "exit_code": 2,
                "verdict": "API_BREAK",
                "diff": {
                    "verdict": "API_BREAK",
                    "exit": {
                        "code": 2,
                        "reasons": ["compatibility_gate", "promoted_crosscheck"],
                        "compatibility_contribution": 2,
                        "contract_coverage_contribution": 0,
                        "analysis_assurance_contribution": 0,
                        "crosscheck_promotion_contribution": 2,
                    },
                },
            },
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["diff"]["exit"] == _CLEAN_EXIT_BLOCK

    def test_neutralization_covers_every_block_the_aggregate_reads(self):
        # The invariant behind the fix: the writer must zero exactly the
        # blocks the reader consults. Asserting it against the shared
        # traversal is what stops the two drifting apart again.
        from abicheck.aggregate import contract_coverage_blocks

        out = augment_report(
            {
                "scan_schema_version": "1.8",
                "exit_code": 1,
                "verdict": "NO_CHANGE",
                "contract_coverage_exit_contribution": 1,
                "diff": {
                    "verdict": "NO_CHANGE",
                    "contract_coverage_exit_contribution": 1,
                },
            },
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        blocks = contract_coverage_blocks(out)
        assert len(blocks) >= 2
        for block in blocks:
            assert block.get("contract_coverage_exit_contribution", 0) == 0

    def test_a_read_only_nested_mapping_is_neutralized_too(self):
        """An immutable nested block must still be neutralized, not skipped.

        `contract_coverage_block_paths` admits any `Mapping`, and the
        aggregate reads any `Mapping` — so skipping a non-`dict` one left its
        contribution at 1 and an advisory check still gated CI (CodeRabbit
        review, reproduced). Copying into a real `dict` and rebinding is what
        makes an unwritable block writable *and* keeps the caller's own
        container untouched.
        """
        from types import MappingProxyType

        proxy = MappingProxyType(
            {"verdict": "NO_CHANGE", "contract_coverage_exit_contribution": 1}
        )
        original = {
            "scan_schema_version": "1.8",
            "exit_code": 1,
            "verdict": "NO_CHANGE",
            "diff": proxy,
        }
        out = augment_report(
            original,
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["exit_code"] == 0
        assert out["diff"]["contract_coverage_exit_contribution"] == 0
        # The caller's immutable view is neither written through nor swapped.
        assert original["diff"] is proxy
        assert proxy["contract_coverage_exit_contribution"] == 1

    def test_deferred_keeps_its_contract_coverage_contribution(self):
        # `deferred` exists so the trailing aggregate computes the gate from
        # the real values -- neutralizing it there would blind that
        # computation, on this axis exactly as on the severity one.
        out = augment_report(
            {
                "verdict": "COMPATIBLE",
                "severity": {"exit_code": 0, "blocking": False},
                "contract_coverage_exit_contribution": 1,
            },
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="deferred",
        )
        assert out["contract_coverage_exit_contribution"] == 1

    @pytest.mark.parametrize(
        "payload",
        [
            pytest.param({}, id="absent"),
            pytest.param(
                {"contract_coverage_exit_contribution": "nope"}, id="malformed"
            ),
        ],
    )
    def test_advisory_does_not_invent_a_contribution_that_was_never_stated(
        self, payload: dict
    ):
        # An absent or unusable value stays absent/unusable rather than
        # becoming a 0 the run never declared -- otherwise an advisory run
        # would look like it had answered the coverage question.
        out = augment_report(
            {"verdict": "COMPATIBLE", "severity": {"exit_code": 0}, **payload},
            name="libfoo",
            profile_id="linux-gcc14",
            baseline_channel="release",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out.get("contract_coverage_exit_contribution") == payload.get(
            "contract_coverage_exit_contribution"
        )

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

    @pytest.mark.parametrize(
        "guard_verdict", ["BUDGET_OVERFLOW", "EVIDENCE_CONTRACT_ERROR"]
    )
    def test_scan_guard_sentinel_verdicts_are_operational_errors(self, guard_verdict):
        """A scan guard sentinel (service_scan.py's BUDGET_OVERFLOW/
        EVIDENCE_CONTRACT_ERROR) is not a compatibility finding -- the scan
        never completed its comparison at all. Must be classified
        operational (populating operational_errors) so gate-mode: deferred/
        advisory can't turn a guard failure into a quiet pass (Codex
        review) -- it was previously only checking verdict == "ERROR"."""
        out = augment_report(
            {
                "scan_schema_version": "1.1",
                "verdict": guard_verdict,
                "exit_code": 5 if guard_verdict == "BUDGET_OVERFLOW" else 1,
                "level": {"depth": "headers"},
            },
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["verdict"] == guard_verdict
        assert "compatibility_verdict" not in out
        assert out["operational_errors"] == [
            {
                "kind": "scan_guard_triggered",
                "message": f"the analysis reported a non-compatibility verdict: {guard_verdict!r}",
            }
        ]
        assert out["policy_gate_decision"] == "fail"

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

    def test_publication_defaults_to_skipped_not_a_false_claim(self):
        """check-target's own nested analysis step always disables
        add-job-summary/pr-comment/upload-sarif, and finalize only writes
        the report JSON to disk -- none of that is a real ADR-047 §7
        publication channel, so the default must not falsely claim
        published/job_summary (Codex review)."""
        out = augment_report(
            self._base_compare_report(verdict="COMPATIBLE", exit_code=0),
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="local",
        )
        assert out["publication"] == {"state": "skipped", "channels": []}

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
        # Omitted, not null -- the schema declares compatibility_verdict a
        # plain string enum with no null alternative.
        assert "compatibility_verdict" not in report
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
        assert "compatibility_verdict" not in report
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


class TestBuildNewTargetReport:
    def test_shape_is_never_a_compatibility_verdict(self):
        report = build_new_target_report(
            name="libnew",
            profile_id="p",
            baseline_channel="release-contract",
            requested_depth="source",
            resolve_message="target 'libnew' is not in this baseline-set's manifest.",
            project="epics-base/pvxs",
            head_sha="deadbeef",
            base_ref="main",
            tool_version="abicheck 0.x.y",
            action_version="abicheck/abicheck@v1",
        )
        assert report["verdict"] == NEW_TARGET_VERDICT
        assert report["verdict"] != BOOTSTRAP_VERDICT
        assert report["verdict"] not in {
            "NO_CHANGE",
            "COMPATIBLE",
            "COMPATIBLE_WITH_RISK",
            "API_BREAK",
            "BREAKING",
            "ERROR",
        }
        assert report["baseline_new_target"] is True
        assert "baseline_bootstrap" not in report
        assert "compatibility_verdict" not in report
        assert report["check_evidence_coverage"]["state"] == "new_target"
        assert report["operational_errors"] == []
        assert report["policy_gate_decision"] == "pass"
        assert report["message"].startswith("target 'libnew'")
        assert report["project"] == "epics-base/pvxs"
        assert report["tool_version"] == "abicheck 0.x.y"

    def test_optional_fields_omitted_when_not_given(self):
        report = build_new_target_report(
            name="libnew",
            profile_id="p",
            baseline_channel="release-contract",
            requested_depth="source",
            resolve_message="target 'libnew' is not in this baseline-set's manifest.",
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
