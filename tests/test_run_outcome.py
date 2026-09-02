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

"""ADR-063 Phase 7: ``RunOutcome`` and the aggregate/scan read boundary.

Covers ``policy/outcome.py``'s domain type itself, ``workflows/aggregate/
gate.py``'s structured-first reading (with the legacy decode as the named
fallback), the three synthetic report builders, the two
``buildsource/check_report.py`` mutators (``_neutralize_gate``/
``_escalate_removed_library_severity``), the three scan writers, and the
schema/``not_comparable`` writer surface.
"""

from __future__ import annotations

import copy
import json

import jsonschema
import pytest

from abicheck.buildsource.check_report import (
    augment_report,
    build_bootstrap_report,
    build_new_target_report,
    build_operational_error_report,
)
from abicheck.policy.outcome import (
    OperationalStatus,
    PolicyGateDecision,
    RunOutcome,
    TargetLifecycle,
    fold_gate_and_operational,
    policy_gate_decision_for_exit_code,
    run_outcome_for_scan_fields,
)
from abicheck.workflows.aggregate.gate import GateInfo

# ---------------------------------------------------------------------------
# RunOutcome / PolicyGateDecision / OperationalStatus domain behavior
# ---------------------------------------------------------------------------


class TestPolicyGateDecisionOrdering:
    @pytest.mark.parametrize(
        "code,gate",
        [
            (0, PolicyGateDecision.NONE),
            (1, PolicyGateDecision.ADDITION_QUALITY),
            (2, PolicyGateDecision.POTENTIAL_BREAKING),
            (4, PolicyGateDecision.ABI_BREAKING),
        ],
    )
    def test_exit_code_round_trips_through_gate(self, code, gate):
        assert policy_gate_decision_for_exit_code(code) is gate
        assert (
            policy_gate_decision_for_exit_code(code) == gate
            and gate.value
            and gate  # non-empty enum member
        )
        from abicheck.policy.outcome import policy_gate_decision_exit_code

        assert policy_gate_decision_exit_code(gate) == code

    def test_unrecognized_code_fails_safe_to_abi_breaking(self):
        assert policy_gate_decision_for_exit_code(3) is PolicyGateDecision.ABI_BREAKING


class TestFoldGateAndOperational:
    def test_none_and_none_is_zero(self):
        assert fold_gate_and_operational(PolicyGateDecision.NONE, OperationalStatus.NONE) == 0

    @pytest.mark.parametrize(
        "operational",
        [
            OperationalStatus.BUDGET_OVERFLOW,
            OperationalStatus.NOT_COMPARABLE,
            OperationalStatus.EVIDENCE_CONTRACT_ERROR,
            OperationalStatus.EXTRACTION_ERROR,
        ],
    )
    def test_operational_failure_never_masked_by_a_clean_gate(self, operational):
        assert fold_gate_and_operational(PolicyGateDecision.NONE, operational) == 1

    def test_operational_never_lowers_a_worse_gate(self):
        assert (
            fold_gate_and_operational(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.BUDGET_OVERFLOW
            )
            == 4
        )


class TestRunOutcomeDictRoundTrip:
    def test_to_dict_from_dict_round_trip(self):
        outcome = RunOutcome(
            compatibility=None,
            assurance=None,
            gate=PolicyGateDecision.ABI_BREAKING,
            operational=OperationalStatus.NONE,
            lifecycle=TargetLifecycle.BOOTSTRAP,
        )
        data = outcome.to_dict()
        restored = RunOutcome.from_dict(data)
        assert restored is not None
        assert restored.gate is PolicyGateDecision.ABI_BREAKING
        assert restored.operational is OperationalStatus.NONE
        assert restored.lifecycle is TargetLifecycle.BOOTSTRAP
        assert restored.compatibility is None

    def test_from_dict_none_on_absent_or_malformed(self):
        assert RunOutcome.from_dict(None) is None
        assert RunOutcome.from_dict("not a dict") is None
        assert RunOutcome.from_dict({}) is None
        assert RunOutcome.from_dict({"gate": "not_a_real_value", "operational": "none"}) is None

    def test_from_dict_defaults_lifecycle_to_existing_when_absent(self):
        restored = RunOutcome.from_dict({"gate": "none", "operational": "none"})
        assert restored is not None
        assert restored.lifecycle is TargetLifecycle.EXISTING


