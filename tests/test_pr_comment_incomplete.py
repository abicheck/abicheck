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

"""Tests for the sticky PR-comment renderer's "analysis incomplete" bucket —
split out of test_pr_comment.py (that file's own line-count cap;
CLAUDE.md's "Files that are large" section).

Covers `layer_coverage_asymmetric` / `evidence_required_missing` /
`source_fact_coverage_incomplete` / `dwarf_info_missing` (the
`_EVIDENCE_KIND_VALUES` set), the `contract_coverage_failures` sibling
ledger, and the `_incomplete_is_blocking` gate-derivation logic — all a
distinct, orthogonal axis from the ordinary Breaking/Needs review/Safe
compatibility buckets (see `abicheck/pr_comment.py`'s own module docstring).
"""

from __future__ import annotations

from abicheck.pr_comment import build_model, render_comment


def _compare_report(changes: list[dict] | None = None) -> dict:
    return {
        "verdict": "BREAKING",
        "library": "libfoo.so",
        "old_version": "v1.2.0",
        "new_version": "v1.3.0",
        "policy": "strict_abi",
        "changes": changes
        if changes is not None
        else [
            {
                "kind": "func_removed",
                "symbol": "foo_init",
                "description": "removed",
                "severity": "breaking",
                "source_location": "foo.h:20",
            },
            {
                "kind": "type_size_changed",
                "symbol": "struct Ctx",
                "description": "grew",
                "old_value": "16",
                "new_value": "24",
                "severity": "breaking",
            },
            {
                "kind": "enum_member_added",
                "symbol": "Color::PURPLE",
                "description": "closed enum",
                "severity": "api_break",
            },
            {
                "kind": "func_added",
                "symbol": "foo_v2",
                "description": "new",
                "severity": "compatible",
            },
            {
                "kind": "type_added",
                "symbol": "struct CtxV2",
                "description": "new",
                "severity": "compatible",
            },
        ],
    }


def test_evidence_coverage_finding_excluded_from_compatibility_buckets():
    # layer_coverage_asymmetric is a comparison-quality signal, not a
    # compatibility finding — it must not land in breaking/review/safe, and
    # must not itself count toward `counts`.
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks (DWARF).",
                "severity": "risk",
            },
        ]
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete[0].kind == "layer_coverage_asymmetric"
    # Friendlier symbol label, not the raw internal marker.
    assert model.incomplete[0].symbol == "Evidence coverage"
    assert model.total_changes == 1
    assert model.incomplete_blocking is False


def test_evidence_required_missing_advisory_by_default_blocking_under_gate():
    # Codex review: evidence_required_missing is NOT intrinsically
    # always-blocking — action/run.sh's own gate only turns the check red on
    # an API_BREAK verdict when fail-on-api-break is actually set (its
    # default is false), so a hardcoded always-True here disagreed with the
    # check this comment is supposed to mirror. Blocking-ness follows the
    # finding's own resolved severity ("api_break") the same way every other
    # api_break finding is gated in this module.
    report = _compare_report(
        [
            {
                "kind": "evidence_required_missing",
                "symbol": "evidence:build_context",
                "description": "Policy requires build context evidence, none collected.",
                "severity": "api_break",
            },
        ]
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    assert model.incomplete[0].symbol == "Required evidence: build context"
    assert model.incomplete_blocking is False

    model_gated = build_model(report, gate_api_break=True)
    assert model_gated.incomplete_blocking is True


def test_evidence_required_missing_preserves_distinct_layers():
    # Codex review: one evidence_required_missing finding per missing layer
    # must not collapse to one indistinguishable "Required evidence" label —
    # a maintainer needs to know *which* layers to supply.
    report = _compare_report(
        [
            {
                "kind": "evidence_required_missing",
                "symbol": "evidence:build_context",
                "description": "d1",
                "severity": "api_break",
            },
            {
                "kind": "evidence_required_missing",
                "symbol": "evidence:source_abi",
                "description": "d2",
                "severity": "api_break",
            },
        ]
    )
    model = build_model(report)
    symbols = {f.symbol for f in model.incomplete}
    assert symbols == {
        "Required evidence: build context",
        "Required evidence: source ABI",
    }


def test_source_fact_coverage_incomplete_and_dwarf_info_missing_are_incomplete():
    report = _compare_report(
        [
            {
                "kind": "source_fact_coverage_incomplete",
                "symbol": "",
                "description": "incomplete fact set",
                "severity": "risk",
            },
            {
                "kind": "dwarf_info_missing",
                "symbol": "<dwarf>",
                "description": "no DWARF on new binary",
                "severity": "compatible",
            },
        ]
    )
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 2
    assert model.incomplete_blocking is False
    symbols = {f.symbol for f in model.incomplete}
    assert symbols == {"Source-fact coverage", "Debug info coverage"}


def test_layer_coverage_asymmetric_not_blocking_from_potential_breaking_alone():
    # Codex review (second round): action/run.sh's `compare`-mode branch has
    # no unconditional severity gate for its exit-code-2/4 tiers the way
    # `scan` mode does — every severity-aware exit 2 (whether from a plain
    # API_BREAK verdict or from potential_breaking: error) maps to the same
    # API_BREAK label, gated solely by fail-on-api-break. So
    # potential_breaking: error alone must NOT mark this risk finding as
    # blocking; only gate_api_break does.
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "risk",
            },
        ]
    )
    report["severity"] = {"config": {"potential_breaking": "error"}}
    model = build_model(report)
    assert model.incomplete_blocking is False

    model_gated = build_model(report, gate_api_break=True)
    assert model_gated.incomplete_blocking is True


