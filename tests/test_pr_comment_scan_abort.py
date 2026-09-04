# Copyright 2026 Nikolay Petrov
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

"""Tests for ``scan``'s own abort envelope in the sticky PR comment
(:func:`abicheck.pr_comment_scan_abort.scan_abort_incomplete_reason`,
dispatched from :func:`abicheck.pr_comment_scan.from_scan`).

Split from ``tests/test_pr_comment_scan.py`` rather than added there -- that
module sits at its own ADR-061 no-growth debt budget with zero line slack
(``architecture/debt.yaml``), the same reason the production fix landed in
its own sibling module (``abicheck/pr_comment_scan_abort.py``) instead of
growing ``pr_comment_scan.py`` itself. Reuses ``_scan_report``'s shape via a
local copy rather than importing a private helper across test modules.
"""

from __future__ import annotations

from abicheck.pr_comment import build_model, render_comment, should_post
from abicheck.pr_comment_scan_abort import scan_abort_incomplete_reason


def _scan_report(**overrides):
    report = {
        "scan_schema_version": "1.23",
        "verdict": "COMPATIBLE",
        "exit_code": 0,
    }
    report.update(overrides)
    return report


def test_budget_overflow_abort_is_a_blocking_incomplete_finding():
    """The real ``scan --format json`` abort envelope
    (``cli_scan._emit_scan_abort_report``): ``diff`` carries only an
    ``exit`` block, no ``findings``/``additions``/``quality``/``reason`` --
    must render like NOT_COMPARABLE's blocking "analysis incomplete"
    finding, not as a clean, zero-findings comparison (Codex review, fresh
    evidence: the empty buckets previously rendered "No ABI changes" for a
    scan that aborted before comparing anything, and under
    ``--on=changes`` this could delete a prior sticky failure comment).
    """
    report = _scan_report(
        verdict="BUDGET_OVERFLOW",
        exit_code=5,
        diff={
            "exit": {
                "code": 5,
                "reasons": ["budget_overflow"],
                "budget_overflow_contribution": 5,
            }
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete[0].kind == "scan_aborted"
    assert model.incomplete[0].detail == (
        "scan aborted before completing a comparison (budget_overflow)"
    )
    assert model.incomplete_blocking is True
    assert should_post(model, "changes")
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Source analysis incomplete" in body


def test_evidence_contract_error_abort_is_a_blocking_incomplete_finding():
    report = _scan_report(
        verdict="EVIDENCE_CONTRACT_ERROR",
        exit_code=1,
        diff={"exit": {"code": 1, "reasons": ["evidence_contract_error"]}},
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete[0].detail == (
        "scan aborted before completing a comparison (evidence_contract_error)"
    )
    assert model.incomplete_blocking is True
    assert should_post(model, "changes")


def test_abort_reason_falls_back_to_the_verdict_when_no_reasons_list():
    """A malformed/older abort envelope with no ``exit.reasons`` list still
    labels the finding from the top-level ``verdict`` rather than a bare
    "aborted", and does not crash."""
    report = _scan_report(verdict="BUDGET_OVERFLOW", exit_code=5, diff={"exit": {}})
    model = build_model(report)
    assert model.incomplete[0].detail == (
        "scan aborted before completing a comparison (BUDGET_OVERFLOW)"
    )


def test_not_an_abort_shape_returns_none():
    """A normal baseline-comparison ``diff`` (real findings/additions keys
    alongside its own unrelated ``exit`` block) must not be misread as an
    abort -- the abort shape is *only* ``{"exit": ...}`` with no other key."""
    assert (
        scan_abort_incomplete_reason(
            {"exit": {"code": 0}, "findings": [], "additions": []},
            {"verdict": "COMPATIBLE"},
        )
        is None
    )


def test_not_comparable_reason_wins_gate_api_break_crosscheck_routing():
    """Sibling assertion to ``pr_comment_scan``'s own crosscheck-routing
    guard: a scan-abort reason, like NOT_COMPARABLE, must keep a promoted
    cross-check finding in ``review`` rather than ``breaking`` even under
    ``--gate-api-break`` -- both the classified ``Finding`` list and the
    exact scalar header totals (``model.counts``) agree, since an aborted
    scan's cross-check evidence (if any survived long enough to be
    recorded) never actually gated this run's real exit code (``scan_engine``
    never reaches cross-check folding once it has already raised the
    abort)."""
    report = _scan_report(
        verdict="BUDGET_OVERFLOW",
        exit_code=5,
        diff={"exit": {"code": 5, "reasons": ["budget_overflow"]}},
        crosscheck={"counts_by_check": {"identity_collision_detected": 1}},
        crosscheck_severities={"identity_collision_detected": "error"},
    )
    model = build_model(report, gate_api_break=True)
    assert len(model.breaking) == 0
    assert len(model.review) == 1
    breaking_total, review_total, _ = model.counts
    assert (breaking_total, review_total) == (0, 1)
