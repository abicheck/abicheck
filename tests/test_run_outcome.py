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
        assert (
            fold_gate_and_operational(PolicyGateDecision.NONE, OperationalStatus.NONE)
            == 0
        )

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
        assert (
            RunOutcome.from_dict({"gate": "not_a_real_value", "operational": "none"})
            is None
        )

    def test_from_dict_defaults_lifecycle_to_existing_when_absent(self):
        restored = RunOutcome.from_dict({"gate": "none", "operational": "none"})
        assert restored is not None
        assert restored.lifecycle is TargetLifecycle.EXISTING


class TestRunOutcomeDictForDiffResultReusesGate:
    def test_uses_the_passed_gate_rather_than_recomputing(self):
        """Codex review (P1), fresh evidence: run_outcome_dict_for_diff_
        result used to call gate_decision_for_result itself -- a second,
        independent policy evaluation during rendering that could drift
        from the severity block's own gate. It must now read the caller's
        already-computed GateDecision instead. A deliberately wrong
        `gate.exit_code` (disagreeing with what the real severity config
        would compute) proves the passed value is what's actually used."""
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.policy.severity import GateDecision
        from abicheck.report.run_outcome import run_outcome_dict_for_diff_result

        result = DiffResult(
            library="libfoo",
            old_version="1.0",
            new_version="1.1",
            verdict=Verdict.NO_CHANGE,
        )
        fake_gate = GateDecision(
            scheme="severity",
            exit_code=4,
            blocking=True,
            blocking_categories=("abi_breaking",),
        )
        out = run_outcome_dict_for_diff_result(result, None, fake_gate)
        assert out["gate"] == "abi_breaking"

    def test_none_gate_falls_back_to_the_legacy_verdict_mapping(self):
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.report.run_outcome import run_outcome_dict_for_diff_result

        result = DiffResult(
            library="libfoo",
            old_version="1.0",
            new_version="1.1",
            verdict=Verdict.BREAKING,
        )
        out = run_outcome_dict_for_diff_result(result, None, None)
        assert out["gate"] == "abi_breaking"


class TestRunOutcomeForScanFields:
    def test_ordinary_compatible_verdict(self):
        outcome = run_outcome_for_scan_fields("COMPATIBLE", 0)
        assert (
            outcome.compatibility is not None
            and outcome.compatibility.value == "COMPATIBLE"
        )
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
            "COMPATIBLE",
            1,
            contract_coverage_contribution=1,
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
            "API_BREAK",
            2,
            contract_coverage_contribution=1,
        )
        assert outcome.gate is PolicyGateDecision.POTENTIAL_BREAKING

    def test_severity_scheme_ignores_coverage_contribution_entirely(self):
        # Under the severity scheme, the nested severity_exit_code is
        # already compatibility-only -- the coverage-only special case must
        # never fire there (it isn't ambiguous in the first place).
        outcome = run_outcome_for_scan_fields(
            "COMPATIBLE",
            1,
            severity_exit_code=1,
            contract_coverage_contribution=1,
        )
        assert outcome.gate is PolicyGateDecision.ADDITION_QUALITY

    def test_member_evidence_contract_error_folds_in_when_operational_is_none(self):
        """Codex review (P2): ScanSetResult's own _aggregate_scan_set_verdict
        lets a stronger member's API_BREAK/BREAKING win the reported
        verdict/exit_code over a *different* member's own
        EVIDENCE_CONTRACT_ERROR -- without member_evidence_contract_error,
        that member's abort has no signal left in run_outcome at all."""
        outcome = run_outcome_for_scan_fields(
            "API_BREAK",
            2,
            member_evidence_contract_error=True,
        )
        assert outcome.gate is PolicyGateDecision.POTENTIAL_BREAKING
        assert outcome.operational is OperationalStatus.EVIDENCE_CONTRACT_ERROR

    def test_member_evidence_contract_error_never_overrides_a_derived_operational(self):
        # A set-level BUDGET_OVERFLOW already dominates every member per
        # _aggregate_scan_set_verdict's own step 1 -- the member flag must
        # never override an operational status already derived from
        # verdict/exit_code.
        outcome = run_outcome_for_scan_fields(
            "BUDGET_OVERFLOW",
            5,
            member_evidence_contract_error=True,
        )
        assert outcome.operational is OperationalStatus.BUDGET_OVERFLOW

    def test_late_abort_preserves_the_prior_break_verdict_not_just_the_gate(self):
        """Codex review (P2), fresh evidence beyond the abort-gate fix
        above: a late BUDGET_OVERFLOW that already found a real BREAKING
        comparison must not report compatibility=None alongside
        gate=abi_breaking -- that contradicts the documented rule that null
        means nothing was compared. A persisted compatibility_contribution
        of 2/4 is unambiguous and must be carried through as the matching
        real Verdict."""
        outcome = run_outcome_for_scan_fields(
            "BUDGET_OVERFLOW", 5, severity_exit_code=4
        )
        assert outcome.compatibility is not None
        assert outcome.compatibility.value == "BREAKING"
        assert outcome.gate is PolicyGateDecision.ABI_BREAKING

        outcome2 = run_outcome_for_scan_fields(
            "EVIDENCE_CONTRACT_ERROR", 1, severity_exit_code=2
        )
        assert outcome2.compatibility is not None
        assert outcome2.compatibility.value == "API_BREAK"

    def test_late_abort_with_a_clean_prior_contribution_stays_ambiguous(self):
        # A 0 contribution can't be told apart from NO_CHANGE/COMPATIBLE/
        # COMPATIBLE_WITH_RISK -- must stay None, unlike the 2/4 case above.
        outcome = run_outcome_for_scan_fields(
            "BUDGET_OVERFLOW", 5, severity_exit_code=0
        )
        assert outcome.compatibility is None

    def test_assurance_block_threaded_through_from_scan_outcome(self):
        """Codex review (P2), fresh evidence: every scan writer passed no
        assurance at all, so the independent assurance axis always read
        None even when the report's own diff.analysis_assurance block was
        fully computed."""
        from abicheck.policy.outcome import run_outcome_dict_for_scan_outcome

        aa_block = {"schema_version": "1.0", "status": "complete"}
        outcome = run_outcome_dict_for_scan_outcome(
            "COMPATIBLE", 0, {"analysis_assurance": aa_block}
        )
        assert outcome["assurance"] == aa_block

    def test_assurance_block_threaded_through_from_scan_report(self):
        from abicheck.policy.outcome import run_outcome_dict_for_scan

        aa_block = {"schema_version": "1.0", "status": "complete"}
        report = {"diff": {"analysis_assurance": aa_block}}
        outcome = run_outcome_dict_for_scan("COMPATIBLE", 0, report=report)
        assert outcome["assurance"] == aa_block

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
            "EVIDENCE_CONTRACT_ERROR",
            1,
            report=report,
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
            "API_BREAK",
            2,
            member_evidence_contract_error=True,
        )
        assert outcome.gate is PolicyGateDecision.POTENTIAL_BREAKING


