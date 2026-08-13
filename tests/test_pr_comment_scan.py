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


def test_scan_reports_the_resolved_policy_from_the_diff_block():
    # Codex review: `_run_baseline_compare`'s resolved policy nests inside
    # `report["diff"]["policy"]` (`cli_scan_baseline._baseline_summary`),
    # the same place its severity-gate block and contract-coverage ledger
    # already nest -- reading a top-level `report["policy"]` (which scan's
    # own JSON never populates) always silently fell back to the
    # "strict_abi" default, misreporting a non-default `--policy` run.
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
            "policy": "abicc_default",
        },
    )
    model = build_model(report)
    assert model.policy == "abicc_default"


def test_scan_policy_falls_back_to_strict_abi_when_absent():
    # An audit-only run (diff=None) has no policy classification to report
    # at all -- the footer still needs a value, so it falls back to the
    # documented default rather than rendering blank/None.
    model = build_model(_scan_report(diff=None))
    assert model.policy == "strict_abi"


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


def test_scan_evidence_quality_finding_routes_to_incomplete_not_review():
    # Codex review: an evidence-quality kind (source_fact_coverage_
    # incomplete/layer_coverage_asymmetric/evidence_required_missing/
    # dwarf_info_missing) describes the *comparison's own* evidence
    # coverage, not a detected API/ABI change -- compare's own
    # `_bucket_changes` already pulls these into the "analysis incomplete"
    # bucket ahead of the compatibility severity/gate logic. Scan's own
    # findings classification must do the same, not render a misleading
    # "Compatibility risk blocks this PR" headline for a missing-evidence
    # signal.
    report = _scan_report(
        verdict="COMPATIBLE_WITH_RISK",
        exit_code=0,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 1,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "risk",
                    "kind": "source_fact_coverage_incomplete",
                    "symbol": "",
                    "description": "L4 source-fact evidence incomplete",
                    "finding_id": "e1",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.review == []
    assert model.breaking == []
    assert len(model.incomplete) == 1
    assert model.incomplete[0].kind == "source_fact_coverage_incomplete"
    assert model.incomplete[0].symbol == "Source-fact coverage"
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Compatibility risk blocks this PR" not in body
    # Codex review, follow-up: the exact review *total* (derived from
    # diff["risk"]'s raw scalar) must also exclude the evidence-quality
    # occurrence, not just the itemized list -- otherwise the header still
    # says "1 needs review" next to an empty Review section.
    assert model.counts == (0, 0, 0)


def test_scan_evidence_kind_excluded_from_exact_totals_when_truncated():
    # The itemized diff["findings"] list is independently capped from the
    # raw diff["risk"]/diff["api_break"]/diff["breaking"] scalars -- an
    # evidence-quality finding cut past that cap is still recorded in
    # diff["findings_truncated_kinds"] (kind -> cut count, no bucket of its
    # own), and the exact-total derivation must still exclude it via that
    # kind's fixed default bucket.
    report = _scan_report(
        verdict="COMPATIBLE_WITH_RISK",
        exit_code=0,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 1,
            "compatible": 0,
            # No "findings" key at all -- the one real risk finding was cut
            # entirely by the cap, surviving only in the truncation ledger.
            "findings_truncated": True,
            "findings_truncated_kinds": {"layer_coverage_asymmetric": 1},
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)


def test_scan_incomplete_total_includes_truncated_evidence_occurrences():
    # Codex review: `model.incomplete` only ever holds the itemized (capped)
    # evidence-quality findings from diff["findings"] -- a further
    # occurrence cut by the report cap survives only in
    # diff["findings_truncated_kinds"], which the exact breaking/review
    # scalars already account for (see the test above) but the
    # analysis-incomplete count itself did not, so a diff with 25
    # source_fact_coverage_incomplete findings capped at 20 rendered
    # "20 analysis incomplete" right next to a truncation note claiming the
    # header counts above were exact.
    report = _scan_report(
        verdict="COMPATIBLE_WITH_RISK",
        exit_code=0,
        diff={
            "breaking": 0,
            "api_break": 0,
            # The raw scalar counts every risk-bucket finding the comparison
            # actually produced, itemized or cap-cut alike -- 20 itemized +
            # 5 in the truncation ledger below.
            "risk": 25,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "risk",
                    "kind": "source_fact_coverage_incomplete",
                    "symbol": f"evidence:tu_{i}",
                    "description": "incomplete source-fact coverage",
                    "finding_id": f"e{i}",
                }
                for i in range(20)
            ],
            "findings_truncated": True,
            "findings_truncated_kinds": {"source_fact_coverage_incomplete": 5},
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 20
    assert model.incomplete_total == 25
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "25 analysis incomplete" in body
    assert "20 analysis incomplete" not in body


def test_scan_fully_truncated_incomplete_bucket_still_renders():
    # Codex review, follow-up: when earlier buckets (20 breaking findings)
    # consume the entire shared findings cap, every analysis-incomplete
    # occurrence is cut before it can be itemized -- model.incomplete_total
    # is exact and positive, but model.incomplete itself is empty. Every
    # render-time check keyed on `bool(model.incomplete)` alone (headline,
    # count line, note, section) silently omitted the whole bucket in that
    # case, right next to a truncation note claiming the counts above were
    # exact.
    report = _scan_report(
        verdict="BREAKING",
        exit_code=4,
        diff={
            # The raw scalar counts every breaking-bucket finding regardless
            # of whether it fit under the shared findings cap.
            "breaking": 20,
            "api_break": 0,
            "risk": 5,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "breaking",
                    "kind": "func_removed",
                    "symbol": f"_Z3fn{i}v",
                    "description": "function removed",
                    "finding_id": f"b{i}",
                }
                for i in range(20)
            ],
            "findings_truncated": True,
            # All 5 risk-bucket occurrences were cut entirely -- none made it
            # into the itemized "findings" list above.
            "findings_truncated_kinds": {"source_fact_coverage_incomplete": 5},
        },
    )
    model = build_model(report)
    assert model.counts == (20, 0, 0)
    assert model.incomplete == []
    assert model.incomplete_total == 5
    assert model.has_incomplete is True
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "5 analysis incomplete" in body
    assert "Analysis incomplete (5)" in body
    assert "were cut by the report cap" in body


