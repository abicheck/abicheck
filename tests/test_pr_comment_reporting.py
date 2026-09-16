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

"""Reporting-fidelity contract for the PR comment.

These tests state *invariants* about what survives the
completed-result -> JSON -> comment-model -> Markdown path, rather than
pinning one rendered snapshot per change kind. The bug class they close is
one this repository has hit repeatedly (AGENTS.md, "A bug fix's regression
test targets the bug *class*"): a report field that exists in the JSON, is
read by the HTML report, and is silently dropped by the comment adapter --
where "silently" is the operative word, because 151 focused comment tests
passed while confidence, coverage warnings and every one-sided value were
being discarded.

So the central test here is not "an added enum member's value 3 appears".
It is :func:`test_every_one_sided_value_survives_rendering`, which
enumerates the whole old/new x present/absent/zero/false/empty-string
product and asserts each cell is distinguishable in the output -- against an
oracle written from the *requirement* ("a value the report states is
visible, and empty is not absent"), not from ``_detail_text``'s own
formatting.
"""

from __future__ import annotations

import json

import pytest

from abicheck.confidence import detector_disablement_warning
from abicheck.pr_comment import build_model, should_post
from abicheck.pr_comment_base import Finding
from abicheck.pr_comment_render import (
    _BODY_BUDGET,
    GITHUB_COMMENT_LIMIT,
    render_comment,
)
from abicheck.report.change_summary import summarize_changes
from abicheck.report.evidence_summary import evidence_summary

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def _report(changes: list[dict] | None = None, **extra: object) -> dict:
    report: dict = {
        "verdict": "COMPATIBLE",
        "library": "libfoo.so",
        "old_version": "v1.0.0",
        "new_version": "v1.1.0",
        "policy": "strict_abi",
        "changes": changes or [],
    }
    report.update(extra)
    return report