class TestRunOutcomeForScanFields:
    def test_ordinary_compatible_verdict(self):
        outcome = run_outcome_for_scan_fields("COMPATIBLE", 0)
        assert outcome.compatibility is not None and outcome.compatibility.value == "COMPATIBLE"
        assert outcome.gate is PolicyGateDecision.NONE
        assert outcome.operational is OperationalStatus.NONE

    def test_budget_overflow_verdict_and_exit_5(self):
        outcome = run_outcome_for_scan_fields("BUDGET_OVERFLOW", 5)
        assert outcome.compatibility is None
        assert outcome.operational is OperationalStatus.BUDGET_OVERFLOW
        assert outcome.gate is PolicyGateDecision.NONE

    def test_exit_6_maps_to_not_comparable_even_without_the_sentinel_verdict(self):
        outcome = run_outcome_for_scan_fields("NO_CHANGE_BUT_ACTUALLY_ABORTED", 6)
        assert outcome.operational is OperationalStatus.NOT_COMPARABLE

    def test_severity_exit_code_preferred_over_top_level(self):
        # Top-level exit_code=1 already folds an orthogonal contribution;
        # the nested severity exit_code is the real compatibility-only value.
        outcome = run_outcome_for_scan_fields("COMPATIBLE", 1, severity_exit_code=2)
        assert outcome.gate is PolicyGateDecision.POTENTIAL_BREAKING

    def test_legacy_scheme_coverage_only_exit_1_is_not_a_compatibility_gate(self):
        """Codex review (P1): under the legacy scan scheme (no
        severity_exit_code), a raw top-level exit_code of 1 is ambiguous --
        legacy scan's own native codes are 0/2/4/5/6, so a bare 1 can only be
        ADR-049 Phase 7's orthogonal contract-coverage contribution folded
        onto an otherwise-compatible 0. Confirmed via the report's own
        declared contribution, the gate must read NONE, not
        ADDITION_QUALITY -- mirroring GateInfo.from_scan_report's own
        identical raw-code special case (COVERAGE_INCOMPLETE_EXIT branch)."""
        outcome = run_outcome_for_scan_fields(
            "COMPATIBLE", 1, contract_coverage_contribution=1,
        )
        assert outcome.gate is PolicyGateDecision.NONE
        assert outcome.operational is OperationalStatus.NONE

    def test_legacy_scheme_exit_1_without_declared_coverage_stays_blocking(self):
        """Fail-closed counterpart: an undeclared/unconfirmed contribution
        must not be assumed to be coverage-only -- the gate stays whatever
        the raw code says, exactly like the reader this mirrors."""
        outcome = run_outcome_for_scan_fields("COMPATIBLE", 1)
        assert outcome.gate is PolicyGateDecision.ADDITION_QUALITY

    def test_legacy_scheme_real_break_unaffected_by_coverage_contribution(self):
        # A genuine break (2/4) must never be zeroed by an orthogonal
        # coverage contribution riding along on the same folded exit code.
        outcome = run_outcome_for_scan_fields(
            "API_BREAK", 2, contract_coverage_contribution=1,
        )
        assert outcome.gate is PolicyGateDecision.POTENTIAL_BREAKING

    def test_severity_scheme_ignores_coverage_contribution_entirely(self):
        # Under the severity scheme, the nested severity_exit_code is
        # already compatibility-only -- the coverage-only special case must
        # never fire there (it isn't ambiguous in the first place).
        outcome = run_outcome_for_scan_fields(
            "COMPATIBLE", 1, severity_exit_code=1, contract_coverage_contribution=1,
        )
        assert outcome.gate is PolicyGateDecision.ADDITION_QUALITY

    def test_member_evidence_contract_error_folds_in_when_operational_is_none(self):
        """Codex review (P2): ScanSetResult's own _aggregate_scan_set_verdict
        lets a stronger member's API_BREAK/BREAKING win the reported
        verdict/exit_code over a *different* member's own
        EVIDENCE_CONTRACT_ERROR -- without member_evidence_contract_error,
        that member's abort has no signal left in run_outcome at all."""
        outcome = run_outcome_for_scan_fields(
            "API_BREAK", 2, member_evidence_contract_error=True,
        )
        assert outcome.gate is PolicyGateDecision.POTENTIAL_BREAKING
        assert outcome.operational is OperationalStatus.EVIDENCE_CONTRACT_ERROR

    def test_member_evidence_contract_error_never_overrides_a_derived_operational(self):
        # A set-level BUDGET_OVERFLOW already dominates every member per
        # _aggregate_scan_set_verdict's own step 1 -- the member flag must
        # never override an operational status already derived from
        # verdict/exit_code.
        outcome = run_outcome_for_scan_fields(
            "BUDGET_OVERFLOW", 5, member_evidence_contract_error=True,
        )
        assert outcome.operational is OperationalStatus.BUDGET_OVERFLOW

    def test_abort_report_compatibility_contribution_preferred_over_top_level(self):
        """Codex review (P1/P2): a late BUDGET_OVERFLOW that already found a
        real ABI break must preserve that gate, not zero it out just
        because the abort's own top-level exit code (5) falls outside the
        0/1/2/4 compatibility scheme."""
        from abicheck.policy.outcome import run_outcome_dict_for_scan

        report = {
            "scan_schema_version": "1.24",
            "exit": {"code": 5, "compatibility_contribution": 4},
        }
        outcome = run_outcome_dict_for_scan("BUDGET_OVERFLOW", 5, report=report)
        assert outcome["gate"] == "abi_breaking"
        assert outcome["operational"] == "budget_overflow"

    def test_evidence_contract_error_abort_reads_persisted_compatibility(self):
        from abicheck.policy.outcome import run_outcome_dict_for_scan

        report = {
            "scan_schema_version": "1.24",
            "exit": {"code": 1, "compatibility_contribution": 0},
        }
        outcome = run_outcome_dict_for_scan(
            "EVIDENCE_CONTRACT_ERROR", 1, report=report,
        )
        # Not addition_quality: the abort report's own persisted
        # compatibility_contribution (0) is authoritative, not the
        # abort's unrelated top-level exit code.
        assert outcome["gate"] == "none"
        assert outcome["operational"] == "evidence_contract_error"

    def test_bundle_incomplete_is_operational_not_a_compatibility_gate(self):
        """Codex review (P2): ScanSetResult.run_scan_set's own
        BUNDLE_INCOMPLETE verdict floors exit_code at 1 with no report= to
        read a real compatibility contribution from -- that floor must never
        read as a real ADDITION_QUALITY compatibility gate, mirroring
        `compatibility` already being None for the identical reason (the
        verdict string itself carries no real compatibility meaning)."""
        outcome = run_outcome_for_scan_fields("BUNDLE_INCOMPLETE", 1)
        assert outcome.compatibility is None
        assert outcome.gate is PolicyGateDecision.NONE
        assert outcome.operational is OperationalStatus.EXTRACTION_ERROR

    def test_bundle_incomplete_membership_does_not_leak_to_ordinary_verdicts(self):
        # The BUNDLE_INCOMPLETE zeroing keys off *verdict* membership, not
        # operational's derived value -- it must never fire for an ordinary
        # verdict that merely also carries member_evidence_contract_error.
        outcome = run_outcome_for_scan_fields(
            "API_BREAK", 2, member_evidence_contract_error=True,
        )
        assert outcome.gate is PolicyGateDecision.POTENTIAL_BREAKING