def test_scan_promoted_evidence_kind_keeps_incomplete_blocking():
    # Codex review: pulling an evidence-quality finding out of breaking/
    # review into incomplete correctly excluded it from the compatibility
    # totals, but a run whose only gating cause was a promoted
    # evidence_required_missing (api_break-bucket by default) still exited
    # non-zero -- the comment must not render the soft "Analysis coverage
    # reduced" headline next to an actually-red Action check.
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 0,
            # Matches the one api_break-bucket finding below -- the raw
            # scalar counts every api_break-bucket finding regardless of
            # kind, evidence-shaped or not.
            "api_break": 1,
            "risk": 0,
            "compatible": 0,
            "severity": {
                "config": {"potential_breaking": "error"},
                "categories": {},
            },
            "findings": [
                {
                    "bucket": "api_break",
                    "kind": "evidence_required_missing",
                    "symbol": "evidence:source_abi",
                    "description": "required evidence layer missing",
                    "finding_id": "e1",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete_blocking is True
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Source analysis incomplete" in body
    assert "Analysis coverage reduced" not in body


def test_scan_evidence_breaking_bucket_not_blocking_when_abi_breaking_demoted():
    # Codex review: a breaking-bucket evidence-quality finding (reachable
    # only via a policy override, since none of the four kinds default
    # there) must follow the resolved `abi_breaking` severity level under
    # the severity-aware scheme, not `gate_breaking` alone -- `abi_breaking:
    # warning` demotes this run to an advisory exit 0 (action/run.sh's
    # ADVISORY_BREAK), regardless of `fail-on-breaking`'s default-true
    # value, so incomplete_blocking must stay False.
    report = _scan_report(
        verdict="COMPATIBLE_WITH_RISK",
        exit_code=0,
        diff={
            "breaking": 1,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
            "severity": {
                "config": {"abi_breaking": "warning"},
                "categories": {},
            },
            "findings": [
                {
                    "bucket": "breaking",
                    "kind": "evidence_required_missing",
                    "symbol": "evidence:source_abi",
                    "description": "required evidence layer missing",
                    "finding_id": "e1",
                },
            ],
        },
    )
    model = build_model(report)  # gate_breaking defaults to True
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete_blocking is False


def test_scan_evidence_breaking_bucket_blocks_unconditionally_when_abi_breaking_error():
    # The complement: scan's own action/run.sh severity-category block
    # (`_sev_cats_real`) fails the step unconditionally once a category is
    # genuinely configured `error` and that's what produced the exit --
    # unlike compare, which has no such block. `fail-on-breaking: false`
    # (gate_breaking=False) must not suppress the blocking headline here.
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 1,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
            "severity": {
                "config": {"abi_breaking": "error"},
                "categories": {},
            },
            "findings": [
                {
                    "bucket": "breaking",
                    "kind": "evidence_required_missing",
                    "symbol": "evidence:source_abi",
                    "description": "required evidence layer missing",
                    "finding_id": "e1",
                },
            ],
        },
    )
    model = build_model(report, gate_breaking=False)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete_blocking is True
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Source analysis incomplete" in body
    assert "Analysis coverage reduced" not in body