def test_render_header_analysis_incomplete_not_worded_as_review():
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "risk",
            },
        ]
    )
    body = render_comment(build_model(report), sha="deadbeef")
    assert "Analysis coverage reduced" in body
    assert "Review recommended" not in body
    assert "🛑 Analysis incomplete" in body


def test_render_header_evidence_required_missing_advisory_by_default():
    report = _compare_report(
        [
            {
                "kind": "evidence_required_missing",
                "symbol": "evidence:source_abi",
                "description": "Policy requires source ABI evidence, none collected.",
                "severity": "api_break",
            },
        ]
    )
    body = render_comment(build_model(report), sha="deadbeef")
    assert "Analysis coverage reduced" in body
    assert "Source analysis incomplete" not in body
    assert "Review recommended" not in body


def test_render_header_source_analysis_incomplete_when_blocking():
    report = _compare_report(
        [
            {
                "kind": "evidence_required_missing",
                "symbol": "evidence:source_abi",
                "description": "Policy requires source ABI evidence, none collected.",
                "severity": "api_break",
            },
        ]
    )
    body = render_comment(build_model(report, gate_api_break=True), sha="deadbeef")
    assert "Source analysis incomplete" in body
    assert "🛑" in body
    assert "Review recommended" not in body


def test_real_breaking_finding_wins_headline_over_incomplete():
    # A genuine ABI break must never be hidden behind an "analysis
    # incomplete" headline — the incomplete bucket still renders its own
    # section and a note, but the headline stays "ABI BREAKING".
    report = _compare_report(
        [
            {
                "kind": "func_removed",
                "symbol": "foo_init",
                "description": "removed",
                "severity": "breaking",
            },
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "risk",
            },
        ]
    )
    model = build_model(report)
    assert model.counts == (1, 0, 0)
    assert len(model.incomplete) == 1
    body = render_comment(model, sha="deadbeef")
    assert "ABI BREAKING" in body
    assert "🛑 Analysis incomplete" in body
    assert "analysis-coverage finding" in body  # the note pointing at the section


def test_real_review_finding_keeps_headline_over_advisory_incomplete():
    # Codex review: an ungated `layer_coverage_asymmetric` (advisory, not
    # blocking) must not bury a genuine, separate review-bucket finding
    # (e.g. a default-severity api_break) behind "Analysis coverage
    # reduced" — a real compatibility finding always keeps headline
    # priority over a merely-advisory coverage gap.
    report = _compare_report(
        [
            {
                "kind": "enum_member_added",
                "symbol": "E::X",
                "description": "closed enum",
                "severity": "api_break",
            },
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "risk",
            },
        ]
    )
    model = build_model(report)
    assert model.incomplete_blocking is False
    assert len(model.review) == 1
    body = render_comment(model, sha="deadbeef")
    assert "Source API changed; binary ABI unchanged" in body
    assert "Analysis coverage reduced" not in body
    # The coverage gap is still surfaced, just not as the headline.
    assert "🛑 Analysis incomplete" in body
    assert "analysis-coverage finding" in body