# ---------------------------------------------------------------------------
# GateInfo structured-first reading (workflows/aggregate/gate.py)
# ---------------------------------------------------------------------------


class TestGateInfoFromReportDataStructuredFirst:
    def _run_outcome_block(self, gate: PolicyGateDecision, operational: OperationalStatus):
        return RunOutcome(
            compatibility=None, assurance=None, gate=gate, operational=operational
        ).to_dict()

    def test_legacy_severity_block_and_structured_fields_agree(self):
        """A fresh report carries both -- reading structured-first must not
        disagree with the legacy severity block for the same report."""
        legacy_only = {
            "severity": {
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            }
        }
        both = {
            **legacy_only,
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
            ),
        }
        legacy_gate = GateInfo.from_report_data(legacy_only)
        structured_gate = GateInfo.from_report_data(both)
        assert legacy_gate is not None and structured_gate is not None
        assert legacy_gate.exit_code == structured_gate.exit_code == 4
        assert legacy_gate.blocking == structured_gate.blocking is True

    def test_structured_fields_alone_read_without_a_severity_block(self):
        """A legacy-scheme compare report (no severity_config, hence no
        `severity` block) still carries `run_outcome` -- from_report_data
        must resolve it directly rather than falling through to None."""
        data = {
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.POTENTIAL_BREAKING, OperationalStatus.NONE
            )
        }
        gate = GateInfo.from_report_data(data)
        assert gate is not None
        assert gate.exit_code == 2
        assert gate.blocking is True

    def test_no_severity_and_no_run_outcome_is_none(self):
        assert GateInfo.from_report_data({}) is None

    def test_present_but_malformed_run_outcome_fails_closed(self):
        """Codex review (P2): a present-but-unparseable run_outcome must not
        be treated the same as an absent one -- that would let a corrupt,
        policy-blocked report silently fall through to the (possibly
        greener) legacy verdict path instead of failing the target
        unavailable, exactly the defect the severity block's own
        _MalformedGate handling already guards against."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        data = {"run_outcome": {"gate": "not_a_real_value", "operational": "none"}}
        with pytest.raises(_MalformedGate):
            GateInfo.from_report_data(data)

    def test_gate_contradicting_severity_fails_closed(self):
        """Codex review (P2): both blocks individually parse, but disagree
        on the compatibility axis itself (severity says clean, run_outcome
        says abi_breaking) -- must fail closed rather than silently trust
        the greener severity block."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        data = {
            "severity": {"exit_code": 0, "blocking": False, "blocking_categories": []},
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
            ),
        }
        with pytest.raises(_MalformedGate):
            GateInfo.from_report_data(data)

    def test_scoped_gate_divergence_from_severity_is_not_a_contradiction(self):
        """The scoped-gate case (Codex review, fresh evidence following the
        contradiction check above): a --used-by/--required-symbol report's
        severity.exit_code is scoped_exit_code (already folded with the
        orthogonal contract-coverage/analysis-assurance floors), while
        run_outcome.gate is derived from scoped_compatibility_contribution
        (deliberately pre-fold, compatibility-only, per D6's axis
        separation) -- the two legitimately differ whenever a coverage/
        assurance floor applies. full_run_outcome's presence (only set by
        cli_compare_fold._swap_in_scoped_run_outcome) is what must exempt
        this from the contradiction check, not silently coincide with it."""
        data = {
            "severity": {
                "exit_code": 1,
                "blocking": True,
                "blocking_categories": ["contract_coverage"],
            },
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.NONE, OperationalStatus.NONE
            ),
            "full_run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
            ),
        }
        gate = GateInfo.from_report_data(data)
        assert gate is not None
        assert gate.exit_code == 1
        assert gate.blocking is True

    def test_operational_failure_folds_into_an_otherwise_clean_severity_block(self):
        """The orthogonal-axes fold: RunOutcome.operational raises an
        otherwise-clean severity gate, exactly the shape ADR-049 Phase 7's
        contract-coverage axis already uses."""
        data = {
            "severity": {"exit_code": 0, "blocking": False, "blocking_categories": []},
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.NONE, OperationalStatus.EVIDENCE_CONTRACT_ERROR
            ),
        }
        gate = GateInfo.from_report_data(data)
        assert gate is not None
        assert gate.exit_code == 1
        assert gate.blocking is True
        assert "evidence_contract_error" in gate.blocking_categories

    def test_operational_never_lowers_a_worse_severity_block(self):
        data = {
            "severity": {
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            },
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.BUDGET_OVERFLOW
            ),
        }
        gate = GateInfo.from_report_data(data)
        assert gate is not None
        assert gate.exit_code == 4