def _body(report: dict, **kw: object) -> str:
    return render_comment(build_model(report), sha="abcdef1234", **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Acceptance case 1 -- four additions, the enum's new value 3 is visible
# ---------------------------------------------------------------------------

_FOUR_ADDITIONS = [
    {
        "kind": "func_added",
        "symbol": "foo_open",
        "description": "new function",
        "new_value": "foo_open",
        "severity": "compatible",
        "entity": "function",
        "operation": "added",
    },
    {
        "kind": "func_added",
        "symbol": "foo_close",
        "description": "new function",
        "severity": "compatible",
        "entity": "function",
        "operation": "added",
    },
    {
        "kind": "var_added",
        "symbol": "foo_default_flags",
        "description": "new variable",
        "severity": "compatible",
        "entity": "variable",
        "operation": "added",
    },
    {
        "kind": "enum_member_added",
        "symbol": "Color::PURPLE",
        "description": "new enumerator",
        "new_value": "3",
        "severity": "compatible",
        "entity": "enum",
        "operation": "added",
    },
]


@pytest.mark.parametrize("detail", ["standard", "full"])
def test_added_enum_member_value_is_visible(detail: str) -> None:
    body = _body(_report(_FOUR_ADDITIONS), detail=detail)
    assert "→ 3" in body, "an added enumerator's own value must reach the comment"


def test_entity_by_operation_summary_counts_four_additions() -> None:
    body = _body(_report(_FOUR_ADDITIONS))
    assert "📋 What changed (4 findings)" in body
    assert "| Functions | 0 | 0 | 2 |" in body
    assert "| Variables | 0 | 0 | 1 |" in body
    assert "| Enums | 0 | 0 | 1 |" in body


def test_change_summary_states_its_counting_unit() -> None:
    """The table counts findings, and must say so -- conflating findings,
    declarations and unique symbols is how a reader mis-reads every row."""
    assert summarize_changes(_FOUR_ADDITIONS).unit == "findings"
    two_findings_one_symbol = [
        {
            "kind": "func_removed",
            "symbol": "s",
            "entity": "function",
            "operation": "removed",
        },
        {
            "kind": "func_parameter_type_changed",
            "symbol": "s",
            "entity": "function",
            "operation": "modified",
        },
    ]
    summary = summarize_changes(two_findings_one_symbol)
    assert summary.counted == 2, "two findings about one symbol are two findings"


def test_change_summary_uses_canonical_classification_not_name_matching() -> None:
    """A kind whose *name* ends in ``_added`` while naming a trait gained by
    a persisting entity must not be counted as an addition -- the defect the
    canonical ``ChangeKindMeta`` dimensions exist to prevent."""
    summary = summarize_changes([{"kind": "type_field_added"}])
    row = summary.rows[0]
    assert row.added == 0 and row.modified == 1


# ---------------------------------------------------------------------------
# Acceptance case 2 -- one-sided / zero / false / empty-string / missing
# ---------------------------------------------------------------------------

#: (id, change fields, what a reader must be able to tell from the row).
#: The oracle is the *requirement*, deliberately not `_detail_text`'s own
#: formatting: each case names the substrings that must and must not appear.
_VALUE_CASES = [
    ("both_sides", {"old_value": "16", "new_value": "24"}, ["16", "24"], []),
    ("new_only", {"new_value": "3"}, ["3"], []),
    ("old_only", {"old_value": "7"}, ["7"], []),
    ("new_zero", {"new_value": 0}, ["0"], []),
    ("old_zero", {"old_value": 0}, ["0"], []),
    ("both_zero", {"old_value": 0, "new_value": 0}, ["0 → 0"], []),
    ("new_false", {"new_value": False}, ["false"], []),
    (
        "old_true_new_false",
        {"old_value": True, "new_value": False},
        ["true → false"],
        [],
    ),
    ("new_empty_string", {"new_value": ""}, ['""'], []),
    ("old_empty_new_value", {"old_value": "", "new_value": "x"}, ['"" → x'], []),
    ("explicit_null_is_absent", {"old_value": None, "new_value": None}, [], ["→"]),
    ("missing_is_absent", {}, [], ["→"]),
]


@pytest.mark.parametrize(
    "case_id,fields,must_appear,must_not_appear",
    _VALUE_CASES,
    ids=[c[0] for c in _VALUE_CASES],
)
def test_every_one_sided_value_survives_rendering(
    case_id: str, fields: dict, must_appear: list[str], must_not_appear: list[str]
) -> None:
    change = {
        "kind": "type_size_changed",
        "symbol": "struct Ctx",
        "description": "layout",
        "severity": "breaking",
        **fields,
    }
    body = _body(_report([change]), detail="full")
    row = next(ln for ln in body.splitlines() if "struct Ctx" in ln)
    for needle in must_appear:
        assert needle in row, f"{case_id}: {needle!r} missing from {row!r}"
    for needle in must_not_appear:
        assert needle not in row, f"{case_id}: {needle!r} should not appear in {row!r}"


def test_zero_and_absent_render_differently() -> None:
    """The distinction the superseded ``not in (None, "")`` test collapsed."""
    zero = _body(
        _report(
            [
                {
                    "kind": "type_size_changed",
                    "symbol": "S",
                    "new_value": 0,
                    "severity": "breaking",
                }
            ]
        ),
        detail="full",
    )
    absent = _body(
        _report([{"kind": "type_size_changed", "symbol": "S", "severity": "breaking"}]),
        detail="full",
    )
    assert zero != absent


def test_value_repeating_the_symbol_is_not_repeated_in_the_row() -> None:
    body = _body(_report(_FOUR_ADDITIONS), detail="full")
    row = next(ln for ln in body.splitlines() if "foo_open" in ln)
    assert row.count("foo_open") == 1


# ---------------------------------------------------------------------------
# Acceptance case 4/5 -- evidence, coverage limits, diagnostic-only posting
# ---------------------------------------------------------------------------

_COVERAGE_LIMITED = _report(
    [],
    confidence="medium",
    evidence_tier="L1",
    evidence_tiers=["L0", "L1"],
    coverage_warnings=["No header/AST data; type-level changes may be missed."],
    detectors=[
        {
            "name": "pe_metadata",
            "changes_count": 0,
            "not_evaluated": True,
            "coverage_gap": "not a PE artifact",
        },
    ],
)


def test_confidence_and_coverage_warning_reach_the_comment() -> None:
    body = _body(_COVERAGE_LIMITED, detail="standard")
    assert "Confidence: **medium**" in body
    assert "No header/AST data" in body


def test_confidence_is_never_invented_when_the_report_states_none() -> None:
    body = _body(_report(_FOUR_ADDITIONS))
    assert "Confidence" not in body
    assert evidence_summary(_report(_FOUR_ADDITIONS)).confidence is None


def test_zero_findings_plus_actionable_diagnostic_still_posts() -> None:
    model = build_model(_COVERAGE_LIMITED)
    assert model.total_changes == 0
    assert should_post(model, "changes") is True
    assert "No header/AST data" in render_comment(model, sha="a")


#: The *real* shape a clean ELF-vs-ELF comparison of two ordinary shared
#: libraries produces: eight `coverage_warnings`, every one of them a
#: permanently-disabled platform detector. Captured from an actual
#: `abicheck compare` run rather than hand-written, because the whole point
#: is that the production list is not the "material limitations" list it
#: looks like.
_ROUTINE_DISABLED = [
    ("fingerprint_renames", "requires ELF metadata in elf_only_mode"),
    ("kabi", "missing Module.symvers (kABI) metadata"),
    ("pe", "missing PE metadata"),
    ("macho", "missing Mach-O metadata"),
    ("python_ext", "missing CPython extension metadata"),
    ("python_api", "missing Python API surface (no .pyi stub recovered)"),
    ("sycl", "missing SYCL metadata"),
    ("vtable_layout", "missing DWARF/header type metadata (inheritance)"),
]


def _routine_elf_run(changes: list[dict] | None = None, **extra: object) -> dict:
    return _report(
        changes or [],
        confidence="high",
        evidence_tiers=["elf", "dwarf", "header"],
        coverage_warnings=[
            detector_disablement_warning(n, g) for n, g in _ROUTINE_DISABLED
        ],
        detectors=[
            {"name": n, "changes_count": 0, "enabled": False, "coverage_gap": g}
            for n, g in _ROUTINE_DISABLED
        ],
        **extra,
    )


def test_inapplicable_detector_alone_does_not_post_on_a_clean_run() -> None:
    """An ELF comparison's PE/Mach-O/kABI/SYCL detectors are disabled on
    every run, forever. Treating that as a reason to comment would put
    routine noise on every clean PR -- and treating it as a *limitation*
    would put a "Limits on what was checked (8)" block there too."""
    model = build_model(_routine_elf_run())
    assert model.evidence is not None
    assert model.evidence.coverage_warnings == ()
    assert len(model.evidence.detector_disablement_warnings) == 8
    assert should_post(model, "changes") is False


def test_a_material_limitation_beside_routine_noise_still_posts() -> None:
    """The split must not throw the signal out with the noise: the same run
    plus one genuine warning is a run that must comment."""
    report = _routine_elf_run()
    report["coverage_warnings"] = [
        *report["coverage_warnings"],  # type: ignore[misc]
        "No header/AST data; type-level changes may be missed.",
    ]
    model = build_model(report)
    assert model.evidence is not None
    assert model.evidence.coverage_warnings == (
        "No header/AST data; type-level changes may be missed.",
    )
    assert should_post(model, "changes") is True


def test_unrecognised_warning_is_never_dropped() -> None:
    """The split fails safe: a warning no detector accounts for stays
    material rather than silently vanishing."""
    report = _routine_elf_run()
    report["coverage_warnings"] = ["something new this build emits"]
    model = build_model(report)
    assert model.evidence is not None
    assert model.evidence.coverage_warnings == ("something new this build emits",)


def test_detector_applicability_is_labelled_as_not_run_not_as_missing_coverage() -> (
    None
):
    body = _body(_COVERAGE_LIMITED, detail="standard")
    assert "did not run" in body
    assert "pe_metadata" in body
    assert "missing required" not in body.lower()


def test_never_stays_authoritative_even_with_diagnostics() -> None:
    assert should_post(build_model(_COVERAGE_LIMITED), "never") is False


def test_clean_complete_run_posts_nothing_under_changes() -> None:
    assert should_post(build_model(_report([])), "changes") is False
    assert should_post(build_model(_report([])), "always") is True


def test_same_binary_comparison_reports_no_changes_not_an_error() -> None:
    same = _report([], verdict="NO_CHANGE", old_version="v1.0.0", new_version="v1.0.0")
    body = render_comment(build_model(same), sha="a")
    assert "0 breaking" in body


# ---------------------------------------------------------------------------
# Acceptance case 7 -- no-baseline audit
# ---------------------------------------------------------------------------


def test_no_baseline_audit_invents_no_old_new_verdict() -> None:
    audit = {
        "audit_report_schema_version": "1.0",
        "library": "libfoo.so",
        "new_version": "v1.1.0",
        "policy": "strict_abi",
        "exit_code": 0,
        "changes": [],
    }
    body = render_comment(build_model(audit), sha="a")
    assert "vs `" not in body
    assert "no baseline" in body.lower()


# ---------------------------------------------------------------------------
# Acceptance case 8/10 -- scale, grouping, and truncation honesty
# ---------------------------------------------------------------------------


def _many(
    n: int, kind: str = "func_added", severity: str = "compatible", ns: str = ""
) -> list[dict]:
    return [
        {
            "kind": kind,
            "symbol": f"{ns}sym_{i}",
            "description": f"change {i}",
            "severity": severity,
            "source_location": f"include/foo.h:{i}",
        }
        for i in range(n)
    ]


def test_thirty_related_additions_keep_a_route_to_every_member() -> None:
    body = _body(_report(_many(30, ns="ns::Widget::")), detail="standard")
    assert "All grouped members (30)" in body
    for i in range(30):
        assert f"ns::Widget::sym_{i}" in body, (
            f"member {i} unreachable from the comment"
        )


def test_one_thousand_findings_keep_per_symbol_rows_under_full_detail() -> None:
    body = _body(
        _report(_many(1000, kind="func_removed", severity="breaking")), detail="full"
    )
    assert len(body) <= _BODY_BUDGET <= GITHUB_COMMENT_LIMIT
    rows = [ln for ln in body.splitlines() if ln.startswith("| `func_removed`")]
    assert len(rows) > 25, "a global downgrade to 25 grouped rows loses the detail"
    # The header's counts stay exact regardless of what the tables showed.
    assert "**1000 breaking**" in body
    assert "📋 What changed (1000 findings)" in body


def test_omitted_row_count_is_exact_and_links_onward() -> None:
    body = _body(
        _report(_many(1000, kind="func_removed", severity="breaking")),
        detail="full",
        report_url="https://e/run/1",
    )
    omitted = next(ln for ln in body.splitlines() if "more not shown" in ln)
    shown = len([ln for ln in body.splitlines() if ln.startswith("| `func_removed`")])
    assert f"{1000 - shown} more not shown" in omitted
    assert "https://e/run/1" in body


def test_shortened_body_keeps_headline_counts_and_navigation() -> None:
    body = _body(
        _report(_many(4000, kind="func_removed", severity="breaking")),
        detail="full",
        report_url="https://e/run/1",
        report_artifact_url="https://e/run/1/artifacts/7",
    )
    assert len(body) <= _BODY_BUDGET
    assert "**4000 breaking**" in body
    assert "Head `abcdef1`" in body
    assert "Download full report" in body
    assert body.count("<details") == body.count("</details>"), "unbalanced <details>"


def test_truncated_body_is_structurally_valid_markdown() -> None:
    huge = [
        {
            "kind": "func_removed",
            "symbol": "x" * 400 + str(i),
            "description": "y" * 400,
            "severity": "breaking",
        }
        for i in range(400)
    ]
    body = render_comment(build_model(_report(huge)), sha="a", detail="full")
    assert len(body) <= GITHUB_COMMENT_LIMIT
    assert body.count("<details") == body.count("</details>")
    assert not body.rstrip().endswith("|"), "cut mid-table-row"


# ---------------------------------------------------------------------------
# Acceptance case 11 -- markdown-sensitive text
# ---------------------------------------------------------------------------


def test_markdown_sensitive_values_are_escaped() -> None:
    body = _body(
        _report(
            [
                {
                    "kind": "type_size_changed",
                    "symbol": "evil`|name",
                    "description": "a|b\nc",
                    "old_value": "`x`",
                    "new_value": "|y",
                    "severity": "breaking",
                }
            ]
        ),
        detail="full",
    )
    row = next(ln for ln in body.splitlines() if "evil" in ln)
    assert "`|" not in row.replace("\\|", "")
    assert row.count("|") - row.count("\\|") == 4, "cell boundaries only"


# ---------------------------------------------------------------------------
# Acceptance case 13/14 -- links, and the same facts across formats
# ---------------------------------------------------------------------------


def test_workflow_run_and_artifact_links_are_distinct() -> None:
    body = _body(
        _report(_FOUR_ADDITIONS),
        report_url="https://e/run/1",
        report_artifact_url="https://e/a/2",
    )
    assert "[View workflow run](https://e/run/1)" in body
    assert "[Download full report](https://e/a/2)" in body


def test_no_artifact_link_when_no_upload_url_supplied() -> None:
    body = _body(_report(_FOUR_ADDITIONS), report_url="https://e/run/1")
    assert "Download full report" not in body


def test_comment_generation_runs_no_extraction_or_comparison(monkeypatch) -> None:
    """Rendering is a projection of an already-completed result. It must not
    re-run the pipeline: the Action renders inside the same job that already
    paid for the analysis, and a second execution would both double the cost
    and be able to disagree with the report it is supposed to be showing."""
    import abicheck.checker as checker
    import abicheck.dumper as dumper

    calls: list[str] = []
    monkeypatch.setattr(checker, "compare", lambda *a, **k: calls.append("compare"))
    monkeypatch.setattr(dumper, "dump", lambda *a, **k: calls.append("dump"))
    render_comment(build_model(_report(_FOUR_ADDITIONS)), sha="a", detail="full")
    assert calls == []


def test_json_and_comment_agree_on_the_same_facts() -> None:
    """Equivalent-information check across formats: every fact the comment
    shows is read from the JSON, so the two cannot state different values."""
    report = _report(_FOUR_ADDITIONS, confidence="high", evidence_tiers=["L0", "L2"])
    round_tripped = json.loads(json.dumps(report))
    model = build_model(round_tripped)
    assert model.evidence is not None
    assert model.evidence.confidence == round_tripped["confidence"]
    assert list(model.evidence.evidence_tiers) == round_tripped["evidence_tiers"]
    assert model.change_summary is not None
    assert model.change_summary.counted == len(round_tripped["changes"])


# ---------------------------------------------------------------------------
# Acceptance case 9 -- suppression / reclassification stay visible
# ---------------------------------------------------------------------------


def test_suppressed_findings_stay_visible_and_post() -> None:
    report = _report([], suppression={"suppressed_count": 12})
    model = build_model(report)
    assert should_post(model, "changes") is True
    assert "12" in render_comment(model, sha="a")


# ---------------------------------------------------------------------------
# Acceptance case 8 -- bundle totals are never reconstructed from a cap
# ---------------------------------------------------------------------------


def test_bundle_change_summary_is_declared_inexact() -> None:
    release = {
        "libraries": [
            {
                "library": "liba.so",
                "verdict": "BREAKING",
                "breaking": 120,
                "findings": [{"kind": "func_removed", "symbol": "a"}],
            }
        ],
    }
    model = build_model(release)
    assert model.change_summary is not None
    assert model.change_summary.exact is False
    body = render_comment(model, sha="a")
    assert "Not exact totals" in body


# ---------------------------------------------------------------------------
# Grouping must never be a dead end, and never aggregate one symbol with itself
# ---------------------------------------------------------------------------


def test_two_findings_on_one_symbol_are_not_rolled_into_one_row() -> None:
    """The rollup summarises *entities*. Two findings about one function
    have nothing to summarise: the aggregated row read
    "`foo_init`, `foo_init`" and dropped both findings' own values."""
    changes = [
        {
            "kind": "func_return_changed",
            "symbol": "foo_init",
            "description": "Return type changed",
            "old_value": "int",
            "new_value": "long",
            "severity": "breaking",
        },
        {
            "kind": "func_params_changed",
            "symbol": "foo_init",
            "description": "Parameters changed",
            "old_value": "int",
            "new_value": "long",
            "severity": "breaking",
        },
    ]
    body = _body(_report(changes, verdict="BREAKING"), detail="standard")
    assert "`foo_init`, `foo_init`" not in body
    assert "int → long" in body
    assert body.count("int → long") == 2


@pytest.mark.parametrize("group_size", [2, 3, 9, 30])
def test_every_aggregated_group_keeps_a_route_to_member_detail(group_size: int) -> None:
    """Not just the groups big enough to be cut: a two-member aggregated row
    lists both names and still loses both findings' detail."""
    changes = [
        {
            "kind": "func_added",
            "symbol": f"ns::Widget::m{i}",
            "description": f"detail {i}",
            "severity": "compatible",
        }
        for i in range(group_size)
    ]
    body = _body(_report(changes), detail="standard")
    for i in range(group_size):
        assert f"detail {i}" in body, f"member {i}'s own detail is unreachable"


# ---------------------------------------------------------------------------
# Runner-specific absolute paths
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "location,prefix,expected",
    [
        ("/ws/include/foo.h:7", "/ws", "include/foo.h:7"),
        ("/ws/include/foo.h:7", "/ws/", "include/foo.h:7"),
        # A sibling directory sharing the root's name prefix is not under it.
        ("/wsx/include/foo.h:7", "/ws", "/wsx/include/foo.h:7"),
        # Outside the checkout: left exactly as the report gave it.
        ("/usr/include/stdio.h:1", "/ws", "/usr/include/stdio.h:1"),
        # No prefix supplied: unchanged.
        ("/ws/include/foo.h:7", "", "/ws/include/foo.h:7"),
    ],
)
def test_path_prefix_stripping_is_component_aligned(
    location: str, prefix: str, expected: str
) -> None:
    change = {
        "kind": "func_removed",
        "symbol": "s",
        "description": "gone",
        "severity": "breaking",
        "source_location": location,
    }
    model = build_model(_report([change], verdict="BREAKING"), path_prefix=prefix)
    assert model.breaking[0].location == expected