def test_incomplete_blocking_when_policy_promotes_severity_to_breaking():
    # Codex review: a named policy / --policy-file override can promote
    # layer_coverage_asymmetric all the way to `severity: "breaking"` in the
    # JSON without touching the `potential_breaking` severity-config knob at
    # all — blocking-ness must follow the finding's own resolved severity,
    # not just the one kind + one level check.
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "breaking",
            },
        ]
    )
    model = build_model(report)
    assert model.incomplete_blocking is True
    body = render_comment(model, sha="deadbeef")
    assert "Source analysis incomplete" in body
    assert "🛑" in body


def test_incomplete_blocking_under_fail_on_api_break():
    # gate_api_break (the action's fail-on-api-break) must gate an
    # api_break-severity incomplete finding the same way it gates every
    # other api_break finding elsewhere in this module.
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "api_break",
            },
        ]
    )
    model = build_model(report, gate_api_break=True)
    assert model.incomplete_blocking is True
    model_ungated = build_model(report, gate_api_break=False)
    assert model_ungated.incomplete_blocking is False


def test_legacy_risk_severity_never_blocks_even_under_gate_api_break():
    # Codex review: under the LEGACY exit-code scheme (no --severity-*
    # flags — the default for layer_coverage_asymmetric's own "risk"
    # severity), severity.legacy_exit_code maps COMPATIBLE_WITH_RISK to a
    # FIXED exit code of 0 — it never gates the process exit at all,
    # regardless of fail-on-api-break. An earlier revision treated
    # "risk" the same as "api_break" (fixed exit 2, genuinely gated by
    # fail-on-api-break), which is correct for api_break but wrong for
    # risk under this scheme.
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "risk",
            },
        ]
    )
    assert "severity" not in report  # legacy scheme: no --severity-* config
    model = build_model(report, gate_api_break=True, gate_breaking=True)
    assert model.incomplete_blocking is False


def test_severity_aware_api_break_needs_category_gated_not_just_gate_flag():
    # Codex review: under the SEVERITY-AWARE scheme, severity.compute_exit_code
    # only folds in a category's contribution when that category's configured
    # level is "error" — gate_api_break alone (with potential_breaking left at
    # its default, non-error level) must not be sufficient, unlike under the
    # legacy scheme where api_break's exit-2 mapping is unconditional.
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "api_break",
            },
        ]
    )
    report["severity"] = {"config": {"potential_breaking": "warning"}}
    model = build_model(report, gate_api_break=True)
    assert model.incomplete_blocking is False

    # With potential_breaking actually gated to error, both conditions are
    # now met and it blocks.
    report["severity"] = {"config": {"potential_breaking": "error"}}
    model_gated = build_model(report, gate_api_break=True)
    assert model_gated.incomplete_blocking is True


def test_severity_aware_breaking_needs_abi_breaking_category_gated():
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Promoted by a plugin_abi-style policy.",
                "severity": "breaking",
            },
        ]
    )
    report["severity"] = {"config": {"abi_breaking": "warning"}}
    model = build_model(report, gate_breaking=True)
    assert model.incomplete_blocking is False

    report["severity"] = {"config": {"abi_breaking": "error"}}
    model_gated = build_model(report, gate_breaking=True)
    assert model_gated.incomplete_blocking is True

    # abi_breaking:error alone, without gate_breaking, still doesn't block —
    # compare's exit-4 tier is still gated by fail-on-breaking either way.
    model_ungated_flag = build_model(report, gate_breaking=False)
    assert model_ungated_flag.incomplete_blocking is False