class TestGateInfoFromScanReportStructuredFirst:
    def _scan_report(self, verdict: str, exit_code: int, run_outcome: dict) -> dict:
        return {
            "scan_schema_version": "1.24",
            "verdict": verdict,
            "exit_code": exit_code,
            "run_outcome": run_outcome,
        }

    def test_budget_overflow_exit_5_still_blocks(self):
        """Explicitly named by the Phase 7 plan: PolicyGateDecision alone
        cannot represent scan's budget-overflow/not-comparable exits, so a
        gate.py reader that folds only .gate and ignores .operational would
        wrongly read this as non-blocking."""
        run_outcome = RunOutcome(
            compatibility=None,
            assurance=None,
            gate=PolicyGateDecision.NONE,
            operational=OperationalStatus.BUDGET_OVERFLOW,
        ).to_dict()
        report = self._scan_report("BUDGET_OVERFLOW", 5, run_outcome)
        gate = GateInfo.from_scan_report(report)
        assert gate is not None
        assert gate.blocking is True
        assert gate.exit_code == 1

    def test_not_comparable_exit_6_still_blocks(self):
        run_outcome = RunOutcome(
            compatibility=None,
            assurance=None,
            gate=PolicyGateDecision.NONE,
            operational=OperationalStatus.NOT_COMPARABLE,
        ).to_dict()
        report = self._scan_report("NOT_COMPARABLE", 6, run_outcome)
        gate = GateInfo.from_scan_report(report)
        assert gate is not None
        assert gate.blocking is True
        assert gate.exit_code == 1

    def test_structured_path_is_actually_taken_not_the_legacy_fallback(self):
        """Decisive proof the structured reader ran: the report carries no
        usable top-level `exit_code` at all (a value the legacy raw-code
        path would reject with `_MalformedGate`), yet the structured
        `run_outcome` alone is enough for from_scan_report to resolve
        cleanly."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        run_outcome = RunOutcome(
            compatibility=None,
            assurance=None,
            gate=PolicyGateDecision.ABI_BREAKING,
            operational=OperationalStatus.NONE,
        ).to_dict()
        report = {
            "scan_schema_version": "1.24",
            "verdict": "BREAKING",
            "run_outcome": run_outcome,
            # No "exit_code" key at all.
        }
        gate = GateInfo.from_scan_report(report)
        assert gate is not None and gate.exit_code == 4
        # And confirm the *absence* really would have broken the legacy path.
        del report["run_outcome"]
        with pytest.raises(_MalformedGate):
            GateInfo.from_scan_report(report)

    def test_present_but_malformed_run_outcome_fails_closed(self):
        """Codex review (P2), scan-report counterpart to the identical
        compare-report test above."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        report = self._scan_report(
            "COMPATIBLE", 0, {"gate": "bogus", "operational": "none"},
        )
        with pytest.raises(_MalformedGate):
            GateInfo.from_scan_report(report)

    def test_severity_scheme_scan_cross_checks_top_level_run_outcome(self):
        """Codex review (P2), fresh evidence beyond the compare-report
        contradiction fix: a severity-scheme scan's diff.severity gate is
        read via a *nested* from_report_data call that has no run_outcome
        key of its own (it lives at the outer scan envelope's top level) --
        without folding/cross-checking it separately, a scan report's own
        top-level run_outcome was never consulted at all, so a corrupted
        diff.severity.exit_code: 0 alongside run_outcome.gate: abi_breaking
        (or even an outright invalid gate value) was silently accepted as
        nonblocking, unlike the equivalent compare report."""
        from abicheck.change_registry_types import Verdict
        from abicheck.workflows.aggregate.gate import _MalformedGate

        contradicting = {
            "scan_schema_version": "1.24",
            "diff": {
                "severity": {"exit_code": 0, "blocking": False, "blocking_categories": []}
            },
            "run_outcome": RunOutcome(
                compatibility=None,
                assurance=None,
                gate=PolicyGateDecision.ABI_BREAKING,
                operational=OperationalStatus.NONE,
            ).to_dict(),
        }
        with pytest.raises(_MalformedGate):
            GateInfo.from_scan_report(contradicting)

        invalid_gate = {
            "scan_schema_version": "1.24",
            "diff": {
                "severity": {"exit_code": 0, "blocking": False, "blocking_categories": []}
            },
            "run_outcome": {
                "schema_version": "1.0", "compatibility": None, "assurance": None,
                "gate": "bogus", "operational": "none",
            },
        }
        with pytest.raises(_MalformedGate):
            GateInfo.from_scan_report(invalid_gate)

        # An agreeing report must still resolve cleanly -- this isn't a
        # blanket rejection of every severity-scheme scan's run_outcome.
        agreeing = {
            "scan_schema_version": "1.24",
            "diff": {
                "severity": {
                    "exit_code": 4, "blocking": True, "blocking_categories": ["abi_breaking"],
                }
            },
            "run_outcome": RunOutcome(
                compatibility=Verdict("BREAKING"),
                assurance=None,
                gate=PolicyGateDecision.ABI_BREAKING,
                operational=OperationalStatus.NONE,
            ).to_dict(),
        }
        gate = GateInfo.from_scan_report(agreeing)
        assert gate is not None
        assert gate.exit_code == 4
        assert gate.blocking is True