def test_large_report_does_not_collapse_to_a_summary_body() -> None:
    """The grouped-members block is a *route* to detail, not a detail dump.

    An unbounded one made a 1000-finding standard-detail body overflow the
    comment budget at every row budget, so the shortening plan fell all the
    way through to the summary level: measured, that turned ~15 KB of tables
    into a 440-byte body with no findings at all -- strictly less
    information than the grouped rows this block was added to complete.
    """
    body = _body(
        _report(
            _many(1000, kind="func_removed", severity="breaking", ns="ns::C::"),
            verdict="BREAKING",
        ),
        detail="standard",
    )
    assert len(body) > 5000, "body collapsed past every detail section"
    assert "❌ Breaking" in body
    assert "**1000 breaking**" in body


# ---------------------------------------------------------------------------
# Review round 1 (CodeRabbit): each of these is a way this work could
# reacquire the very defect it exists to fix -- an authoritative fact the
# report states and the comment does not show, or shows a wrong number for.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,description",
    [
        ("0", "10 fields were reordered"),
        ("1", "21 members"),
        ("2", "a 1234-byte buffer"),
        ("x", "the xyz accessor"),
    ],
)
def test_a_value_is_never_dropped_for_being_a_description_substring(
    value: str, description: str
) -> None:
    """The de-duplication rule must be a whole-value match.

    A substring test reacquires this PR's own bug: a one-sided ``0`` is a
    substring of "10 fields", so the row would render the description alone
    and say nothing about the value the report actually carried.
    """
    change = {
        "kind": "type_size_changed",
        "symbol": "struct Ctx",
        "description": description,
        "new_value": value,
        "severity": "breaking",
    }
    body = _body(_report([change], verdict="BREAKING"), detail="full")
    row = next(ln for ln in body.splitlines() if "struct Ctx" in ln)
    assert f"→ {value}" in row


