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
from abicheck.workflows.scan_abort_result import scan_abort_result_fields


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
        assert fields["report"] == {"exit": expected.to_dict()}

    def test_prior_decision_is_preserved_for_budget_overflow(self):
        # A caller that already resolved a full decision before a *later*
        # budget overflow (scan_engine.py's post-compare deadline check) can
        # carry it through -- the persisted report keeps those contributions
        # instead of showing every other axis as "never computed".
        from abicheck.exit_decision import resolve_exit_decision

        prior = resolve_exit_decision(compatibility_contribution=2)
        fields = scan_abort_result_fields("budget_overflow", prior_decision=prior)
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
            "evidence_contract_error", prior_decision=prior
        )
        assert fields["report"]["exit"]["compatibility_contribution"] == 0


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