def test_scan_unpromoted_evidence_kind_is_not_blocking():
    # The complement: an evidence-quality finding that did NOT gate the run
    # (no --gate-api-break, no potential_breaking promotion) stays
    # non-blocking.
    report = _scan_report(
        verdict="COMPATIBLE_WITH_RISK",
        exit_code=0,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 1,
            "compatible": 0,
            "findings": [
                {
                    "bucket": "risk",
                    "kind": "layer_coverage_asymmetric",
                    "symbol": "",
                    "description": "baseline has an evidence layer the candidate lacks",
                    "finding_id": "e1",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.incomplete_blocking is False
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Analysis coverage reduced" in body


def test_scan_dwarf_info_missing_routes_to_incomplete_not_safe():
    # Codex review: dwarf_info_missing is COMPATIBLE by default severity, so
    # it lands in diff["quality"] (never diff["findings"]) -- an
    # unconditional append there rendered a scan with missing DWARF info as
    # a green "No compatibility impact detected" comment instead of
    # surfacing the coverage gap.
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 1,
            "quality": [
                {
                    "bucket": "compatible",
                    "kind": "dwarf_info_missing",
                    "symbol": "",
                    "description": "no DWARF debug info",
                    "finding_id": "d1",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.safe == []
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete[0].kind == "dwarf_info_missing"
    assert model.incomplete[0].symbol == "Debug info coverage"
    body = render_comment(model, sha="abc1234", detail="standard")
    # `_header` prioritizes a non-empty `model.incomplete` over the "no
    # impact"/"no changes" headlines -- before this fix, the entry stayed
    # in `safe` and `incomplete` was empty, so the header read "No
    # compatibility impact detected" instead.
    assert "Analysis coverage reduced" in body
    assert "No compatibility impact detected" not in body


def test_scan_additions_render_as_public_api_additions():
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            # "compatible" must match the real total the additions list is
            # itemizing (schema 1.13's `additions` is always a subset of
            # `compatible`) -- the exact-total fix reads this scalar
            # directly, so a mismatched fixture value now surfaces here
            # rather than being silently masked by the old additions-only
            # computation.
            "compatible": 1,
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


def test_scan_gate_api_break_does_not_promote_under_demoted_severity_scheme():
    # Codex review: the legacy scheme's fixed api_break -> breaking mapping
    # (the test above) is unconditional -- but under the severity-aware
    # scheme with `potential_breaking` left at its non-error default,
    # `action/run.sh`'s ADVISORY_BREAK keeps the run's real exit at 0
    # regardless of `fail-on-api-break`/gate_api_break. Applying the legacy
    # mapping here rendered a "Source API break blocks this PR" headline
    # beside an actually-green Action check.
    report = _scan_report(
        verdict="COMPATIBLE_WITH_RISK",
        exit_code=0,
        diff={
            "breaking": 0,
            "api_break": 1,
            "risk": 0,
            "compatible": 0,
            "severity": {
                "config": {"potential_breaking": "warning"},
                "categories": {},
            },
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
    assert model.counts == (0, 1, 0)
    assert len(model.breaking) == 0
    assert len(model.review) == 1
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Source API break blocks this PR" not in body


def test_scan_promoted_evidence_kind_not_blocking_under_demoted_severity_scheme():
    # Same fix, the incomplete-bucket-blocking path: an evidence-quality
    # api_break-bucket finding must not read as blocking either, under the
    # identical demoted-severity-scheme + gate_api_break combination.
    report = _scan_report(
        verdict="COMPATIBLE_WITH_RISK",
        exit_code=0,
        diff={
            "breaking": 0,
            "api_break": 1,
            "risk": 0,
            "compatible": 0,
            "severity": {
                "config": {"potential_breaking": "warning"},
                "categories": {},
            },
            "findings": [
                {
                    "bucket": "api_break",
                    "kind": "evidence_required_missing",
                    "symbol": "evidence:source_abi",
                    "description": "required evidence layer missing",
                    "finding_id": "e1",
                },
            ],
        },
    )
    model = build_model(report, gate_api_break=True)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete_blocking is False


def test_scan_breaking_categories_survive_cap_when_addition_reserves_the_slot():
    # Codex review: with a small enough report cap (e.g. --max-findings 1),
    # cli_scan_baseline._add_severity_blocking_compatible_findings's
    # reserved floor can consume the *entire* shared findings-list budget
    # for a severity-addition-error-promoted compatible finding, pushing a
    # real ABI break out of the itemized list entirely -- even though
    # diff["breaking"] == 1 and the run is genuinely BREAKING.
    # breaking_categories/breaking_severities, derived purely from the
    # itemized (capped) list, then held only "addition", mislabeling the
    # headline as a policy-only block ("Public API expansion requires
    # approval") and even rendering a "Compatibility: COMPATIBLE" claim next
    # to a real ABI break.
    report = _scan_report(
        verdict="BREAKING",
        exit_code=4,
        diff={
            "breaking": 1,
            "api_break": 0,
            "risk": 0,
            "compatible": 1,
            "severity": {
                "config": {"addition": "error"},
                "categories": {"addition": {"count": 1}},
            },
            # The report cap reserved the sole itemized slot for the
            # promoted compatible addition, dropping the real ABI break.
            "findings": [
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "new_api_fn",
                    "description": "new public function",
                    "finding_id": "a1",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (2, 0, 0)
    assert "abi_breaking" in model.breaking_categories
    assert "breaking" in model.breaking_severities
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "ABI BREAKING" in body
    assert "Compatibility: COMPATIBLE" not in body
    assert "Public API expansion requires approval" not in body


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


def test_scan_subject_with_backtick_does_not_break_out_of_code_span():
    # Codex review: run.sh passes an untrusted scanned-artifact basename
    # through --subject; a crafted filename containing a backtick could
    # otherwise terminate the header's code span and inject arbitrary
    # Markdown (a fake heading, a link, ...) into the sticky comment. The
    # raw value is stored as-is on the model (escaping is a render-time
    # concern, not a storage one) -- this asserts the rendered body itself
    # never lets the injected heading escape the code span.
    model = build_model(_scan_report(subject="evil`\n## Injected heading\n`lib.so"))
    body = render_comment(model, sha="abc1234", detail="standard")
    # The injected text survives as inert plain text (escaping isn't
    # required to strip it, only to neutralize its Markdown significance),
    # but it must never land on its own line -- that's what would make it
    # render as a real Markdown heading rather than harmless code-span text.
    assert not any(
        line.strip() == "## Injected heading" for line in body.splitlines()
    )
    header_line = next(line for line in body.splitlines() if line.startswith("**Head"))
    assert "\n" not in header_line
    assert "## Injected heading" in header_line  # present, but inert inside the span


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
                "categories": {"addition": {"severity": "error", "count": 1}},
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


def test_scan_promoted_addition_not_duplicated_in_safe_bucket():
    # Codex review: schema 1.13's always-on `diff.additions` unconditionally
    # includes every addition, so a compatible finding promoted to blocking
    # by `addition: error` (which also lands in `diff.findings` per
    # `cli_scan_baseline._add_severity_blocking_compatible_findings`) was
    # rendering under both "Blocked by policy" (breaking) and the green
    # "Public API additions" (safe) section at once.
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 1,
            "severity": {
                "config": {"addition": "error"},
                "categories": {"addition": {"severity": "error", "count": 1}},
            },
            "findings": [
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "_Z3newv",
                    "description": "added",
                    "finding_id": "shared-id-1",
                },
            ],
            "additions": [
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "_Z3newv",
                    "description": "added",
                    "finding_id": "shared-id-1",
                },
            ],
        },
    )
    model = build_model(report)
    # One finding total: Breaking, not Breaking *and* safe.
    assert model.counts == (1, 0, 0)
    assert model.breaking[0].category == "addition"
    body = render_comment(model, sha="abc1234", detail="full")
    assert body.count("_Z3newv") == 1 or body.count("newv") == 1


def test_scan_safe_total_exact_when_additions_truncated():
    # Codex review (follow-up): `diff.additions` is itself capped at
    # --max-findings, so len(model.safe) alone under-reports a diff with
    # more real additions than the cap -- `additions_total` (schema 1.13)
    # is the exact, untruncated count.
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            # Matches additions_total (25) below -- every compatible
            # finding here is addition-shaped, none quality-shaped (see
            # this scalar's role in the exact-total fix).
            "compatible": 25,
            "additions": [
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": f"_Z{i}v",
                    "description": "added",
                    "finding_id": f"a{i}",
                }
                for i in range(20)
            ],
            "additions_truncated": True,
            "additions_total": 25,
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 25)
    assert len(model.safe) == 20  # itemized rows stay capped
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "25 safe" in body
    assert "truncated" in body.lower()


def test_scan_addition_promotion_empties_the_entire_safe_section():
    # `diff["additions"]` (`cli_scan_baseline._addition_finding_dicts`)
    # itemizes only addition-category compatible changes, always -- so an
    # `addition: error` severity config promotes every one of them to
    # blocking at once, never a subset. There is no realistic mixed
    # promoted/unpromoted split within one report's additions list; the
    # exact-count math (`_scan_promoted_compatible_counts`) and the
    # itemized safe section must agree that the whole category emptied out.
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 3,
            "severity": {
                "config": {"addition": "error"},
                "categories": {"addition": {"severity": "error", "count": 3}},
            },
            "findings": [
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "_Z1v",
                    "description": "added",
                    "finding_id": "promoted-1",
                },
            ],
            "findings_truncated": True,
            "additions": [
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "_Z1v",
                    "description": "added",
                    "finding_id": "promoted-1",
                },
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "_Z2v",
                    "description": "added",
                    "finding_id": "promoted-2",
                },
                {
                    "bucket": "compatible",
                    "kind": "func_added",
                    "symbol": "_Z3v",
                    "description": "added",
                    "finding_id": "promoted-3",
                },
            ],
        },
    )
    model = build_model(report)
    # Codex review: `diff["findings"]` is independently capped from
    # `diff["additions"]` -- here only 1 of the 3 promoted additions made
    # it into `findings` (`findings_truncated: True`). An ID-based
    # exclusion sourced from that truncated list would have left the
    # other two promoted additions rendered green even though the exact
    # header already reports zero safe. `addition_promoted` empties the
    # whole safe section wholesale instead, so the two must agree.
    assert model.counts == (3, 0, 0)
    assert model.safe == []