# ---------------------------------------------------------------------------
# GateInfo structured-first reading (workflows/aggregate/gate.py)
# ---------------------------------------------------------------------------


class TestGateInfoFromReportDataStructuredFirst:
    def _run_outcome_block(
        self, gate: PolicyGateDecision, operational: OperationalStatus
    ):
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

    def test_incomplete_run_outcome_fails_closed_when_severity_is_absent(self):
        """Codex review (P2), fresh evidence beyond the unparseable-value
        fix above: RunOutcome.from_dict only requires gate/operational to
        parse, so a minimal, schema-incomplete run_outcome (missing
        schema_version/compatibility/assurance/lifecycle) previously passed
        as authoritative -- most dangerously here, since with no severity
        block at all there is no OTHER cross-check to catch it, and a
        BREAKING report could read as gate: none."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        data = {"run_outcome": {"gate": "none", "operational": "none"}}
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
        report.scoped_gate._swap_in_scoped_run_outcome) is what must exempt
        this from the contradiction check, not silently coincide with it.

        A genuine compatibility break on the scoped gate (run_outcome.gate
        != none) still surfaces here -- unlike the coverage-only sibling
        test below, which the exemption rebuilds down to a clean gate."""
        data = {
            "severity": {
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            },
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
            ),
            "full_run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
            ),
            "full_verdict": "BREAKING",
            "used_by": ["app.so"],
        }
        gate = GateInfo.from_report_data(data)
        assert gate is not None
        assert gate.exit_code == 4
        assert gate.blocking is True
        assert gate.blocking_categories == ("abi_breaking",)

    def test_scoped_coverage_only_severity_does_not_read_as_a_compatibility_break(
        self,
    ):
        """Codex review (P1), fresh evidence: retaining the folded
        `severity.exit_code` for a scoped report (rather than rebuilding
        from the pure `run_outcome.gate`) meant a scoped report whose only
        contribution was contract-coverage/analysis-assurance (compatibility
        clean, run_outcome.gate: none) still built a GateInfo with
        exit_code=1/blocking=True -- so aggregation counted the target as a
        *compatibility* blocker even though that same coverage/assurance
        contribution is folded onto the aggregate's own orthogonal axis
        independently, double-counting one contribution as two kinds of
        blocker. The exemption must rebuild purely from run_outcome.gate."""
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
            "full_verdict": "BREAKING",
            "used_by": ["app.so"],
        }
        gate = GateInfo.from_report_data(data)
        assert gate is not None
        assert gate.exit_code == 0
        assert gate.blocking is False
        assert gate.blocking_categories == ()

    def test_garbage_full_run_outcome_does_not_bypass_the_contradiction_check(self):
        """Codex review (P2), fresh evidence beyond the contradiction fix
        above: the exemption used to be earned by mere key presence
        (`"full_run_outcome" in data`), so an unscoped, corrupted report
        could pair a genuinely contradictory severity/run_outcome pair with
        an arbitrary `full_run_outcome` value (anything, even None) and have
        the authoritative cross-check silently disabled. The exemption must
        require full_run_outcome to itself be a well-formed RunOutcome
        block, the only shape report.scoped_gate._swap_in_scoped_run_outcome
        ever actually produces."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        data = {
            "severity": {"exit_code": 0, "blocking": False, "blocking_categories": []},
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
            ),
            "full_run_outcome": None,
        }
        with pytest.raises(_MalformedGate):
            GateInfo.from_report_data(data)

    def test_well_formed_but_unrelated_full_run_outcome_does_not_bypass_the_check(self):
        """Codex review (P2), fresh evidence beyond the previous garbage-
        value fix: a *well-formed* full_run_outcome alone was still enough
        to earn the exemption, even attached to an otherwise-unscoped
        corrupt report -- the real writer (report.scoped_gate.
        apply_scoped_gate) never emits full_run_outcome without also unconditionally
        emitting full_verdict and at least one of used_by/
        required_symbol_contract. Without those markers too, the exemption
        must not apply."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        data = {
            "severity": {"exit_code": 0, "blocking": False, "blocking_categories": []},
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
            ),
            "full_run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING, OperationalStatus.NONE
            ),
        }
        with pytest.raises(_MalformedGate):
            GateInfo.from_report_data(data)

    def test_minimal_full_run_outcome_does_not_bypass_the_check(self):
        """Codex review (P2), fresh evidence beyond the two fixes above:
        RunOutcome.from_dict only requires gate/operational to parse (a
        deliberately lenient reader for its OTHER callers) -- so a minimal,
        forged two-key full_run_outcome (missing schema_version/
        compatibility/assurance/lifecycle) alongside full_verdict/used_by
        still earned the exemption. The exemption must additionally
        require every key $defs.run_outcome declares required to actually
        be present, not merely that the two present keys parse."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        data = {
            "severity": {
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            },
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.NONE, OperationalStatus.NONE
            ),
            "full_run_outcome": {"gate": "none", "operational": "none"},
            "full_verdict": "BREAKING",
            "used_by": ["app.so"],
        }
        with pytest.raises(_MalformedGate):
            GateInfo.from_report_data(data)

    def test_schema_invalid_full_run_outcome_values_do_not_bypass_the_check(self):
        """Codex review (P2), fresh evidence beyond the required-key fix
        above: requiring the six keys to be *present* still let schema-
        invalid *values* through (schema_version: null, compatibility: {},
        lifecycle: "bogus" all satisfy "key present" while RunOutcome.
        from_dict silently ignores/defaults every one of them). The
        exemption must validate the required fields' schema types/enums,
        not only their names."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        data = {
            "severity": {
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            },
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.NONE, OperationalStatus.NONE
            ),
            "full_run_outcome": {
                "schema_version": None,
                "compatibility": {},
                "assurance": None,
                "gate": "none",
                "operational": "none",
                "lifecycle": "bogus",
            },
            "full_verdict": "BREAKING",
            "used_by": ["app.so"],
        }
        with pytest.raises(_MalformedGate):
            GateInfo.from_report_data(data)

    def test_null_scoped_markers_do_not_bypass_the_check(self):
        """Codex review, fresh evidence beyond the schema-type fix above:
        `report.scoped_gate.apply_scoped_gate` never emits `full_verdict`/`used_by`/
        `required_symbol_contract` with an explicit null -- but the exemption
        previously only checked *key presence*. `full_verdict: None` (fails
        Verdict parsing), or both scoped markers explicitly `None` (matches
        neither), must not earn the exemption; an otherwise-BREAKING severity
        block must still fail closed."""
        from abicheck.workflows.aggregate.gate import _MalformedGate

        outcome = self._run_outcome_block(
            PolicyGateDecision.NONE, OperationalStatus.NONE
        )
        base = {
            "severity": {
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["x"],
            },
            "run_outcome": outcome,
            "full_run_outcome": outcome,
        }
        with pytest.raises(_MalformedGate):
            GateInfo.from_report_data({**base, "full_verdict": None, "used_by": ["a"]})
        with pytest.raises(_MalformedGate):
            GateInfo.from_report_data(
                {
                    **base,
                    "full_verdict": "BREAKING",
                    "used_by": None,
                    "required_symbol_contract": None,
                }
            )

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

    def test_operational_category_survives_beside_a_stronger_gate(self):
        """Codex review, fresh evidence: operational_status_exit_code caps
        every non-NONE member at exit 1, so beside a *stronger* gate (exit
        4) the numeric max() never raises exit_code -- the previous code
        skipped the whole replace() in that case and silently dropped the
        operational category from blocking_categories, hiding that part of
        the run (e.g. an EVIDENCE_CONTRACT_ERROR member) never completed."""
        data = {
            "severity": {
                "exit_code": 4,
                "blocking": True,
                "blocking_categories": ["abi_breaking"],
            },
            "run_outcome": self._run_outcome_block(
                PolicyGateDecision.ABI_BREAKING,
                OperationalStatus.EVIDENCE_CONTRACT_ERROR,
            ),
        }
        gate = GateInfo.from_report_data(data)
        assert gate is not None
        assert gate.exit_code == 4
        assert set(gate.blocking_categories) == {
            "abi_breaking",
            "evidence_contract_error",
        }


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
            "COMPATIBLE",
            0,
            {"gate": "bogus", "operational": "none"},
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
                "severity": {
                    "exit_code": 0,
                    "blocking": False,
                    "blocking_categories": [],
                }
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
                "severity": {
                    "exit_code": 0,
                    "blocking": False,
                    "blocking_categories": [],
                }
            },
            "run_outcome": {
                "schema_version": "1.0",
                "compatibility": None,
                "assurance": None,
                "gate": "bogus",
                "operational": "none",
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
                    "exit_code": 4,
                    "blocking": True,
                    "blocking_categories": ["abi_breaking"],
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


def _compare_report_with_run_outcome(
    gate: PolicyGateDecision, operational: OperationalStatus
):
    exit_code = fold_gate_and_operational(gate, operational)
    return {
        "report_schema_version": "2.48",
        "library": "libpvxs",
        "verdict": "BREAKING"
        if gate is PolicyGateDecision.ABI_BREAKING
        else "COMPATIBLE",
        "severity": {
            "config": {},
            "categories": {},
            "exit_code": exit_code,
            "blocking": exit_code != 0,
            "blocking_categories": ["abi_breaking"]
            if gate is PolicyGateDecision.ABI_BREAKING
            else [],
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
                "libfoo",
                "1.0",
                "2.0",
                "profile_mismatch",
                "profiles differ",
                report_schema_version="2.48",
            )


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


class TestRunOutcomeSchemaValidation:
    def test_full_run_outcome_is_a_defined_schema_property(self):
        """Codex review (P1), fresh evidence: scoped (--used-by/--required-
        symbol) compare JSON emits the public full_run_outcome field
        (report.scoped_gate._swap_in_scoped_run_outcome), but neither copy of
        compare_report.schema.json defined it -- unlike the analogous
        full_severity, which the two are meant to mirror."""
        from pathlib import Path

        for rel in (
            "abicheck/schemas/compare_report.schema.json",
            "docs/reference/schemas/v1/compare_report.schema.json",
        ):
            schema_path = Path(__file__).resolve().parent.parent / rel
            schema = json.loads(schema_path.read_text())
            props = schema["properties"]
            assert "full_run_outcome" in props, rel
            assert props["full_run_outcome"]["$ref"] == "#/$defs/run_outcome"

    def test_scoped_json_with_full_run_outcome_validates_against_the_schema(self):
        """A real (--used-by/--required-symbol) scoped compare report -- the
        one shape that actually emits full_run_outcome -- still validates
        against the published schema mirror now that the field is defined."""
        from pathlib import Path

        from abicheck import reporter
        from abicheck.analysis_assurance import compute_analysis_assurance
        from abicheck.change_registry_types import Verdict
        from abicheck.checker_types import DiffResult
        from abicheck.model import AbiSnapshot

        old = AbiSnapshot(library="libfoo", version="1.0")
        new = AbiSnapshot(library="libfoo", version="1.1")
        result = DiffResult(
            library="libfoo",
            old_version="1.0",
            new_version="1.1",
            verdict=Verdict.NO_CHANGE,
        )
        result.analysis_assurance = compute_analysis_assurance(result, old, new)
        data = json.loads(reporter.to_json(result))
        data["full_run_outcome"] = RunOutcome(
            compatibility=None,
            assurance=None,
            gate=PolicyGateDecision.ABI_BREAKING,
            operational=OperationalStatus.NONE,
            lifecycle=TargetLifecycle.EXISTING,
        ).to_dict()

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
            library="libfoo",
            old_version="1.0",
            new_version="1.1",
            verdict=Verdict.NO_CHANGE,
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