def test_incomplete_breaking_severity_honors_gate_breaking():
    # Codex review: a policy override that promotes an evidence-coverage
    # finding all the way to severity: "breaking" must still respect
    # fail-on-breaking (gate_breaking) — not block unconditionally, the way
    # an earlier revision did.
    report = _compare_report(
        [
            {
                "kind": "layer_coverage_asymmetric",
                "symbol": "evidence:coverage",
                "description": "Base was analyzed with evidence the target lacks.",
                "severity": "breaking",
            },
        ]
    )
    model_default = build_model(report)  # gate_breaking defaults True
    assert model_default.incomplete_blocking is True
    model_ungated = build_model(report, gate_breaking=False)
    assert model_ungated.incomplete_blocking is False


def test_incomplete_quality_issues_error_blocks_unconditionally():
    # Codex review: dwarf_info_missing (default severity "compatible",
    # category "quality_issues") gated by `quality_issues: error` fails the
    # real Action step at compare's own exit-code-1 SEVERITY_ERROR tier,
    # which action/run.sh gates with no fail-on-* condition at all — this
    # must block regardless of gate_api_break/gate_breaking.
    report = _compare_report(
        [
            {
                "kind": "dwarf_info_missing",
                "symbol": "<dwarf>",
                "description": "no DWARF on new binary",
                "severity": "compatible",
            },
        ]
    )
    report["severity"] = {"config": {"quality_issues": "error"}}
    model = build_model(report, gate_api_break=False, gate_breaking=False)
    assert model.incomplete_blocking is True
    body = render_comment(model, sha="x")
    assert "Source analysis incomplete" in body

    # Without the quality_issues gate, the same finding stays advisory.
    report_ungated = _compare_report(
        [
            {
                "kind": "dwarf_info_missing",
                "symbol": "<dwarf>",
                "description": "no DWARF on new binary",
                "severity": "compatible",
            },
        ]
    )
    model_ungated = build_model(report_ungated)
    assert model_ungated.incomplete_blocking is False


def test_contract_coverage_failures_join_incomplete_bucket():
    # Codex review: ADR-049 Phase 5's sibling coverage-failure ledger
    # (contract_coverage_failures) is deliberately kept OUTSIDE `changes` —
    # a report whose only problem is incomplete contract-evidence coverage
    # previously had zero entries in every bucket at all, even though
    # contract_coverage_exit_contribution already failed the real gate.
    report = _compare_report([])
    report["contract_coverage_failures"] = [
        {
            "provider": "export_table",
            "side": "new",
            "record_id": "libfoo.so",
            "reason": "export table not captured",
            "status": "unresolved",
            "completeness": "absent",
            "mode": "exports",
            "suppressible": False,
        }
    ]
    report["contract_coverage_exit_contribution"] = 1
    model = build_model(report)
    assert model.counts == (0, 0, 0)
    assert len(model.incomplete) == 1
    finding = model.incomplete[0]
    assert finding.kind == "contract_coverage_failure"
    assert "export table" in finding.symbol
    assert "new" in finding.symbol
    assert "export table not captured" in finding.detail
    assert model.incomplete_blocking is True
    assert model.total_changes == 1

    body = render_comment(model, sha="x")
    assert "Source analysis incomplete" in body
    assert "🛑 Analysis incomplete" in body


def test_contract_coverage_blocking_overrides_scoped_compatible_headline():
    # Codex review: contract_coverage_exit_contribution folds into the real
    # exit code unconditionally, on top of ANY other verdict — including a
    # --used-by/--required-symbol scoped COMPATIBLE one. A scoped-compatible
    # run whose contract coverage also failed must not render
    # "✅ Compatible (scoped)" as if that were the whole story: the real
    # exit code is already non-zero from the orthogonal coverage axis.
    report = _compare_report([])  # clean full-library diff too
    report["verdict"] = "COMPATIBLE"
    report["used_by"] = [
        {
            "app": "/opt/app/bin/myapp",
            "verdict": "COMPATIBLE",
            "required_symbol_count": 3,
            "missing_symbols": [],
            "missing_versions": [],
            "relevant_change_count": 0,
            "symbol_coverage": 100.0,
        }
    ]
    report["contract_coverage_failures"] = [
        {
            "provider": "export_table",
            "side": "new",
            "record_id": "libfoo.so",
            "reason": "export table not captured",
            "status": "unresolved",
            "completeness": "absent",
            "mode": "exports",
            "suppressible": False,
        }
    ]
    report["contract_coverage_exit_contribution"] = 1
    model = build_model(report)
    assert model.scoped_verdict == "COMPATIBLE"
    assert model.contract_coverage_blocking is True
    body = render_comment(model, sha="x")
    assert "Source analysis incomplete" in body
    assert "Compatible (scoped)" not in body