def test_scan_promoted_count_exact_even_when_findings_list_is_capped():
    # Codex review, follow-up: 25 additions promoted to blocking by
    # `addition: error`, but the shared `findings` cap only reserves room
    # for 5 of them (`_add_severity_blocking_compatible_findings`'s
    # reserved-floor design guarantees a *minimum*, not completeness) --
    # the exact severity category count (`diff.severity.categories.
    # addition.count`) must still drive the header, not len(model.breaking).
    findings = [
        {
            "bucket": "compatible",
            "kind": "func_added",
            "symbol": f"_Zp{i}v",
            "description": "added",
            "finding_id": f"promoted-{i}",
        }
        for i in range(5)
    ]
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 25,
            "severity": {
                "config": {"addition": "error"},
                "categories": {"addition": {"severity": "error", "count": 25}},
            },
            "findings": findings,
            "findings_truncated": True,
            "additions_truncated": True,
            "additions_total": 25,
        },
    )
    model = build_model(report)
    assert model.counts == (25, 0, 0)


def test_scan_compatible_quality_finding_counted_when_no_additions_present():
    # Codex review: a compatible-but-non-addition finding (a quality-category
    # change like `func_noexcept_added`, or a policy-demoted removal) lands
    # in `diff.compatible` but never in `diff.additions` (which itemizes
    # only ADDITION_KINDS). Deriving the safe total solely from
    # `diff.additions`/`additions_total` made a diff whose only findings
    # were of this shape report zero changes across every bucket -- the
    # default `pr-comment-on: changes` skipped (or sticky mode deleted) a
    # comment for a real, non-empty scan result.
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 3,
            # No "additions" key at all -- every one of the 3 compatible
            # findings is quality-shaped, not addition-shaped.
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 3)
    assert should_post(model, "changes")


