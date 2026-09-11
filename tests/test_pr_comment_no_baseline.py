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
        # mode="scan" + no_baseline_audit=True (NOT scan_audit_only -- that
        # flag is legacy scan's own, now-Action-unreachable audit shape, see
        # the module docstring's round-3 note) is the reuse-of-existing-
        # bucket-counting choice this fix makes -- what actually matters
        # here is that it did NOT silently fall through to _from_compare's
        # empty-changes-list reading.
        assert model.mode == "scan"
        assert model.no_baseline_audit is True
        assert model.scan_audit_only is False


class TestGatingFindingIsRenderedAndPosted:
    """The regression this whole module exists to lock down: a real,
    API_BREAK-classified candidate-side finding (case148's own
    header_build_context_mismatch shape) must show up in the comment and
    must trigger posting under the default `--on changes` policy."""

    def _report_with_finding(
        self, verdict: str, category: str, audit_gate_exit: int = 0
    ):
        report = _audit_report(
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
        report["exit_axes"]["audit_gate"] = audit_gate_exit
        return report

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


class TestHeadlineNeverClaimsATwoSidedComparison:
    """Codex review, PR #1210, round 3: reusing `_bucket_changes`'s two-sided
    headline wording ("ABI BREAKING", "Source API changed; binary ABI
    unchanged") for a no-baseline audit falsely implies a before/after
    result this run never produced, and derives blocking-ness from bucket
    membership rather than the actual overall exit-code outcome --
    misrepresenting a gating run as merely advisory, or vice versa."""

    def _report_with_finding(
        self, verdict: str, category: str, audit_gate_exit: int = 0, exit_code: int = 0
    ):
        report = _audit_report(
            findings=[
                {
                    "kind": "header_build_context_mismatch",
                    "symbol": "",
                    "description": "irrelevant for this test",
                    "verdict": verdict,
                    "category": category,
                    "evolution": "persistent",
                    "candidate_side_enrichment": False,
                    "observed_value": "define:BIG_BUFFERS",
                }
            ],
        )
        report["exit_axes"]["audit_gate"] = audit_gate_exit
        report["exit_code"] = exit_code
        return report

    def test_gating_api_break_finding_gets_a_blocking_audit_headline(self) -> None:
        # The default Action shape: --severity-preset default injected,
        # policy/audit_gate_exit.py's axis actually fired (exit_axes.
        # audit_gate == 3, folded into the overall exit_code == 3) -- the
        # comment must say so plainly, not the generic two-sided "Source
        # API changed; binary ABI unchanged".
        report = self._report_with_finding(
            "API_BREAK", "potential_breaking", audit_gate_exit=3, exit_code=3
        )
        model = build_model(report)
        assert model.no_baseline_audit_blocking is True
        assert model.no_baseline_audit_gate_fired is True
        body = render_comment(model, sha="deadbeef")
        assert "Audit gate: candidate-side finding blocks this step" in body
        assert "Source API changed; binary ABI unchanged" not in body
        assert "ABI BREAKING" not in body

    def test_non_gating_api_break_finding_gets_a_non_blocking_audit_headline(
        self,
    ) -> None:
        # No severity-preset (or an explicit info-only) -- the same
        # api_break-severity finding lands in the Review bucket exactly as
        # above, but this run's own exit never gated on it at all
        # (exit_code == 0). The headline must not claim blocking.
        report = self._report_with_finding("API_BREAK", "potential_breaking")
        model = build_model(report)
        assert model.no_baseline_audit_blocking is False
        body = render_comment(model, sha="deadbeef")
        assert "blocks this step" not in body
        assert "Source API changed; binary ABI unchanged" not in body

    def test_breaking_finding_also_gets_the_audit_headline_not_abi_breaking(
        self,
    ) -> None:
        report = self._report_with_finding(
            "BREAKING", "abi_breaking", audit_gate_exit=3, exit_code=3
        )
        model = build_model(report)
        body = render_comment(model, sha="deadbeef")
        assert "ABI BREAKING" not in body
        assert "Audit gate: candidate-side finding blocks this step" in body

    def test_blocked_by_a_different_axis_still_gets_a_blocking_headline(
        self,
    ) -> None:
        # Codex review, PR #1210, round 4: a report blocked by
        # --contract's coverage ledger alone (exit_axes.audit_gate stays 0,
        # but the overall exit_code is nonzero from a different axis) must
        # still render a blocking headline -- not the audit-gate-specific
        # wording (that axis never fired), but blocking nonetheless.
        report = self._report_with_finding(
            "API_BREAK", "potential_breaking", audit_gate_exit=0, exit_code=1
        )
        model = build_model(report)
        assert model.no_baseline_audit_blocking is True
        assert model.no_baseline_audit_gate_fired is False
        body = render_comment(model, sha="deadbeef")
        assert "🛑" in body
        assert "Audit gate:" not in body
        assert "not gated" not in body


class TestContextLineNeverClaimsAComparison:
    """Codex review, PR #1210, round 4: `_header_block`'s context line
    unconditionally rendered "vs `baseline`" even for a no-baseline audit
    (`old_acquisition_state: declared_absent` in the report) -- fixed to
    skip the "vs X" phrasing entirely for this shape."""

    def test_context_line_does_not_say_vs_baseline(self) -> None:
        report = _audit_report(
            findings=[
                {
                    "kind": "header_build_context_mismatch",
                    "symbol": "",
                    "description": "irrelevant",
                    "verdict": "API_BREAK",
                    "category": "potential_breaking",
                    "evolution": "persistent",
                    "candidate_side_enrichment": False,
                    "observed_value": "x",
                }
            ]
        )
        model = build_model(report)
        body = render_comment(model, sha="deadbeef")
        assert "vs `baseline`" not in body
        assert "audit, no baseline" in body


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
