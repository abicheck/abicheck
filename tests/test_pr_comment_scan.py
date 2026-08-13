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

"""Tests for ``scan`` JSON report support in the sticky PR comment
(:mod:`abicheck.pr_comment_scan`, dispatched from
:func:`abicheck.pr_comment.build_model`).
"""

from __future__ import annotations

from abicheck.pr_comment import build_model, render_comment, should_post


def _scan_report(**overrides):
    report = {
        "scan_schema_version": "1.13",
        "verdict": "COMPATIBLE",
        "exit_code": 0,
        "risk": {
            "total": 10,
            "n_paths": 1,
            "matched": {"internal_source": 1},
            "recommended_method": "s2",
        },
        "coverage": [
            {
                "layer": "pattern_scan",
                "status": "present",
                "confidence": "reduced",
                "detail": "lexical pattern scan (S3), 1 file(s), 0 fact(s)",
                "elapsed_s": 0.01,
            },
        ],
        "diff": {
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
        },
    }
    report.update(overrides)
    return report


def test_build_model_recognizes_scan_report():
    model = build_model(_scan_report())
    assert model.mode == "scan"


def test_scan_report_with_no_diff_is_audit_only():
    model = build_model(_scan_report(diff=None))
    assert model.scan_audit_only is True
    assert model.counts == (0, 0, 0)
    assert not should_post(model, "changes")
    assert should_post(model, "always")
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Scan audit" in body


def test_scan_breaking_finding_renders_in_breaking_bucket():
    report = _scan_report(
        verdict="BREAKING",
        exit_code=4,
        diff={
            "breaking": 1,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "breaking",
                    "kind": "func_removed",
                    "symbol": "_Z3foov",
                    "description": "removed",
                    "source_location": "foo.h:10",
                    "finding_id": "x1",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (1, 0, 0)
    assert model.breaking[0].kind == "func_removed"
    assert model.scan_verdict == "BREAKING"
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "ABI BREAKING" in body
    assert "func_removed" in body


def test_scan_api_break_finding_renders_in_review_bucket():
    report = _scan_report(
        verdict="API_BREAK",
        exit_code=2,
        diff={
            "breaking": 0,
            "api_break": 1,
            "risk": 0,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "api_break",
                    "kind": "func_signature_changed",
                    "symbol": "_Z3barv",
                    "description": "signature changed",
                    "source_location": "bar.h:5",
                    "finding_id": "x2",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (0, 1, 0)
    assert model.review[0].severity == "api_break"


def test_scan_additions_render_as_public_api_additions():
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 3,
            "additions": [
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "_Z3newv",
                    "description": "added",
                    "source_location": "new.h:1",
                    "finding_id": "x3",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 1)
    assert model.safe[0].category == "addition"
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Public API additions" in body


def test_scan_not_comparable_reason_is_a_blocking_incomplete_finding():
    report = _scan_report(
        verdict="NOT_COMPARABLE",
        exit_code=6,
        diff={"reason": "profile/scope mismatch"},
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete[0].detail == "profile/scope mismatch"
    assert model.incomplete_blocking is True
    assert should_post(model, "changes")
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Source analysis incomplete" in body


def test_scan_not_evaluated_and_suppressed_findings_are_excluded_from_buckets():
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
            "not_evaluated": 1,
            "findings": [
                {
                    "bucket": "not_evaluated",
                    "kind": "type_field_added",
                    "symbol": "_Z3xv",
                    "description": "excluded",
                    "finding_id": "x4",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert model.incomplete == []


def test_scan_gate_api_break_promotes_api_break_to_breaking():
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 1,
            "risk": 0,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "api_break",
                    "kind": "func_signature_changed",
                    "symbol": "_Z3barv",
                    "description": "signature changed",
                    "finding_id": "x5",
                },
            ],
        },
    )
    model = build_model(report, gate_api_break=True)
    assert model.counts == (1, 0, 0)


def test_scan_risk_and_coverage_render_in_scan_note():
    model = build_model(_scan_report())
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "🔎 Scan:" in body
    assert "risk score 10" in body
    assert "📊 Coverage:" in body
    assert "pattern_scan" in body


def test_scan_subject_defaults_to_artifact_when_absent():
    model = build_model(_scan_report())
    assert model.subject == "artifact"


def test_scan_subject_uses_report_provided_value():
    model = build_model(_scan_report(subject="libfoo.so"))
    assert model.subject == "libfoo.so"


def test_scan_severity_gate_reads_from_diff_not_top_level():
    # Codex review: `_run_baseline_compare` nests the resolved severity
    # gate inside `report["diff"]["severity"]`, not the top level.
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 1,
            "severity": {
                "config": {"potential_breaking": "error", "addition": "error"},
            },
            "findings": [
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "_Z3newv",
                    "description": "added",
                    "finding_id": "x6",
                },
            ],
        },
    )
    model = build_model(report)
    # The compatible finding was promoted to blocking by `addition: error`
    # -- it must land in Breaking, not silently render as a safe addition.
    assert model.counts == (1, 0, 0)
    assert model.breaking[0].category == "addition"