def test_scan_compatible_quality_total_subtracts_promoted_quality_count():
    # Combining with the quality_issues promotion: 5 compatible findings
    # total, 2 of which were promoted to blocking by `quality_issues: error`
    # -- the safe total must be exactly 3, not 5 (over-counting the
    # promoted ones as also safe) and not 0 (under-counting the unpromoted
    # ones, the bug this fix closes).
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 5,
            "severity": {
                "config": {"quality_issues": "error"},
                "categories": {"quality_issues": {"severity": "error", "count": 2}},
            },
        },
    )
    model = build_model(report)
    assert model.counts == (2, 0, 3)


def test_scan_quality_findings_itemized_in_safe_section():
    # Codex review, follow-up: correcting the total wasn't enough on its
    # own -- a scan whose only compatible findings were quality-shaped
    # reported an exact "3 safe" header with nothing itemized underneath it
    # (no rows, even at `detail: full`). `diff.quality` closes that.
    report = _scan_report(
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 2,
            "quality": [
                {
                    "bucket": "compatible",
                    "kind": "field_became_volatile",
                    "symbol": "_Zq1v",
                    "description": "field became volatile",
                    "finding_id": "q1",
                },
                {
                    "bucket": "compatible",
                    "kind": "field_became_volatile",
                    "symbol": "_Zq2v",
                    "description": "field became volatile",
                    "finding_id": "q2",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 2)
    assert len(model.safe) == 2
    assert all(f.category == "quality_issues" for f in model.safe)
    body = render_comment(model, sha="abc1234", detail="full")
    assert "field_became_volatile" in body


def test_scan_quality_promotion_empties_the_quality_section_wholesale():
    # Mirrors the addition-promotion fix: `quality_issues: error` promotes
    # every quality-category compatible finding to blocking at once, so
    # none of them belongs in the green section, regardless of how many of
    # the promoted findings made it into the (independently capped)
    # `diff.findings` list.
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 2,
            "severity": {
                "config": {"quality_issues": "error"},
                "categories": {"quality_issues": {"severity": "error", "count": 2}},
            },
            "findings": [
                {
                    "bucket": "compatible",
                    "kind": "field_became_volatile",
                    "symbol": "_Zq1v",
                    "description": "field became volatile",
                    "finding_id": "q1",
                },
            ],
            "findings_truncated": True,
            "quality": [
                {
                    "bucket": "compatible",
                    "kind": "field_became_volatile",
                    "symbol": "_Zq1v",
                    "description": "field became volatile",
                    "finding_id": "q1",
                },
                {
                    "bucket": "compatible",
                    "kind": "field_became_volatile",
                    "symbol": "_Zq2v",
                    "description": "field became volatile",
                    "finding_id": "q2",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (2, 0, 0)
    assert model.safe == []


def test_scan_promoted_dwarf_info_missing_excluded_from_breaking_total():
    # Codex review: dwarf_info_missing is COMPATIBLE by default severity, so
    # a `quality_issues: error` promotion moves it into `diff.findings`
    # with `bucket: "compatible"` -- `_scan_findings_to_buckets`'s own
    # evidence-kind check already routes it to `incomplete`, but
    # `_scan_promoted_compatible_counts`'s exact per-category scalar
    # (`categories.quality_issues.count`) still counted every promoted
    # compatible finding in that category, evidence-shaped or not, folding
    # it into the Breaking total too -- double-counted across two
    # sections, with a spurious "ABI BREAKING" headline for what is really
    # an evidence-policy failure (exit 1), not a detected break.
    report = _scan_report(
        verdict="SEVERITY_ERROR",
        exit_code=1,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 1,
            "severity": {
                "config": {"quality_issues": "error"},
                "categories": {"quality_issues": {"severity": "error", "count": 1}},
            },
            "findings": [
                {
                    "bucket": "compatible",
                    "kind": "dwarf_info_missing",
                    "symbol": "",
                    "description": "no DWARF debug info",
                    "finding_id": "d1",
                },
            ],
            "quality": [
                {
                    "bucket": "compatible",
                    "kind": "dwarf_info_missing",
                    "symbol": "",
                    "description": "no DWARF debug info",
                    "finding_id": "d1",
                },
            ],
        },
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete[0].kind == "dwarf_info_missing"
    assert model.incomplete_blocking is True
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "ABI BREAKING" not in body
    assert "Source analysis incomplete" in body


def test_scan_audit_only_surfaces_api_break_crosscheck_findings():
    # Codex review: an audit-only run (no --against) always has diff=None,
    # but scan_engine._audit_exit_code can still publish API_BREAK/exit 2
    # from an API_BREAK_KINDS cross-check finding -- previously every
    # bucket stayed empty regardless, so the comment either vanished under
    # `--on=changes` or rendered the green "Scan audit" headline under
    # `--on=always` right next to a red (fail-on-api-break) check.
    report = _scan_report(
        verdict="API_BREAK",
        exit_code=2,
        diff=None,
        crosscheck={
            "version": 1,
            "findings": 3,
            "counts_by_check": {"header_build_context_mismatch": 3},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={},
    )
    model = build_model(report)
    assert model.scan_audit_only is True
    # Codex review, follow-up: the exact review total is the summed
    # cross-check occurrence count (3), not len() of the one aggregate row
    # this single kind renders as.
    assert model.counts == (0, 3, 0)
    assert model.review[0].kind == "header_build_context_mismatch"
    assert "3 occurrence" in model.review[0].detail
    assert should_post(model, "changes")
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Scan audit — no baseline to compare" not in body


def test_scan_promoted_risk_crosscheck_renders_risk_not_api_break_headline():
    # Codex review: `scan_engine._crosscheck_severity_exit` deliberately
    # lets even a RISK-tier check (e.g. `identity_collision_detected`)
    # reach the source-break exit tier once promoted with `--crosscheck
    # KEY=error` -- but that gate promotion doesn't turn the underlying
    # evidence into an actual API break. Hardcoding severity="api_break"
    # for every crosscheck finding made the sticky headline claim
    # "Source API break blocks this PR" even for a promoted RISK check;
    # it must read "Compatibility risk blocks this PR" instead.
    report = _scan_report(
        verdict="API_BREAK",
        exit_code=2,
        diff=None,
        crosscheck={
            "version": 1,
            "findings": 1,
            "counts_by_check": {"identity_collision_detected": 1},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={"identity_collision_detected": "error"},
    )
    model = build_model(report, gate_api_break=True)
    assert model.breaking[0].severity == "risk"
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Compatibility risk blocks this PR" in body
    assert "Source API break blocks this PR" not in body


def test_scan_promoted_api_break_crosscheck_still_renders_api_break_headline():
    # A genuinely API_BREAK_KINDS check (header_build_context_mismatch)
    # promoted the same way must still read as a real API break.
    report = _scan_report(
        verdict="API_BREAK",
        exit_code=2,
        diff=None,
        crosscheck={
            "version": 1,
            "findings": 1,
            "counts_by_check": {"header_build_context_mismatch": 1},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={"header_build_context_mismatch": "error"},
    )
    model = build_model(report, gate_api_break=True)
    assert model.breaking[0].severity == "api_break"
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Source API break blocks this PR" in body


def test_scan_audit_only_gate_api_break_moves_crosscheck_to_breaking():
    report = _scan_report(
        verdict="API_BREAK",
        exit_code=2,
        diff=None,
        crosscheck={
            "version": 1,
            "findings": 1,
            "counts_by_check": {"header_build_context_mismatch": 1},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={},
    )
    model = build_model(report, gate_api_break=True)
    assert model.counts == (1, 0, 0)


def test_scan_audit_only_promoted_crosscheck_surfaced():
    # A RISK-tier check promoted via --crosscheck KEY=error also gates an
    # audit-only run (scan_engine._crosscheck_severity_exit) -- must be
    # surfaced the same way an API_BREAK_KINDS finding is.
    report = _scan_report(
        verdict="API_BREAK",
        exit_code=2,
        diff=None,
        crosscheck={
            "version": 1,
            "findings": 2,
            "counts_by_check": {"identity_collision_detected": 2},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={"identity_collision_detected": "error"},
    )
    model = build_model(report)
    assert model.counts == (0, 2, 0)
    assert "promoted to error" in model.review[0].detail


def test_scan_audit_only_advisory_crosscheck_not_surfaced():
    # An un-promoted RISK-tier check never gates an audit-only run and
    # shouldn't clutter the comment.
    report = _scan_report(
        verdict="COMPATIBLE",
        exit_code=0,
        diff=None,
        crosscheck={
            "version": 1,
            "findings": 4,
            "counts_by_check": {"identity_collision_detected": 4},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={},
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert model.scan_audit_only is True
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Scan audit — no baseline to compare" in body


def test_scan_against_baseline_surfaces_promoted_crosscheck_findings():
    # Codex review: cross-check is a separate evidence axis from the
    # baseline diff (ADR-035 D4) -- a `scan --against` run with a
    # completely clean diff can still be raised to API_BREAK purely by a
    # `--crosscheck KEY=error` promotion (`scan_engine.
    # _crosscheck_severity_exit`, folded into `_run_baseline_compare`
    # unconditionally, unlike the audit-only path's blanket
    # `API_BREAK_KINDS` check below). Restricting the crosscheck-findings
    # helper to `audit_only` silently dropped that signal for every
    # baseline comparison, rendering a green "no ABI changes" comment next
    # to a red `fail-on-api-break` check.
    report = _scan_report(
        verdict="API_BREAK",
        exit_code=2,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
        },
        crosscheck={
            "version": 1,
            "findings": 3,
            "counts_by_check": {"identity_collision_detected": 3},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={"identity_collision_detected": "error"},
    )
    model = build_model(report)
    assert model.scan_audit_only is False
    # Exact review total is the summed occurrence count (3), not the
    # single aggregate row this one kind renders as.
    assert model.counts == (0, 3, 0)
    assert model.review[0].kind == "identity_collision_detected"
    assert "3 occurrence" in model.review[0].detail
    body = render_comment(model, sha="abc1234", detail="standard")
    assert "Scan audit" not in body


def test_scan_against_baseline_unpromoted_api_break_crosscheck_stays_advisory():
    # Codex review, follow-up: unlike the audit-only path
    # (`scan_engine._audit_exit_code`, which gates on ANY `API_BREAK_KINDS`
    # finding), `_run_baseline_compare` only ever folds
    # `_crosscheck_severity_exit` -- an *explicitly promoted* check -- into
    # a baseline comparison's exit code. An un-promoted `API_BREAK_KINDS`
    # cross-check must stay advisory-only here, not render as a review (or,
    # under --gate-api-break, breaking) finding next to an otherwise
    # COMPATIBLE baseline diff.
    report = _scan_report(
        verdict="COMPATIBLE",
        exit_code=0,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
        },
        crosscheck={
            "version": 1,
            "findings": 3,
            "counts_by_check": {"header_build_context_mismatch": 3},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={},
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    model_gated = build_model(report, gate_api_break=True)
    assert model_gated.counts == (0, 0, 0)


def test_scan_against_baseline_gate_api_break_moves_promoted_crosscheck_to_breaking():
    report = _scan_report(
        verdict="API_BREAK",
        exit_code=2,
        diff={
            "breaking": 0,
            "api_break": 0,
            "risk": 0,
            "compatible": 0,
        },
        crosscheck={
            "version": 1,
            "findings": 1,
            "counts_by_check": {"identity_collision_detected": 1},
            "coverage": [],
            "providers": {},
        },
        crosscheck_severities={"identity_collision_detected": "error"},
    )
    model = build_model(report, gate_api_break=True)
    assert model.counts == (1, 0, 0)