def test_contract_coverage_blocking_wins_over_unscoped_breaking_bucket_too():
    # Codex review (second round): the fix above must return the coverage
    # headline directly, not merely skip the scoped-header return and fall
    # through to the rest of _header() — the full-library `breaking` bucket
    # is informational-only under scoping (not part of the actual gate), so
    # it must not steal the headline back from the orthogonal coverage axis
    # that is what's actually failing the scoped consumer's own gate.
    report = _compare_report()  # non-empty: carries real breaking findings
    report["full_verdict"] = report["verdict"]
    report["verdict"] = "COMPATIBLE"
    report["used_by"] = [
        {
            "app": "/opt/app/bin/myapp",
            "verdict": "COMPATIBLE",
            "required_symbol_count": 3,
            "missing_symbols": [],
            "missing_versions": [],
            "relevant_change_count": 0,
            "symbol_coverage": 100.0,
        }
    ]
    report["contract_coverage_failures"] = [
        {
            "provider": "export_table",
            "side": "new",
            "record_id": "libfoo.so",
            "reason": "export table not captured",
            "status": "unresolved",
            "completeness": "absent",
            "mode": "exports",
            "suppressible": False,
        }
    ]
    report["contract_coverage_exit_contribution"] = 1
    model = build_model(report)
    assert model.scoped_verdict == "COMPATIBLE"
    assert model.contract_coverage_blocking is True
    assert model.counts[0] > 0  # the unscoped bucket really does have breaks
    body = render_comment(model, sha="x")
    assert "Source analysis incomplete" in body
    assert "ABI BREAKING" not in body


def test_contract_coverage_advisory_does_not_override_scoped_compatible():
    # The inverse: when contract coverage did NOT contribute to the exit
    # code (contribution 0, or the key absent entirely), the scoped
    # COMPATIBLE headline is untouched — this is the pre-existing,
    # already-tested behavior, confirmed unaffected by the fix above.
    report = _compare_report()
    report["full_verdict"] = report["verdict"]
    report["verdict"] = "COMPATIBLE"
    report["used_by"] = [
        {
            "app": "/opt/app/bin/myapp",
            "verdict": "COMPATIBLE",
            "required_symbol_count": 3,
            "missing_symbols": [],
            "missing_versions": [],
            "relevant_change_count": 0,
            "symbol_coverage": 100.0,
        }
    ]
    model = build_model(report)
    assert model.scoped_verdict == "COMPATIBLE"
    assert model.contract_coverage_blocking is False
    body = render_comment(model, sha="x")
    assert "Compatible (scoped)" in body


def test_contract_coverage_exit_contribution_zero_is_advisory():
    report = _compare_report([])
    report["contract_coverage_failures"] = []
    report["contract_coverage_exit_contribution"] = 0
    model = build_model(report)
    assert model.incomplete == []
    assert model.incomplete_blocking is False
    # A clean (empty) ledger with 0 contribution posts nothing new.
    assert model.total_changes == 0


def test_report_without_contract_evaluation_is_unaffected():
    # A report that never ran --contract-evaluation carries neither key at
    # all — must not fabricate an incomplete finding or force blocking.
    report = _compare_report()
    assert "contract_coverage_failures" not in report
    model = build_model(report)
    assert all(f.kind != "contract_coverage_failure" for f in model.incomplete)


def test_contract_coverage_failures_malformed_shapes_degrade_safely():
    # A wrong-typed contract_coverage_failures value, or a non-dict entry
    # inside an otherwise-valid list, must never raise — just be skipped.
    report_wrong_type = _compare_report()
    report_wrong_type["contract_coverage_failures"] = "not a list"
    model = build_model(report_wrong_type)
    assert all(f.kind != "contract_coverage_failure" for f in model.incomplete)

    report_bad_item = _compare_report([])
    report_bad_item["contract_coverage_failures"] = ["not a dict", 42, None]
    model_bad_item = build_model(report_bad_item)
    assert model_bad_item.incomplete == []