# ---------------------------------------------------------------------------
# The three synthetic report builders
# ---------------------------------------------------------------------------


class TestSyntheticBuilderRunOutcome:
    def test_operational_error_report(self):
        report = build_operational_error_report(
            name="libfoo",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            resolve_outcome="dump_failed",
            resolve_message="boom",
        )
        outcome = report["run_outcome"]
        assert outcome["operational"] == "extraction_error"
        assert outcome["compatibility"] is None
        assert outcome["lifecycle"] == "existing"

    def test_bootstrap_report(self):
        report = build_bootstrap_report(
            name="libfoo",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            resolve_message="no baseline yet",
        )
        outcome = report["run_outcome"]
        assert outcome["lifecycle"] == "bootstrap"
        assert outcome["operational"] == "none"
        assert outcome["compatibility"] is None

    def test_new_target_report(self):
        report = build_new_target_report(
            name="libfoo",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            resolve_message="new target",
        )
        outcome = report["run_outcome"]
        assert outcome["lifecycle"] == "new_target"
        assert outcome["operational"] == "none"
        assert outcome["compatibility"] is None


# ---------------------------------------------------------------------------
# _neutralize_gate / _escalate_removed_library_severity
# ---------------------------------------------------------------------------


def _compare_report_with_run_outcome(gate: PolicyGateDecision, operational: OperationalStatus):
    exit_code = fold_gate_and_operational(gate, operational)
    return {
        "report_schema_version": "2.48",
        "library": "libpvxs",
        "verdict": "BREAKING" if gate is PolicyGateDecision.ABI_BREAKING else "COMPATIBLE",
        "severity": {
            "config": {},
            "categories": {},
            "exit_code": exit_code,
            "blocking": exit_code != 0,
            "blocking_categories": ["abi_breaking"] if gate is PolicyGateDecision.ABI_BREAKING else [],
        },
        "run_outcome": RunOutcome(
            compatibility=None, assurance=None, gate=gate, operational=operational
        ).to_dict(),
    }


