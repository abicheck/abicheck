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

"""Tests for the sticky PR-comment renderer and the ``pr-comment`` CLI."""

from __future__ import annotations

import json

import pytest

from abicheck.pr_comment import (
    MARKER,
    CommentModel,
    build_model,
    render_comment,
    should_post,
)


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


def _release_report() -> dict:
    return {
        "verdict": "BREAKING",
        "old_dir": "/pkg/old",
        "new_dir": "/pkg/new",
        "libraries": [
            {
                "library": "libfoo.so.1",
                "verdict": "BREAKING",
                "breaking": 2,
                "source_breaks": 0,
                "compatible_additions": 1,
            },
            {
                "library": "libbar.so.2",
                "verdict": "COMPATIBLE",
                "breaking": 0,
                "source_breaks": 0,
                "compatible_additions": 3,
            },
        ],
        "unmatched_old": ["libgone.so.1"],
        "unmatched_new": [],
    }


def _appcompat_report() -> dict:
    return {
        "application": "/opt/app/bin/myapp",
        "old_library": "/lib/libfoo.so.1",
        "new_library": "/lib/libfoo.so.2",
        "verdict": "BREAKING",
        "missing_symbols": ["foo_legacy", "foo_old"],
        "relevant_changes": [
            {
                "kind": "func_params_changed",
                "symbol": "foo_run",
                "description": "signature changed",
                "severity": "breaking",
            },
        ],
    }


# ---------------------------------------------------------------------------
# Shape detection + bucketing
# ---------------------------------------------------------------------------


def test_compare_buckets_by_severity():
    model = build_model(_compare_report())
    assert model.mode == "compare"
    assert model.counts == (2, 1, 2)
    assert {f.symbol for f in model.breaking} == {"foo_init", "struct Ctx"}
    assert [f.symbol for f in model.review] == ["Color::PURPLE"]
    assert {f.symbol for f in model.safe} == {"foo_v2", "struct CtxV2"}


def test_compare_detail_text_renders_value_delta():
    model = build_model(_compare_report())
    size_change = next(f for f in model.breaking if f.symbol == "struct Ctx")
    assert "16 → 24" in size_change.detail


def test_release_shape_detected_and_summed():
    model = build_model(_release_report())
    assert model.mode == "release"
    # breaking=2, review(source_breaks)=0, safe(additions)=1+3=4
    assert model.counts == (2, 0, 4)
    assert model.removed_libraries == ["libgone.so.1"]
    assert len(model.library_rows) == 2


def test_release_risk_only_library_counts_as_review():
    # A COMPATIBLE_WITH_RISK library with only risk_changes must still register
    # as a change so `--on changes` posts the warning-tone comment.
    report = {
        "verdict": "COMPATIBLE_WITH_RISK",
        "old_dir": "/pkg/old",
        "new_dir": "/pkg/new",
        "libraries": [
            {
                "library": "librisk.so.1",
                "verdict": "COMPATIBLE_WITH_RISK",
                "breaking": 0,
                "source_breaks": 0,
                "risk_changes": 3,
                "compatible_additions": 0,
            },
        ],
        "unmatched_old": [],
        "unmatched_new": [],
    }
    model = build_model(report)
    assert model.counts == (0, 3, 0)
    assert should_post(model, "changes") is True


def test_release_bundle_findings_register_as_change():
    # A clean per-library release that breaks only at the bundle/matrix level
    # must still register a change so the comment is posted.
    report = {
        "verdict": "BREAKING",
        "old_dir": "/o",
        "new_dir": "/n",
        "libraries": [
            {
                "library": "libok.so",
                "verdict": "COMPATIBLE",
                "breaking": 0,
                "source_breaks": 0,
                "compatible_additions": 0,
            },
        ],
        "bundle_verdict": "BREAKING",
        "bundle_findings": [
            {"kind": "soname_mismatch", "symbol": "libfoo", "description": "x"},
            {"kind": "soname_mismatch", "symbol": "libbar", "description": "y"},
        ],
        "matrix_verdict": "API_BREAK",
        "matrix_findings": [
            {"kind": "macro_guarded", "symbol": "FOO", "description": "z"},
        ],
        "unmatched_old": [],
        "unmatched_new": [],
    }
    model = build_model(report)
    # 2 bundle breaks, 1 matrix review, 0 per-library changes
    assert model.counts == (2, 1, 0)
    assert should_post(model, "changes") is True
    # the per-library library count excludes the synthetic global rows
    assert model.subject == "1 library"
    body = render_comment(model, sha="abc1234")
    assert "bundle checks" in body
    assert "build-config matrix" in body