# ── release-mode contract-coverage ledger (CLI-audit P2, Codex review) ─────


def _release_report() -> dict:
    return {
        "verdict": "COMPATIBLE",
        "old_dir": "/pkg/old",
        "new_dir": "/pkg/new",
        "libraries": [
            {
                "library": "libfoo.so.1",
                "verdict": "COMPATIBLE",
                "breaking": 0,
                "source_breaks": 0,
                "compatible_additions": 0,
            },
            {
                "library": "libbar.so.2",
                "verdict": "COMPATIBLE",
                "breaking": 0,
                "source_breaks": 0,
                "compatible_additions": 0,
            },
        ],
        "unmatched_old": [],
        "unmatched_new": [],
    }


def test_release_contract_coverage_contribution_creates_blocking_incomplete_finding():
    # Without this, a release whose only problem was incomplete contract
    # coverage had `incomplete == []` and 0 changes — the default
    # `--on=changes` policy would then skip (or delete) the sticky comment
    # even though the release's own exit code had already failed.
    report = _release_report()
    report["libraries"][1]["contract_coverage_exit_contribution"] = 1
    report["contract_coverage_exit_contribution"] = 1
    model = build_model(report)
    assert model.mode == "release"
    assert len(model.incomplete) == 1
    assert model.incomplete[0].kind == "contract_coverage_failure"
    assert "libbar.so.2" in model.incomplete[0].detail
    assert "libfoo.so.1" not in model.incomplete[0].detail
    assert model.incomplete_blocking is True
    assert model.total_changes == 1
    _, title = _header_for(model)
    assert title == "Source analysis incomplete"


def test_release_contract_coverage_finding_renders_in_full_detail_body():
    # Codex review (CLI-audit P2 follow-up): the headline/count alone told a
    # reviewer *that* something was incomplete, but `_body_sections`'s
    # release-mode early return skipped `model.incomplete` entirely, so the
    # actual "Incomplete for: ..." detail naming the affected library was
    # unreachable in the rendered comment body -- full detail included.
    report = _release_report()
    report["libraries"][1]["contract_coverage_exit_contribution"] = 1
    report["contract_coverage_exit_contribution"] = 1
    model = build_model(report)
    body = render_comment(model, sha="x", detail="full")
    assert "Analysis incomplete" in body
    assert "Incomplete for: libbar.so.2" in body


def test_release_contract_coverage_zero_contribution_is_not_incomplete():
    report = _release_report()
    report["contract_coverage_exit_contribution"] = 0
    model = build_model(report)
    assert model.incomplete == []
    assert model.incomplete_blocking is False


def test_release_report_without_contract_evaluation_is_unaffected():
    report = _release_report()
    assert "contract_coverage_exit_contribution" not in report
    model = build_model(report)
    assert model.incomplete == []
    assert model.incomplete_blocking is False


def test_release_contract_coverage_without_per_library_detail_still_blocks():
    # A release-level contribution with no per-library key set (shouldn't
    # normally happen -- the release CLI always stamps both together -- but
    # must degrade safely rather than silently dropping the signal).
    report = _release_report()
    report["contract_coverage_exit_contribution"] = 1
    model = build_model(report)
    assert len(model.incomplete) == 1
    assert "per-library" in model.incomplete[0].detail
    assert model.incomplete_blocking is True


def test_release_contract_coverage_malformed_shape_degrades_safely():
    report = _release_report()
    report["contract_coverage_exit_contribution"] = "not an int"
    model = build_model(report)
    assert model.incomplete == []
    assert model.incomplete_blocking is False


def test_release_contract_coverage_skips_non_dict_library_entries():
    # A malformed (non-dict) libraries entry must be skipped when scanning
    # for which library contributed, not raise.
    report = _release_report()
    report["libraries"].append("not a dict")
    report["libraries"][1]["contract_coverage_exit_contribution"] = 1
    report["contract_coverage_exit_contribution"] = 1
    model = build_model(report)
    assert len(model.incomplete) == 1
    assert "libbar.so.2" in model.incomplete[0].detail


def _header_for(model):
    from abicheck.pr_comment import _header

    return _header(model)