class TestNeutralizeGateRunOutcomeAxis:
    def test_advisory_neutralizes_a_blocking_gate_with_no_operational_failure(self):
        report = _compare_report_with_run_outcome(
            PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
        )
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["run_outcome"]["gate"] == "none"
        gate = GateInfo.from_report_data(out)
        assert gate is not None and gate.blocking is False

    def test_advisory_never_neutralizes_a_real_operational_failure(self):
        """The rejected first draft of this fix zeroed .operational too --
        exactly the class of bug _neutralize_gate's own history keeps
        rediscovering, on the opposite axis this time."""
        report = _compare_report_with_run_outcome(
            PolicyGateDecision.NONE, OperationalStatus.EVIDENCE_CONTRACT_ERROR
        )
        out = augment_report(
            report,
            name="libpvxs",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
        )
        assert out["run_outcome"]["operational"] == "evidence_contract_error"
        gate = GateInfo.from_report_data(out)
        assert gate is not None and gate.blocking is True


class TestEscalateRemovedLibraryRunOutcomeAxis:
    def test_exit_8_escalates_run_outcome_gate_end_to_end(self):
        """Reproduces the exit-8 path through augment_report() itself (not a
        hand-built dict), confirmed to fail against a version of
        _escalate_removed_library_severity() that writes only the legacy
        block: GateInfo.from_report_data must read a blocking result from
        the escalated report under every gate mode except advisory."""
        report = _compare_report_with_run_outcome(
            PolicyGateDecision.NONE, OperationalStatus.NONE
        )
        report["verdict"] = "COMPATIBLE_WITH_RISK"
        for gate_mode in ("local", "deferred"):
            out = augment_report(
                copy.deepcopy(report),
                name="libpvxs-bundle",
                profile_id="p",
                baseline_channel="c",
                requested_depth="headers",
                gate_mode=gate_mode,
                analysis_exit_code=8,
            )
            assert out["run_outcome"]["gate"] == "abi_breaking"
            gate = GateInfo.from_report_data(out)
            assert gate is not None and gate.blocking is True and gate.exit_code == 4

    def test_advisory_still_neutralizes_the_escalated_gate(self):
        report = _compare_report_with_run_outcome(
            PolicyGateDecision.NONE, OperationalStatus.NONE
        )
        report["verdict"] = "COMPATIBLE_WITH_RISK"
        out = augment_report(
            report,
            name="libpvxs-bundle",
            profile_id="p",
            baseline_channel="c",
            requested_depth="headers",
            gate_mode="advisory",
            analysis_exit_code=8,
        )
        # Escalation happens before neutralization in augment_report(), and
        # advisory zeroes .gate unconditionally afterward -- an explicitly
        # advisory check must still gate nothing.
        assert out["run_outcome"]["gate"] == "none"