def test_a_two_sided_delta_the_description_already_spells_is_not_repeated() -> None:
    """The one case the rule does suppress, matched as a whole phrase."""
    change = {
        "kind": "type_size_changed",
        "symbol": "Ctx",
        "description": "Size changed: Ctx (64 → 96 bits)",
        "old_value": "64",
        "new_value": "96",
        "severity": "breaking",
    }
    body = _body(_report([change], verdict="BREAKING"), detail="full")
    row = next(ln for ln in body.splitlines() if "Ctx" in ln and "Size changed" in ln)
    assert row.count("64 → 96") == 1


@pytest.mark.parametrize(
    "location,prefix,expected",
    [
        # A Windows runner: $GITHUB_WORKSPACE is native, the header-AST
        # backend reports forward slashes. Comparing them raw never matched,
        # so the absolute runner path stayed in the comment.
        ("D:/a/repo/repo/include/foo.h:7", "D:\\a\\repo\\repo", "include/foo.h:7"),
        (
            "D:\\a\\repo\\repo\\include\\foo.h:7",
            "D:\\a\\repo\\repo",
            "include\\foo.h:7",
        ),
        ("D:/a/repo/repo/include/foo.h:7", "D:/a/repo/repo", "include/foo.h:7"),
        # Still component-aligned after normalization.
        (
            "D:/a/repo/repo-extra/foo.h:7",
            "D:\\a\\repo\\repo",
            "D:/a/repo/repo-extra/foo.h:7",
        ),
    ],
)
def test_path_prefix_stripping_survives_mixed_separators(
    location: str, prefix: str, expected: str
) -> None:
    change = {
        "kind": "func_removed",
        "symbol": "s",
        "description": "gone",
        "severity": "breaking",
        "source_location": location,
    }
    model = build_model(_report([change], verdict="BREAKING"), path_prefix=prefix)
    assert model.breaking[0].location == expected