def _release_global_report(config: dict) -> dict:
    return {
        "verdict": "COMPATIBLE",
        "old_dir": "/o",
        "new_dir": "/n",
        "libraries": [],
        "matrix_verdict": "COMPATIBLE",
        "matrix_findings": [
            {"kind": "func_added", "symbol": "probe_new", "description": "x"},
            {"kind": "soname_bump_unnecessary", "symbol": "libp", "description": "y"},
        ],
        "severity": {"config": config, "exit_code": 1},
        "unmatched_old": [],
        "unmatched_new": [],
    }


def test_release_global_addition_gate_promotes_only_additions():
    # addition gated → only the func_added finding is Breaking; the quality
    # finding stays Safe (gates are not interchangeable).
    model = build_model(_release_global_report({"addition": "error"}))
    assert model.counts == (1, 0, 1)


def test_release_global_quality_gate_promotes_only_quality():
    # quality gated → only the soname_bump_unnecessary finding is Breaking; the
    # addition (func_added) stays Safe even though it's in the same section.
    model = build_model(_release_global_report({"quality_issues": "error"}))
    assert model.counts == (1, 0, 1)
    assert should_post(model, "changes") is True


def test_release_global_ungated_compatible_stays_safe():
    # No matching gate → the whole compatible section stays Safe.
    model = build_model(_release_global_report({"potential_breaking": "error"}))
    assert model.counts == (0, 0, 2)


def test_release_errored_library_counts_as_breaking():
    # A library whose comparison errored has no count fields; it must still
    # register as a change so the failed comparison is reflected in the comment.
    report = {
        "verdict": "ERROR",
        "old_dir": "/o",
        "new_dir": "/n",
        "libraries": [{"library": "libbad.so", "verdict": "ERROR", "error": "boom"}],
        "unmatched_old": [],
        "unmatched_new": [],
    }
    model = build_model(report)
    assert model.counts == (1, 0, 0)
    assert should_post(model, "changes") is True


def test_release_added_libraries_rendered():
    report = {
        "verdict": "COMPATIBLE",
        "old_dir": "/pkg/old",
        "new_dir": "/pkg/new",
        "libraries": [],
        "unmatched_old": [],
        "unmatched_new": ["libnew.so.1"],
    }
    model = build_model(report)
    assert model.added_libraries == ["libnew.so.1"]
    body = render_comment(model, sha="abc1234", detail="full")
    assert "New libraries" in body
    assert "libnew.so.1" in body


def test_appcompat_missing_symbols_count_as_breaking():
    model = build_model(_appcompat_report())
    assert model.mode == "appcompat"
    # 1 relevant breaking change + 2 missing symbols
    assert model.counts == (3, 0, 0)
    assert model.subject == "myapp"
    assert any(f.symbol == "foo_legacy" for f in model.breaking)


def test_appcompat_missing_version_counts_as_breaking():
    # An app broken solely by a missing version tag must still register a change
    # so `--on changes` posts the comment.
    report = {
        "application": "/opt/app/bin/myapp",
        "old_library": "/lib/libfoo.so.1",
        "new_library": "/lib/libfoo.so.2",
        "verdict": "BREAKING",
        "missing_symbols": [],
        "missing_versions": ["LIBFOO_2.0"],
        "relevant_changes": [],
    }
    model = build_model(report)
    assert model.counts == (1, 0, 0)
    assert should_post(model, "changes") is True
    assert any(f.symbol == "LIBFOO_2.0" for f in model.breaking)


