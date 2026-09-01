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

"""ADR-064 stage 1b: `abicheck.workflows.scan_abort_result`.

`TestScanAbortResultFields` states `scan_abort_result_fields`'s own contract
in isolation; `TestScanAbortExitReportWiring` exercises the real
`service_scan.run_scan`/`_run_scan_one_member` catch sites that call it, so
the wiring itself is proven, not only the pure function. Split from
`tests/test_exit_decision.py` (which owns `abicheck.policy.exit_decision*`
directly) because this module's subject lives in `workflows`, not `policy`
-- see `abicheck/workflows/scan_abort_result.py`'s own module docstring for
why the shaping logic moved there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from abicheck.policy.exit_decision_precedence import resolve_scan_exit_decision
from abicheck.schemas import SCAN_SCHEMA_VERSION
from abicheck.workflows.scan_abort_result import (
    attach_prior_on_budget_overflow,
    scan_abort_result_fields,
)


class TestScanAbortResultFields:
    """`_BudgetOverflow`/`_EvidenceContractError` catches now build their
    `ScanResult` from `scan_abort_result_fields`, so `report["exit"]` carries
    a real `ExitDecision` -- the same explanatory block `scan_engine.py`'s own
    `NOT_COMPARABLE` outcome already persists -- instead of leaving `report`
    at its default empty dict.
    """

    def test_budget_overflow_fields(self):
        fields = scan_abort_result_fields("budget_overflow")
        assert fields["verdict"] == "BUDGET_OVERFLOW"
        assert fields["exit_code"] == 5
        assert fields["report"]["scan_schema_version"] == SCAN_SCHEMA_VERSION
        exit_block = fields["report"]["exit"]
        assert exit_block["code"] == 5
        assert exit_block["reasons"] == ["budget_overflow"]
        assert exit_block["budget_overflow_contribution"] == 5
        # Every other axis stayed at its "never computed" default -- nothing
        # ran before the abort.
        assert exit_block["compatibility_contribution"] == 0
        assert exit_block["evidence_contract_error_contribution"] == 0
        assert exit_block["not_comparable_contribution"] == 0

    def test_evidence_contract_error_fields(self):
        fields = scan_abort_result_fields("evidence_contract_error")
        assert fields["verdict"] == "EVIDENCE_CONTRACT_ERROR"
        assert fields["exit_code"] == 1
        exit_block = fields["report"]["exit"]
        assert exit_block["code"] == 1
        assert exit_block["reasons"] == ["evidence_contract_error"]
        assert exit_block["evidence_contract_error_contribution"] == 1
        assert exit_block["budget_overflow_contribution"] == 0

    def test_report_matches_resolve_scan_exit_decision_directly(self):
        # Parity with the shape scan_engine.py's own NOT_COMPARABLE outcome
        # already persists -- same ExitDecision.to_dict(), same key set, not
        # a bespoke, differently-shaped dict for these two axes.
        fields = scan_abort_result_fields("budget_overflow")
        expected = resolve_scan_exit_decision(budget_overflow=True)
        assert expected is not None
        assert fields["report"] == {
            "scan_schema_version": SCAN_SCHEMA_VERSION,
            "exit": expected.to_dict(),
        }

    def test_prior_decision_is_preserved_for_budget_overflow(self):
        # A caller that already resolved a full decision before a *later*
        # budget overflow (scan_engine.py's post-compare deadline check) can
        # carry it through -- the persisted report keeps those contributions
        # instead of showing every other axis as "never computed". The prior
        # decision crosses that exception boundary as a raw dict (`_BudgetOverflow.
        # prior_decision`, set from `ExitDecision.to_dict()`), not the dataclass
        # itself -- see `attach_prior_on_budget_overflow`'s own docstring for why.
        from abicheck.exit_decision import resolve_exit_decision

        prior = resolve_exit_decision(compatibility_contribution=2)
        fields = scan_abort_result_fields(
            "budget_overflow", prior_decision=prior.to_dict()
        )
        assert fields["report"]["exit"]["code"] == 5
        assert fields["report"]["exit"]["compatibility_contribution"] == 2

    def test_prior_decision_is_not_used_for_evidence_contract_error(self):
        # resolve_scan_exit_decision only threads prior_decision through the
        # budget_overflow branch (evidence-contract-error always dominates
        # from a state where nothing else was computed yet) -- passing one
        # for the other axis must not change anything.
        from abicheck.exit_decision import resolve_exit_decision

        prior = resolve_exit_decision(compatibility_contribution=2)
        fields = scan_abort_result_fields(
            "evidence_contract_error", prior_decision=prior.to_dict()
        )
        assert fields["report"]["exit"]["compatibility_contribution"] == 0


class TestAttachPriorOnBudgetOverflow:
    """`attach_prior_on_budget_overflow` gives a `_BudgetOverflow` raised
    inside its block the caller's already-resolved decision, duck-typed via
    ``hasattr`` rather than an `isinstance` check that would need to import
    the private exception class from unclassified `scan_engine.py`.
    """

    class _FakeBudgetOverflow(Exception):
        def __init__(self):
            super().__init__("over budget")
            self.prior_decision = None

    def test_attaches_prior_decision_dict_from_diff_summary(self):
        from abicheck.exit_decision import resolve_exit_decision

        prior_dict = resolve_exit_decision(compatibility_contribution=2).to_dict()
        diff_summary = {"exit": prior_dict}

        with pytest.raises(self._FakeBudgetOverflow) as excinfo:
            with attach_prior_on_budget_overflow(diff_summary):
                raise self._FakeBudgetOverflow()

        assert excinfo.value.prior_decision == prior_dict

    def test_none_diff_summary_attaches_none(self):
        with pytest.raises(self._FakeBudgetOverflow) as excinfo:
            with attach_prior_on_budget_overflow(None):
                raise self._FakeBudgetOverflow()

        assert excinfo.value.prior_decision is None

    def test_exception_without_prior_decision_attribute_passes_through(self):
        # Duck typing via hasattr(): an exception that isn't shaped like
        # `_BudgetOverflow` (no `prior_decision` attribute) must reraise
        # completely untouched, not gain an attribute it never had.
        with pytest.raises(ValueError) as excinfo:
            with attach_prior_on_budget_overflow({"exit": {"code": 1}}):
                raise ValueError("unrelated failure")

        assert not hasattr(excinfo.value, "prior_decision")

    def test_real_late_budget_overflow_carries_the_prior_decision(self):
        # The actual scan_engine.py call site: `_check_scan_budget`'s single
        # raise, wrapped in the real context manager, with a decision the
        # baseline compare already resolved sitting in `diff_summary["exit"]`
        # (as `_run_baseline_compare`/the NOT_COMPARABLE branch leave it).
        from abicheck.exit_decision import resolve_exit_decision
        from abicheck.scan_engine import _BudgetOverflow, _check_scan_budget

        prior = resolve_exit_decision(compatibility_contribution=2)
        diff_summary = {"exit": prior.to_dict()}

        with pytest.raises(_BudgetOverflow) as excinfo:
            with attach_prior_on_budget_overflow(diff_summary):
                _check_scan_budget("10s", budget_s=10.0, elapsed=11.0)

        assert excinfo.value.prior_decision == prior.to_dict()
        fields = scan_abort_result_fields(
            "budget_overflow", prior_decision=excinfo.value.prior_decision
        )
        assert fields["report"]["exit"]["code"] == 5
        assert fields["report"]["exit"]["compatibility_contribution"] == 2


class TestAuditPriorDecision:
    """`audit_prior_decision` gives `run_scan_core`'s no-baseline (audit) path
    the same late-budget-overflow preservation the baseline-compare path has
    -- that branch never builds a `diff_summary`, so without this,
    `attach_prior_on_budget_overflow` had nothing to attach and a late
    overflow in audit mode silently dropped an already-computed API-break/
    crosscheck contribution (Codex review, PR #967, fresh evidence).
    """

    def test_shapes_the_compatibility_and_crosscheck_contributions(self):
        from abicheck.workflows.scan_abort_result import audit_prior_decision

        prior = audit_prior_decision(has_api_break=True, crosscheck_exit=0)
        assert prior["exit"]["code"] == 2
        assert prior["exit"]["compatibility_contribution"] == 2
        assert prior["exit"]["crosscheck_promotion_contribution"] == 0

    def test_crosscheck_promotion_alone_can_dominate(self):
        from abicheck.workflows.scan_abort_result import audit_prior_decision

        prior = audit_prior_decision(has_api_break=False, crosscheck_exit=2)
        assert prior["exit"]["code"] == 2
        assert prior["exit"]["compatibility_contribution"] == 0
        assert prior["exit"]["crosscheck_promotion_contribution"] == 2

    def test_no_findings_is_a_clean_zero_decision(self):
        from abicheck.workflows.scan_abort_result import audit_prior_decision

        prior = audit_prior_decision(has_api_break=False, crosscheck_exit=0)
        assert prior["exit"]["code"] == 0

    def test_audit_exit_code_returns_a_matching_prior_decision(self):
        # `_audit_exit_code` itself, not just the helper it now calls -- an
        # API_BREAK_KINDS finding must produce the same third element
        # `audit_prior_decision` would, computed from the same inputs.
        from types import SimpleNamespace

        from abicheck.checker_policy import ChangeKind
        from abicheck.scan_engine import _audit_exit_code
        from abicheck.workflows.scan_abort_result import audit_prior_decision

        findings = [SimpleNamespace(kind=ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH)]
        verdict, exit_code, prior = _audit_exit_code(findings, severities={})

        assert verdict == "API_BREAK"
        assert exit_code == 2
        assert prior == audit_prior_decision(has_api_break=True, crosscheck_exit=0)

    def test_real_late_audit_budget_overflow_carries_the_prior_decision(self):
        # The actual scan_engine.py call site: run_scan_core's no-baseline
        # branch feeds `_audit_exit_code`'s third element to
        # attach_prior_on_budget_overflow via `diff_summary or audit_prior`
        # -- exercised here through the real `_check_scan_budget` raise.
        from types import SimpleNamespace

        from abicheck.checker_policy import ChangeKind
        from abicheck.scan_engine import (
            _audit_exit_code,
            _BudgetOverflow,
            _check_scan_budget,
        )

        findings = [SimpleNamespace(kind=ChangeKind.HEADER_BUILD_CONTEXT_MISMATCH)]
        _verdict, _exit_code, audit_prior = _audit_exit_code(findings, severities={})

        with pytest.raises(_BudgetOverflow) as excinfo:
            with attach_prior_on_budget_overflow(None or audit_prior):
                _check_scan_budget("10s", budget_s=10.0, elapsed=11.0)

        assert excinfo.value.prior_decision == audit_prior["exit"]
        fields = scan_abort_result_fields(
            "budget_overflow", prior_decision=excinfo.value.prior_decision
        )
        assert fields["report"]["exit"]["code"] == 5
        assert fields["report"]["exit"]["compatibility_contribution"] == 2


class TestScanAbortExitReportWiring:
    """`service_scan.run_scan`/`_run_scan_one_member` build their
    `ScanResult` via ``ScanResult(**scan_abort_result_fields(axis))`` on
    `_BudgetOverflow`/`_EvidenceContractError` -- these tests exercise the
    real catch sites (not just `scan_abort_result_fields` in isolation
    above) to prove the wiring itself, not only the pure function it calls.
    """

    @pytest.mark.parametrize(
        ("exc_name", "depth", "verdict", "exit_code", "reason"),
        [
            ("_BudgetOverflow", "binary", "BUDGET_OVERFLOW", 5, "budget_overflow"),
            (
                "_EvidenceContractError",
                "source",
                "EVIDENCE_CONTRACT_ERROR",
                1,
                "evidence_contract_error",
            ),
        ],
    )
    def test_run_scan(self, monkeypatch, exc_name, depth, verdict, exit_code, reason):
        from abicheck import scan_engine as _se, service_scan as _ss

        exc = getattr(_se, exc_name)

        def raising_core(**kw):
            raise exc("aborted for this test")

        monkeypatch.setattr(_ss, "estimate_scan", lambda req: [])
        monkeypatch.setattr("abicheck.scan_engine.run_scan_core", raising_core)

        req = _ss.ScanRequest(binaries=[Path("libfoo.so")], depth=depth)
        res = _ss.run_scan(req)

        assert res.verdict == verdict
        assert res.exit_code == exit_code
        assert res.report["exit"]["reasons"] == [reason]
        assert res.report["scan_schema_version"] == SCAN_SCHEMA_VERSION
        # Reaches the real to_dict() envelope, not just the dataclass field.
        assert res.to_dict()["report"]["scan_schema_version"] == SCAN_SCHEMA_VERSION

    @pytest.mark.parametrize(
        ("exc_name", "depth", "verdict", "exit_code", "reason"),
        [
            ("_BudgetOverflow", "binary", "BUDGET_OVERFLOW", 5, "budget_overflow"),
            (
                "_EvidenceContractError",
                "source",
                "EVIDENCE_CONTRACT_ERROR",
                1,
                "evidence_contract_error",
            ),
        ],
    )
    def test_run_scan_one_member(
        self, monkeypatch, exc_name, depth, verdict, exit_code, reason
    ):
        from abicheck import scan_engine as _se, service_scan as _ss

        exc = getattr(_se, exc_name)

        def raising_core(**kw):
            raise exc("aborted for this test")

        monkeypatch.setattr("abicheck.scan_engine.run_scan_core", raising_core)

        req = _ss.ScanRequest(binaries=[Path("libfoo.so")], depth=depth)
        res = _ss._run_scan_one_member(
            req, Path("libfoo.so"), start=0.0, budget_s=None, changed_src="none"
        )

        assert res.verdict == verdict
        assert res.exit_code == exit_code
        assert res.report["exit"]["reasons"] == [reason]