@pytest.mark.parametrize("shape", ["no_baseline", "appcompat"])
def test_path_prefix_reaches_every_finding_bearing_report_shape(shape: str) -> None:
    """`compare` was wired; the other two shapes carry `source_location`
    findings too and were left showing absolute runner paths."""
    finding = {
        "kind": "func_removed",
        "symbol": "s",
        "description": "gone",
        "source_location": "/ws/include/foo.h:7",
    }
    if shape == "no_baseline":
        report = {
            "audit_report_schema_version": "1.0",
            "library": "libfoo.so",
            "exit_code": 0,
            "findings": [{**finding, "verdict": "BREAKING"}],
        }
    else:
        report = {
            "application": "app",
            "relevant_changes": [{**finding, "severity": "breaking"}],
        }
    model = build_model(report, path_prefix="/ws")
    assert model.breaking[0].location == "include/foo.h:7"


def test_no_baseline_change_summary_is_built_from_the_findings_it_renders() -> None:
    """An audit's findings live under `findings`; its `changes` is always
    empty. Summarizing the latter produced an empty rollup beside populated
    buckets -- the summary contradicting its own detail sections."""
    report = {
        "audit_report_schema_version": "1.0",
        "library": "libfoo.so",
        "exit_code": 0,
        "findings": [
            {"kind": "func_removed", "symbol": "a", "verdict": "BREAKING"},
            {"kind": "var_added", "symbol": "b", "verdict": "COMPATIBLE"},
        ],
    }
    model = build_model(report)
    assert model.change_summary is not None
    assert model.change_summary.counted == 2
    assert "📋 What changed (2 findings)" in render_comment(model, sha="a")