def test_gate_api_break_files_api_break_as_breaking():
    # With fail-on-api-break, the check goes red on api_break, so the comment
    # must file it under Breaking (not review) to match.
    report = _compare_report(
        [
            {
                "kind": "enum_member_added",
                "symbol": "E::X",
                "description": "d",
                "severity": "api_break",
            },
            {
                "kind": "type_field_added",
                "symbol": "S",
                "description": "d",
                "severity": "risk",
            },
        ]
    )
    gated = build_model(report, gate_api_break=True)
    assert gated.counts == (1, 1, 0)  # api_break → breaking, risk stays review
    body = render_comment(gated, sha="x")
    assert "ABI BREAKING" in body
    # default (ungated) keeps api_break in review
    ungated = build_model(report)
    assert ungated.counts == (0, 2, 0)


def test_gate_api_break_release_source_breaks_count_as_breaking():
    report = {
        "verdict": "API_BREAK",
        "old_dir": "/o",
        "new_dir": "/n",
        "libraries": [
            {
                "library": "lib.so",
                "verdict": "API_BREAK",
                "breaking": 0,
                "source_breaks": 2,
                "risk_changes": 1,
                "compatible_additions": 0,
            },
        ],
        "unmatched_old": [],
        "unmatched_new": [],
    }
    gated = build_model(report, gate_api_break=True)
    assert gated.counts == (2, 1, 0)  # source_breaks → breaking, risk → review
    ungated = build_model(report)
    assert ungated.counts == (0, 3, 0)  # source_breaks + risk → review


def test_severity_addition_error_files_additions_as_breaking():
    # With severity-addition: error the check goes red on additions, so the
    # comment must file them under Breaking (auto-detected from the report's
    # severity config), not Safe.
    report = _compare_report(
        [
            {
                "kind": "func_added",
                "symbol": "foo_new",
                "description": "new",
                "severity": "compatible",
            },
        ]
    )
    report["severity"] = {
        "config": {
            "abi_breaking": "error",
            "potential_breaking": "warning",
            "quality_issues": "warning",
            "addition": "error",
        },
        "categories": {},
        "exit_code": 1,
    }
    gated = build_model(report)
    assert gated.counts == (1, 0, 0)  # addition → breaking
    assert "ABI BREAKING" in render_comment(gated, sha="x")
    # without the severity config, the same addition stays safe
    plain = _compare_report(
        [
            {
                "kind": "func_added",
                "symbol": "foo_new",
                "description": "new",
                "severity": "compatible",
            }
        ]
    )
    assert build_model(plain).counts == (0, 0, 1)


def test_severity_addition_error_classifies_non_added_kinds():
    # Addition kinds that don't end in "_added" must still be treated as
    # additions (sourced from ADDITION_KINDS), so severity-addition: error files
    # them under Breaking rather than Safe/quality.
    for kind in ("type_field_added_compatible", "experimental_graduated"):
        report = _compare_report(
            [
                {
                    "kind": kind,
                    "symbol": "s",
                    "description": "d",
                    "severity": "compatible",
                }
            ]
        )
        report["severity"] = {"config": {"addition": "error"}, "exit_code": 1}
        assert build_model(report).counts == (1, 0, 0), kind


def test_severity_quality_error_release_quality_breaking():
    # compatible_additions conflates additions + quality; a quality-only gate
    # must move the quality subset (not the additions) to Breaking.
    report = {
        "verdict": "COMPATIBLE_WITH_RISK",
        "old_dir": "/o",
        "new_dir": "/n",
        "libraries": [
            {
                "library": "lib.so",
                "verdict": "COMPATIBLE_WITH_RISK",
                "breaking": 0,
                "source_breaks": 0,
                "risk_changes": 0,
                "compatible_additions": 5,
                "quality_issues": 2,
            },
        ],
        "severity": {
            "config": {"quality_issues": "error", "addition": "info"},
            "exit_code": 1,
        },
        "unmatched_old": [],
        "unmatched_new": [],
    }
    # quality (2) → breaking, additions (5-2=3) → safe
    assert build_model(report).counts == (2, 0, 3)


