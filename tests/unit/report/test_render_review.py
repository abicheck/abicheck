from __future__ import annotations

from dataclasses import replace

from abicheck.policy.disposition_ledger import RuleProvenance
from abicheck.pr_comment_base import CommentModel
from abicheck.report.disposition_audit import DispositionAudit
from abicheck.report.pr_comment_group_summary import suppression_note
from abicheck.report.render_review import (
    ImpactedSymbol,
    ReviewDigest,
    render_review_digest,
    render_terminal_digest,
)


def test_direct_severity_digest_reports_the_computed_gate() -> None:
    from abicheck.checker_policy import ChangeKind, Verdict
    from abicheck.checker_types import Change, DiffResult
    from abicheck.reporter import to_review_digest
    from abicheck.severity import resolve_severity_config

    result = DiffResult(
        "1",
        "2",
        "libx.so",
        changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
        verdict=Verdict.BREAKING,
    )
    blocking = resolve_severity_config("default")
    nonblocking = resolve_severity_config("default", abi_breaking="info")
    assert "**Gate:** FAIL (exit 4)" in to_review_digest(
        result, severity_config=blocking
    )
    assert "**Gate:** PASS (exit 0)" in to_review_digest(
        result, severity_config=nonblocking
    )


def test_show_only_copies_still_build_review_groups_in_json_and_markdown() -> None:
    import json

    from abicheck.checker_policy import ChangeKind, Verdict
    from abicheck.checker_types import Change, DiffResult
    from abicheck.reporter import to_json, to_markdown

    layout = Change(
        ChangeKind.LAYOUT_UNVERIFIABLE,
        "Foo",
        "layout evidence unverifiable",
        qualified_name="Foo",
        correlated_change_kind=ChangeKind.TYPE_VTABLE_CHANGED.value,
    )
    vtable = Change(
        ChangeKind.TYPE_VTABLE_CHANGED, "Foo", "vtable changed", qualified_name="Foo"
    )
    result = DiffResult("1", "2", "libx.so", [layout, vtable], Verdict.BREAKING)
    report = json.loads(to_json(result, show_only="risk"))
    assert [group["member_kinds"] for group in report["review_groups"]] == [
        ["layout_unverifiable"]
    ]
    assert "## Related review groups" in to_markdown(result, show_only="risk")


def _counts() -> dict[str, int]:
    return {
        "raw_detected": 12,
        "retained": 9,
        "gating": 2,
        "non_gating": 7,
        "review_groups": 9,
        "gating_review_groups": 1,
        "public_additions": 1,
        "public_removals": 1,
        "public_modifications": 0,
        "detected_public_additions": 1,
        "detected_public_removals": 4,
        "detected_public_modifications": 0,
        "runtime_dependency": 2,
        "hygiene_introduced": 1,
        "hygiene_resolved": 2,
        "hygiene_persistent": 3,
        "hygiene_not_evaluated": 1,
    }


def _groups() -> tuple[dict[str, object], ...]:
    return tuple(
        {
            "display_name": "N::" + ("VeryLong" * 30) + str(index),
            "transition": "changed\n" + ("detail " * 60),
            "consequence": "consumer impact " * 30,
            "action": "review " * 50,
            "member_kinds": ["func_removed"],
            "gating_findings": int(index == 0),
        }
        for index in range(9)
    )


def _digest(**updates: object) -> ReviewDigest:
    base = ReviewDigest(
        library="libx.so",
        old_version="1",
        new_version="2",
        verdict_emoji="❌",
        verdict_label="BREAKING",
        effect="review required",
        manual_review_banner=True,
        coverage_warnings=("asymmetric evidence",),
        additions_label="Public additions",
        breaking_count=2,
        source_breaks_count=0,
        risk_count=1,
        additions_count=1,
        scoped=True,
        out_of_surface_count=3,
        bump_value="major",
        soname_value="bump",
        impacted=(),
        disposition_audit=DispositionAudit(
            12,
            2,
            (("suppressed", 10),),
            tuple(
                (RuleProvenance(rule_id=f"r{i}", reason="accepted"), i + 1)
                for i in range(6)
            ),
            (),
        ),
        quality_issues_count=1,
        env_matrix_source_sha256="abc",
        review_groups=_groups(),
        gate_exit_code=None,
        result_counts=_counts(),
        evidence_summary="ELF and headers",
        show_release_recommendation=True,
    )
    return replace(base, **updates)


def test_terminal_covers_bounded_groups_audit_and_predisposition_counts() -> None:
    text = render_terminal_digest(_digest())
    assert "Gate: not configured" in text
    assert "Detected public operations before disposition" in text
    assert "1 more groups omitted" in text
    assert "2 more suppression rules omitted" in text
    assert "…" in text


def test_markdown_covers_optional_release_environment_and_impacted_sections() -> None:
    text = render_review_digest(_digest())
    assert "Manual review required" in text
    assert "Release recommendation" in text
    assert "Deployment floor digest" in text
    assert "1 more group(s) omitted" in text

    impacted = tuple(ImpactedSymbol(f"sym{i}", "func_removed") for i in range(12))
    text = render_review_digest(
        _digest(
            manual_review_banner=False,
            coverage_warnings=(),
            review_groups=(),
            impacted=impacted,
            result_counts={},
            disposition_audit=None,
            show_release_recommendation=False,
            env_matrix_source_sha256=None,
            gate_exit_code=0,
            evidence_summary="",
        )
    )
    assert "Top impacted symbols" in text
    assert "and 2 more" in text
    assert "Gate:** PASS (exit 0)" in text


def test_pr_comment_suppression_note_covers_audit_and_reclassification() -> None:
    model = CommentModel("compare", "libx", "1", "2", "strict_abi")
    assert suppression_note(model) == []
    model.suppressed_count = 2
    model.reclassified_count = 1
    model.disposition_audit = DispositionAudit(
        3, 0, (("suppressed", 3),), (), ()
    ).to_dict()
    text = "\n".join(suppression_note(model))
    assert "2 findings suppressed" in text
    assert "1 finding reclassified" in text
    assert "3 detected · 0 gating · 3 suppressed" in text
