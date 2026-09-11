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

"""Tests for ``compare --no-baseline``'s audit-report JSON support in the
sticky PR comment (:func:`abicheck.pr_comment._from_no_baseline`, dispatched
from :func:`abicheck.pr_comment.build_model`).

Codex review (PR #1210): before this branch existed, ``build_model`` fell
through to ``_from_compare`` for a no-baseline audit report, which reads
``report["changes"]`` -- always ``[]`` on this shape, since a real finding
lives under the top-level ``findings`` key instead. A gating audit finding
(case148-shaped) therefore rendered as "No ABI changes" and, under the
default ``pr-comment-on: changes``, was never posted at all (and could
delete a previous sticky comment under ``pr-comment-mode: update``).

Fixtures below mirror the real shape ``compare --no-baseline --format
json`` produces (live-verified against ``examples/case148_...`` and
``examples/case143_...`` during this fix), rather than inventing a shape
that happens to satisfy the new code -- see
``tests/parity/test_no_baseline_audit_corpus_parity.py`` for the same G20
corpus fixtures used end to end elsewhere.
"""

from __future__ import annotations

from abicheck.pr_comment import build_model, render_comment, should_post


def _audit_report(**overrides):
    report = {
        "audit_report_schema_version": "1.1",
        "no_baseline": True,
        "library": "libdemo.so",
        "new_version": "1.0",
        "verdict": None,
        "old_acquisition_state": "declared_absent",
        "changes": [],
        "findings": [],
        "suppressed_findings": [],
        "suppressed_count": 0,
        "exit_axes": {
            "audit_gate": 0,
            "contract_coverage": 0,
            "analysis_assurance": 0,
            "evidence_contract": 0,
            "incomplete_scope": 0,
            "no_comparison_completed": 0,
        },
        "contract_coverage_exit_contribution": 0,
        "exit_code": 0,
    }
    report.update(overrides)
    return report


class TestNoBaselineShapeIsRecognized:
    def test_build_model_dispatches_to_no_baseline_not_compare(self) -> None:
        report = _audit_report()
        model = build_model(report)
        # mode="scan" + scan_audit_only=True is the reuse-of-existing-
        # rendering choice this fix makes (see the module docstring) --
        # what actually matters here is that it did NOT silently fall
        # through to _from_compare's empty-changes-list reading.
        assert model.mode == "scan"
        assert model.scan_audit_only is True


class TestGatingFindingIsRenderedAndPosted:
    """The regression this whole module exists to lock down: a real,
    API_BREAK-classified candidate-side finding (case148's own
    header_build_context_mismatch shape) must show up in the comment and
    must trigger posting under the default `--on changes` policy."""

    def _report_with_finding(self, verdict: str, category: str):
        return _audit_report(
            findings=[
                {
                    "kind": "header_build_context_mismatch",
                    "symbol": "",
                    "description": "Public headers were parsed without the "
                    "build's ABI-relevant context.",
                    "verdict": verdict,
                    "category": category,
                    "evolution": "persistent",
                    "candidate_side_enrichment": False,
                    "observed_value": "define:BIG_BUFFERS",
                }
            ],
        )

    def test_api_break_finding_is_not_dropped(self) -> None:
        report = self._report_with_finding("API_BREAK", "potential_breaking")
        model = build_model(report)
        assert model.total_changes == 1, model
        assert model.counts == (0, 1, 0), model.counts  # (breaking, review, safe)
        assert should_post(model, "changes") is True
        body = render_comment(model, sha="deadbeef")
        assert "header_build_context_mismatch" in body
        assert "No ABI changes" not in body

    def test_breaking_finding_lands_in_breaking_bucket(self) -> None:
        report = self._report_with_finding("BREAKING", "abi_breaking")
        model = build_model(report)
        assert model.counts == (1, 0, 0), model.counts
        assert should_post(model, "changes") is True

    def test_risk_finding_is_shown_but_does_not_change_gating_semantics(
        self,
    ) -> None:
        # case143's own shape: RISK-classified, must never gate the exit
        # code (policy/audit_gate_exit.py) -- but the PR comment is a
        # content channel independent of the gate (module docstring), so
        # it should still show the finding, same as a scan --against
        # crosscheck finding always has.
        report = self._report_with_finding("COMPATIBLE_WITH_RISK", "potential_breaking")
        model = build_model(report)
        assert model.total_changes == 1, model
        assert should_post(model, "changes") is True


class TestCleanAuditRendersNoBaselineHeadline:
    """A clean audit (no findings at all) must say "audit — no baseline to
    compare", never the generic "No ABI changes" wording a real two-sided
    comparison would use -- there was nothing to compare, not "nothing
    changed" (mirrors `pr_comment_scan.from_scan`'s identical audit_only
    headline choice for legacy scan's own audit mode)."""

    def test_clean_audit_headline_and_posting(self) -> None:
        report = _audit_report()
        model = build_model(report)
        assert model.total_changes == 0
        assert should_post(model, "changes") is False
        assert should_post(model, "always") is True
        body = render_comment(model, sha="deadbeef")
        assert "audit" in body.lower()
        assert "no baseline" in body.lower()


class TestSuppressedCountIsReadFromTopLevel:
    """The audit report's own `suppressed_count` lives at the top level
    (unlike compare's own nested `report["suppression"]["suppressed_count"]`
    shape `_suppressed_count` reads) -- `_from_no_baseline` must read it
    directly rather than via that helper, which would always see 0 here."""

    def test_nonzero_suppressed_count_is_reported(self) -> None:
        report = _audit_report(suppressed_count=3)
        model = build_model(report)
        assert model.suppressed_count == 3