def test_severity_addition_error_release_additions_breaking():
    report = {
        "verdict": "COMPATIBLE",
        "old_dir": "/o",
        "new_dir": "/n",
        "libraries": [
            {
                "library": "lib.so",
                "verdict": "COMPATIBLE",
                "breaking": 0,
                "source_breaks": 0,
                "risk_changes": 0,
                "compatible_additions": 4,
            },
        ],
        "severity": {"config": {"addition": "error"}, "exit_code": 1},
        "unmatched_old": [],
        "unmatched_new": [],
    }
    assert build_model(report).counts == (4, 0, 0)


def test_malformed_changes_are_skipped():
    model = build_model(
        {
            "changes": [
                "not-a-dict",
                {"kind": "func_added", "symbol": "x", "severity": "compatible"},
            ]
        }
    )
    assert model.counts == (0, 0, 1)


# ---------------------------------------------------------------------------
# should_post
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "on,changes,expected",
    [
        ("never", True, False),
        ("always", False, True),
        ("changes", True, True),
        ("changes", False, False),
    ],
)
def test_should_post(on, changes, expected):
    report = _compare_report([] if not changes else None)
    model = build_model(report)
    assert should_post(model, on) is expected


def test_should_post_changes_true_when_library_removed():
    model = build_model(_release_report())
    # zero per-library would still post because a library was removed
    model.library_rows = []
    assert should_post(model, "changes") is True


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_render_includes_marker_header_sha_and_counts():
    body = render_comment(
        build_model(_compare_report()),
        sha="a1b2c3d4e5f6",
        detail="standard",
        run_label="run #128",
    )
    assert body.startswith(MARKER)
    assert "## ❌ abicheck — ABI BREAKING" in body
    assert "a1b2c3d" in body  # short sha in header
    assert "commit a1b2c3d" in body  # and footer
    assert "**2 breaking** · 1 needs review · 2 safe" in body
    assert "run #128" in body


def test_render_header_review_when_no_breaking():
    report = _compare_report(
        [
            {
                "kind": "enum_member_added",
                "symbol": "E::X",
                "description": "d",
                "severity": "api_break",
            },
        ]
    )
    body = render_comment(build_model(report), sha="deadbeef")
    assert "Review recommended" in body
    assert "⚠️" in body


def test_render_header_safe_only():
    report = _compare_report(
        [
            {
                "kind": "func_added",
                "symbol": "g",
                "description": "d",
                "severity": "compatible",
            },
        ]
    )
    body = render_comment(build_model(report), sha="deadbeef")
    assert "Compatible — safe changes only" in body


def test_render_header_no_changes():
    body = render_comment(build_model(_compare_report([])), sha="deadbeef")
    assert "No ABI changes" in body


def test_summary_detail_has_no_tables():
    body = render_comment(build_model(_compare_report()), sha="x", detail="summary")
    assert "<details" not in body
    assert "**2 breaking**" in body


def test_full_detail_expands_all_sections():
    body = render_comment(build_model(_compare_report()), sha="x", detail="full")
    # every <details> opens expanded in full mode
    assert "<details><summary>" not in body
    assert body.count("<details open>") == 3


def test_standard_truncates_large_breaking_table():
    changes = [
        {
            "kind": "func_removed",
            "symbol": f"sym_{i}",
            "description": "removed",
            "severity": "breaking",
        }
        for i in range(40)
    ]
    body = render_comment(build_model(_compare_report(changes)), sha="x")
    assert "more_" in body  # truncation marker
    # full mode keeps everything
    full = render_comment(build_model(_compare_report(changes)), sha="x", detail="full")
    assert "more_" not in full