def test_appcompat_change_summary_counts_the_findings_it_invents() -> None:
    """`missing_symbols`/`missing_versions` are not `Change`s in the report;
    the comment turns each into a Breaking finding, so the rollup must see
    them or it undercounts exactly what the Breaking section shows."""
    report = {
        "application": "app",
        "relevant_changes": [
            {"kind": "func_removed", "symbol": "a", "severity": "breaking"}
        ],
        "missing_symbols": ["sym_a", "sym_b"],
        "missing_versions": ["V_1.0"],
    }
    model = build_model(report)
    assert model.change_summary is not None
    assert model.change_summary.counted == len(model.breaking) == 4


@pytest.mark.parametrize("cap", [1, 3, 7, 20])
def test_a_section_never_renders_more_rows_than_its_budget(cap: int) -> None:
    """A group is not a row: a family whose members all name one symbol
    expands to one row per member, so capping group *keys* let a section
    exceed its budget outright."""
    from abicheck.pr_comment_render import _findings_table

    findings = [
        Finding(kind=f"k{i}", symbol="ns::C::one", detail=f"d{i}", severity="breaking")
        for i in range(30)
    ]
    out = _findings_table(
        "T", findings, "standard", open_default=True, row_cap=cap, report_url=None
    )
    rows = [ln for ln in out if ln.startswith("| `") or ln.startswith("| … |")]
    data_rows = [ln for ln in rows if not ln.startswith("| … |")]
    assert len(data_rows) <= cap


@pytest.mark.parametrize("cap", [1, 3, 7, 20])
def test_the_omission_notice_counts_findings_not_groups(cap: int) -> None:
    """The section header counts findings, so its omission notice must too;
    counting groups put two different quantities under one number."""
    from abicheck.pr_comment_render import _findings_table

    findings = [
        Finding(
            kind="func_removed", symbol=f"ns{i}::f", detail="d", severity="breaking"
        )
        for i in range(30)
    ]
    out = _findings_table(
        "T", findings, "standard", open_default=True, row_cap=cap, report_url=None
    )
    notice = next(ln for ln in out if "more not shown" in ln)
    assert f"{30 - cap} more not shown" in notice


def test_informational_section_respects_the_row_budget_at_full_detail() -> None:
    from abicheck.pr_comment_render import _safe_section

    findings = [
        Finding(kind="public_surface_grew", symbol=f"s{i}", detail="d")
        for i in range(50)
    ]
    out = _safe_section(findings, "full", 10, None)
    data_rows = [ln for ln in out if ln.startswith("| `")]
    assert len(data_rows) == 10
    assert any("40 more not shown" in ln for ln in out)