# ---------------------------------------------------------------------------
# Scan writers: ScanOutcome.to_dict / ScanResult.to_dict / ScanSetResult.to_dict
# ---------------------------------------------------------------------------


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
                artifact=Path("a.so"), result=ScanResult(verdict="API_BREAK", exit_code=2),
            ),
            ScanArtifactResult(
                artifact=Path("b.so"),
                result=ScanResult(verdict="EVIDENCE_CONTRACT_ERROR", exit_code=1),
            ),
        ]
        result = ScanSetResult(
            verdict="API_BREAK", exit_code=2, per_artifact=per_artifact,
        )
        report = result.to_dict()
        assert report["run_outcome"]["gate"] == "potential_breaking"
        assert report["run_outcome"]["operational"] == "evidence_contract_error"

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
            verdict="API_BREAK", exit_code=2, bundle_incomplete=True,
        )
        report = result.to_dict()
        assert report["run_outcome"]["gate"] == "potential_breaking"
        assert report["run_outcome"]["operational"] == "extraction_error"

    def test_scan_set_result_bundle_incomplete_end_to_end(self):
        """Codex review (P2), end-to-end: run_scan_set's own BUNDLE_INCOMPLETE
        verdict/exit_code=1 must not read as a real compatibility gate."""
        from abicheck.service_scan import ScanSetResult

        result = ScanSetResult(
            verdict="BUNDLE_INCOMPLETE", exit_code=1, bundle_incomplete=True,
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


# ---------------------------------------------------------------------------
# Release fan-out (_format_release_json / _write_release_summary_file)
# ---------------------------------------------------------------------------


class TestReleaseJsonRunOutcome:
    def test_format_release_json_carries_run_outcome(self):
        """Codex review (P2): docs/use/output-formats.md documents that
        every JSON report carries run_outcome, including 'the release
        fan-out' -- _format_release_json never actually built one."""
        import json
        from pathlib import Path

        from abicheck.cli_compare_release_helpers import _format_release_json

        out = _format_release_json(
            "BREAKING", Path("/o"), Path("/n"),
            [{"library": "libfoo.so", "verdict": "BREAKING"}],
            [], [], {}, {}, [], None, None,
        )
        data = json.loads(out)
        assert "run_outcome" in data
        assert data["run_outcome"]["gate"] == "abi_breaking"
        assert data["run_outcome"]["compatibility"] == "BREAKING"

    def test_format_release_json_run_outcome_surfaces_operational_error(self):
        """A library that failed to dump/extract/compare (verdict ERROR) is
        an operational failure, not a compatibility-gate finding --
        run_outcome must say so via .operational, matching OperationalStatus.
        EXTRACTION_ERROR's own documented grounding."""
        import json
        from pathlib import Path

        from abicheck.cli_compare_release_helpers import _format_release_json

        out = _format_release_json(
            "ERROR", Path("/o"), Path("/n"),
            [{"library": "libfoo.so", "verdict": "ERROR"}],
            [], [], {}, {}, [], None, None,
        )
        data = json.loads(out)
        assert data["run_outcome"]["operational"] == "extraction_error"

    def test_removed_library_escalation_agrees_with_severity_block(self):
        """Codex review (P2), fresh evidence: a severity-scheme release
        using --fail-on-removed-library escalates run_outcome.gate to
        abi_breaking even when ordinary findings contribute 0, but the
        severity block emitted alongside it must escalate too (mirroring
        buildsource/check_report.py's _escalate_removed_library_severity) --
        otherwise GateInfo.from_report_data's own severity/run_outcome
        contradiction check (this same PR) rejects this exact, legitimate
        report as corrupt (verified failing before this fix: severity.
        exit_code stayed 0 while run_outcome.gate read abi_breaking)."""
        import json
        from pathlib import Path

        from abicheck.cli_compare_release_helpers import _format_release_json
        from abicheck.severity import resolve_severity_config
        from abicheck.workflows.aggregate.gate import GateInfo

        cfg = resolve_severity_config("default")
        out = _format_release_json(
            "COMPATIBLE", Path("/o"), Path("/n"),
            [{"library": "libfoo.so", "verdict": "COMPATIBLE"}],
            ["libfoo.so"], [], {"libfoo.so": Path("/o/libfoo.so")}, {}, [],
            None, None,
            severity_config=cfg,
            severity_exit_code=0,
            fail_on_removed=True,
        )
        data = json.loads(out)
        assert data["severity"]["exit_code"] == 4
        assert data["run_outcome"]["gate"] == "abi_breaking"
        gate = GateInfo.from_report_data(data)  # must not raise _MalformedGate
        assert gate is not None
        assert gate.exit_code == 4
        assert gate.blocking is True

    def test_write_release_summary_file_carries_run_outcome(self, tmp_path):
        """Codex review (P2): the --output-dir sibling of
        _format_release_json never built a run_outcome either -- the
        identical gap PR #803 already fixed for effective_config_digest on
        this same sibling document."""
        import json

        from abicheck.cli_compare_release import _write_release_summary_file

        _write_release_summary_file(
            tmp_path, "BREAKING",
            [{"library": "libfoo.so", "verdict": "BREAKING"}],
            [], [], {}, {},
        )
        data = json.loads((tmp_path / "summary.json").read_text())
        assert data["run_outcome"]["gate"] == "abi_breaking"


# ---------------------------------------------------------------------------
# not_comparable_document / render_not_comparable_json
# ---------------------------------------------------------------------------


class TestNotComparableRunOutcome:
    def test_not_comparable_document_carries_the_required_operational_axis(self):
        from abicheck.policy.outcome import OperationalStatus as _OperationalStatus
        from abicheck.report.not_comparable import not_comparable_document

        doc = not_comparable_document(
            "libfoo",
            "1.0",
            "2.0",
            "profile_mismatch",
            "profiles differ",
            report_schema_version="2.48",
            operational=_OperationalStatus.NOT_COMPARABLE,
        )
        data = doc.to_mapping()
        assert data["run_outcome"]["operational"] == "not_comparable"
        assert data["verdict"] is None

    def test_operational_is_a_required_keyword_argument(self):
        from abicheck.report.not_comparable import not_comparable_document

        with pytest.raises(TypeError):
            not_comparable_document(  # type: ignore[call-arg]
                "libfoo", "1.0", "2.0", "profile_mismatch", "profiles differ",
                report_schema_version="2.48",
            )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestRunOutcomeSchemaValidation:
    def test_fresh_compare_report_validates_against_the_published_schema_mirror(self):
        from pathlib import Path

        from abicheck import reporter
        from abicheck.analysis_assurance import compute_analysis_assurance
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo", version="1.0")
        new = AbiSnapshot(library="libfoo", version="1.1")
        result = DiffResult(
            library="libfoo", old_version="1.0", new_version="1.1", verdict=Verdict.NO_CHANGE
        )
        result.analysis_assurance = compute_analysis_assurance(result, old, new)
        text = reporter.to_json(result)
        data = json.loads(text)
        assert "run_outcome" in data

        schema_path = (
            Path(__file__).resolve().parent.parent
            / "docs"
            / "reference"
            / "schemas"
            / "v1"
            / "compare_report.schema.json"
        )
        schema = json.loads(schema_path.read_text())
        jsonschema.validate(data, schema)