def test_api_rollup_collapses_overload_and_member_families():
    # Many members of one type / overloads of one function collapse to a single
    # aggregated row in standard mode (mass-change readability).
    changes = [
        {"kind": "func_params_changed", "symbol": f"Widget::resize(int{i})",
         "description": "sig", "severity": "breaking"}
        for i in range(12)
    ]
    body = render_comment(build_model(_compare_report(changes)), sha="x")
    # one aggregated row labelled with the enclosing type and the member count
    assert "`Widget` (12)" in body
    # but full detail keeps every member as its own row (no rollup)
    full = render_comment(build_model(_compare_report(changes)), sha="x", detail="full")
    assert "`Widget` (12)" not in full
    assert "Widget::resize(int0)" in full


def test_api_rollup_keeps_distinct_symbols_flat():
    # Distinct, unrelated symbols are not aggregated — each keeps its own row.
    model = build_model(_compare_report())
    body = render_comment(model, sha="x")
    # both distinct breaking symbols keep their own flat row (no aggregation)
    assert "`foo_init`" in body
    assert "`struct Ctx`" in body


def test_api_rollup_strips_template_args():
    # Template instantiations of the same type collapse to one enclosing group.
    changes = [
        {"kind": "func_params_changed", "symbol": "Vec<int>::push(int)",
         "description": "s", "severity": "breaking"},
        {"kind": "func_params_changed", "symbol": "Vec<float>::push(float)",
         "description": "s", "severity": "breaking"},
    ]
    body = render_comment(build_model(_compare_report(changes)), sha="x")
    assert "`Vec` (2)" in body


def test_group_row_caps_inline_members():
    # An aggregated row lists members up to a cap, then "+N more".
    changes = [
        {"kind": "func_params_changed", "symbol": f"Api::call(int{i})",
         "description": "s", "severity": "breaking"}
        for i in range(11)
    ]
    body = render_comment(build_model(_compare_report(changes)), sha="x")
    assert "`Api` (11)" in body
    assert "+3 more" in body  # 11 members, 8 inline


def test_large_diff_condensed_note_links_report():
    # full overflows → auto-downgrade to standard with a condensed note + link.
    changes = [
        {"kind": "func_removed", "symbol": f"ns{i}::f", "description": "x" * 300,
         "severity": "breaking"} for i in range(2000)
    ]
    body = render_comment(
        build_model(_compare_report(changes)), sha="x", detail="full",
        report_url="https://e/run/2",
    )
    assert "Condensed to fit" in body
    assert "https://e/run/2" in body


def test_comment_hard_truncated_when_even_summary_overflows(monkeypatch):
    import abicheck.pr_comment as pc

    monkeypatch.setattr(pc, "_BODY_BUDGET", 220)
    body = render_comment(
        build_model(_compare_report()), sha="x", detail="full",
        report_url="https://e/run/1",
    )
    assert "truncated to fit" in body
    assert "https://e/run/1" in body
    assert len(body) <= pc._BODY_BUDGET


def test_comment_stays_under_github_size_limit():
    from abicheck.pr_comment import GITHUB_COMMENT_LIMIT

    changes = [
        {"kind": "func_removed", "symbol": f"ns{i}::free_{i}",
         "description": "x" * 200, "severity": "breaking"}
        for i in range(4000)
    ]
    body = render_comment(
        build_model(_compare_report(changes)), sha="x", detail="full",
        report_url="https://example/run/1",
    )
    assert len(body) <= GITHUB_COMMENT_LIMIT
    assert body.startswith(MARKER)  # header survives the downgrade/truncate


def test_backtick_in_symbol_neutralised():
    # A backtick in a symbol must not break the surrounding markdown code span.
    report = _compare_report(
        [{"kind": "func_removed", "symbol": "weird`sym", "description": "d",
          "severity": "breaking"}]
    )
    body = render_comment(build_model(report), sha="x")
    assert "weird`sym" not in body
    assert "weirdˋsym" in body


def test_report_url_parens_percent_encoded():
    # Parentheses in the report URL would terminate the markdown link target.
    body = render_comment(
        build_model(_compare_report()), sha="x", report_url="https://e/run(1)"
    )
    assert "https://e/run%281%29" in body


def test_report_url_linked_in_footer():
    body = render_comment(
        build_model(_compare_report()), sha="x", report_url="https://example/run/9"
    )
    assert "[full report](https://example/run/9)" in body