def test_scan_contract_coverage_failures_read_from_diff():
    # Codex review: `_baseline_contract_block` nests both
    # `contract_coverage_failures` and `contract_coverage_exit_contribution`
    # inside `report["diff"]`, not the top level.
    report = _scan_report(
        verdict="COMPATIBLE",
        exit_code=1,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
            "contract_coverage_failures": [
                {
                    "provider": "export_table",
                    "side": "new",
                    "mode": "exports",
                    "reason": "no export table captured",
                    "status": "missing",
                    "completeness": "none",
                },
            ],
            "contract_coverage_exit_contribution": 1,
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete_blocking is True
    assert model.contract_coverage_blocking is True
    # A coverage-only failure with no compatibility changes must still post
    # under the default `--on=changes` policy -- otherwise sticky mode
    # deletes a prior comment despite the failed coverage gate.
    assert should_post(model, "changes")


def test_scan_header_counts_are_exact_when_findings_are_truncated():
    # Codex review: the itemized `findings` array is capped (default 20),
    # but the scalar `breaking`/`api_break`/`risk` counts in `diff` are the
    # real, untruncated totals -- the header must use those, not len() of
    # the (possibly-truncated) classified list.
    report = _scan_report(
        verdict="BREAKING",
        exit_code=4,
        diff={
            "breaking": 25,
            "api_break": 3,
            "risk": 2,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "breaking",
                    "kind": "func_removed",
                    "symbol": f"_Z{i}v",
                    "description": "removed",
                    "finding_id": f"x{i}",
                }
                for i in range(20)
            ],
            "findings_truncated": True,
            "findings_truncated_kinds": {"func_removed": 5},
        },
    )
    model = build_model(report)
    assert model.counts == (25, 5, 0)
    assert len(model.breaking) == 20  # itemized rows stay capped
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "25 breaking" in body
    assert "truncated" in body.lower()


def test_scan_gate_api_break_promotes_raw_totals_too():
    report = _scan_report(
        diff={
            "breaking": 1,
            "api_break": 4,
            "risk": 0,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "breaking",
                    "kind": "func_removed",
                    "symbol": "_Z1v",
                    "description": "removed",
                    "finding_id": "x1",
                },
            ],
            "findings_truncated": True,
        },
    )
    model = build_model(report, gate_api_break=True)
    # 1 real breaking + 4 real api_break, all promoted to breaking by
    # --gate-api-break -- even though only one is itemized.
    assert model.counts == (5, 0, 0)


def test_scan_not_comparable_reachable_through_action_exit_mapping():
    # Codex review: action/run.sh's scan exit-code switch used to fall
    # through exit 6 (NOT_COMPARABLE) to the generic VERDICT="ERROR" case,
    # and `_maybe_post_pr_comment`'s own ERROR guard then skipped posting
    # entirely -- verified here at the JSON/model layer (the shell-side fix
    # is covered by test_action_run_sh_scan_pr_comment.py's sibling tests).
    report = _scan_report(verdict="NOT_COMPARABLE", exit_code=6, diff={"reason": "x"})
    model = build_model(report)
    assert model.mode == "scan"
    assert model.incomplete_blocking is True