def test_release_render_lists_removed_libraries():
    body = render_comment(build_model(_release_report()), sha="cafe1234")
    assert "Libraries removed" in body
    assert "libgone.so.1" in body
    assert "Per-library results (2)" in body


def test_pipe_characters_escaped_in_cells():
    report = _compare_report(
        [
            {
                "kind": "func_params_changed",
                "symbol": "f(int|long)",
                "description": "a|b",
                "severity": "breaking",
            },
        ]
    )
    body = render_comment(build_model(report), sha="x")
    assert "f(int\\|long)" in body


def test_finding_with_only_location_renders_location_cell():
    report = _compare_report(
        [
            {
                "kind": "func_removed",
                "symbol": "f",
                "description": "",
                "severity": "breaking",
                "source_location": "foo.h:9",
            },
        ]
    )
    body = render_comment(build_model(report), sha="x")
    assert "foo.h:9" in body


def test_safe_section_caps_symbols_per_kind():
    changes = [
        {
            "kind": "func_added",
            "symbol": f"add_{i}",
            "description": "new",
            "severity": "compatible",
        }
        for i in range(20)
    ]
    body = render_comment(build_model(_compare_report(changes)), sha="x")
    assert "(+8)" in body  # 20 symbols, cap 12 → "+8" more


def test_release_full_detail_table_with_rows():
    body = render_comment(build_model(_release_report()), sha="x", detail="full")
    assert "Per-library results (2)" in body
    assert "libfoo.so.1" in body and "libbar.so.2" in body


def test_release_summary_detail_omits_table():
    body = render_comment(build_model(_release_report()), sha="x", detail="summary")
    assert "Per-library results" not in body
    assert "**2 breaking**" in body


def test_release_skips_non_dict_library_entries():
    report = {
        "verdict": "COMPATIBLE",
        "old_dir": "/o",
        "new_dir": "/n",
        "libraries": [
            "not-a-dict",
            {
                "library": "ok.so",
                "verdict": "COMPATIBLE",
                "breaking": 0,
                "source_breaks": 0,
                "compatible_additions": 1,
            },
        ],
        "unmatched_old": [],
        "unmatched_new": [],
    }
    model = build_model(report)
    assert len(model.library_rows) == 1
    assert model.counts == (0, 0, 1)


def test_empty_model_renders_clean_verdict():
    model = CommentModel(
        mode="compare", subject="lib", old_label="o", new_label="n", policy="strict_abi"
    )
    body = render_comment(model, sha="")
    assert "No ABI changes" in body
    assert MARKER in body


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------


def _run_cli(args):
    from click.testing import CliRunner

    from abicheck.cli import main

    return CliRunner().invoke(main, args)


def test_cli_pr_comment_writes_body(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_compare_report()), encoding="utf-8")
    out = tmp_path / "comment.md"
    result = _run_cli(["pr-comment", str(report), "--sha", "abc1234", "-o", str(out)])
    assert result.exit_code == 0
    body = out.read_text(encoding="utf-8")
    assert MARKER in body
    assert "ABI BREAKING" in body


def test_cli_pr_comment_skip_writes_empty_file(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(_compare_report([])), encoding="utf-8")
    out = tmp_path / "comment.md"
    result = _run_cli(["pr-comment", str(report), "--on", "changes", "-o", str(out)])
    assert result.exit_code == 0
    # nothing to post → empty file so the action's `-s` check skips
    assert out.read_text(encoding="utf-8") == ""


def test_cli_pr_comment_invalid_json_errors(tmp_path):
    report = tmp_path / "bad.json"
    report.write_text("{not json", encoding="utf-8")
    result = _run_cli(["pr-comment", str(report)])
    assert result.exit_code != 0
    assert "Cannot read JSON report" in result.output


def test_cli_pr_comment_non_object_errors(tmp_path):
    report = tmp_path / "arr.json"
    report.write_text("[1, 2, 3]", encoding="utf-8")
    result = _run_cli(["pr-comment", str(report)])
    assert result.exit_code != 0
    assert "must be an object" in result.output
