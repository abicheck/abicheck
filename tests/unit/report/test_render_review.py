from __future__ import annotations

from dataclasses import replace

from abicheck.policy.disposition_ledger import RuleProvenance
from abicheck.pr_comment_base import CommentModel
from abicheck.pr_comment_sections import _suppression_note as suppression_note
from abicheck.report.disposition_audit import DispositionAudit
from abicheck.report.pr_comment_group_summary import (
    MAX_COMMENT_REVIEW_GROUPS,
    review_group_note,
)
from abicheck.report.render_review import (
    MAX_REVIEW_PATTERN_MODULATIONS,
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


def _group(name: str, *, gating: bool = False, library: str | None = None) -> dict:
    group: dict[str, object] = {
        "display_name": name,
        "transition": "signature changed",
        "gating_findings": [name] if gating else [],
    }
    if library is not None:
        group["library"] = library
    return group


def test_review_group_note_is_empty_without_groups() -> None:
    assert (
        review_group_note(CommentModel("compare", "libx", "1", "2", "strict_abi")) == []
    )


def test_review_group_note_itemizes_groups_with_scope_and_transition() -> None:
    model = CommentModel("compare", "libx", "1", "2", "strict_abi")
    model.review_groups = [
        _group("Widget::resize", gating=True, library="libwidget"),
        _group("plain_fn"),
    ]
    text = "\n".join(review_group_note(model))
    assert "1 gating; 2 retained total" in text
    assert "libwidget: **Widget::resize** — signature changed" in text
    # A group with no library carries no scope prefix.
    assert "- **plain_fn** — signature changed" in text


def test_review_group_note_prefers_result_counts_over_the_itemized_list() -> None:
    """The count line must stay complete even when the list below is capped.

    `result_counts` is computed over the whole population; the itemized list
    is bounded. Reading the headline off the bounded list would under-report.
    """
    model = CommentModel("compare", "libx", "1", "2", "strict_abi")
    model.review_groups = [_group(f"g{i}", gating=True) for i in range(3)]
    model.result_counts = {"gating_review_groups": 41, "review_groups": 99}
    assert "41 gating; 99 retained total" in "\n".join(review_group_note(model))


def test_review_group_note_caps_the_list_and_discloses_the_remainder() -> None:
    """Bounded, with the omitted count exact, across the cap boundary."""
    for total in (
        MAX_COMMENT_REVIEW_GROUPS - 1,
        MAX_COMMENT_REVIEW_GROUPS,
        MAX_COMMENT_REVIEW_GROUPS + 5,
    ):
        model = CommentModel("compare", "libx", "1", "2", "strict_abi")
        model.review_groups = [_group(f"g{i}") for i in range(total)]
        lines = review_group_note(model)
        shown = [line for line in lines if line.startswith("- **")]
        assert len(shown) == min(total, MAX_COMMENT_REVIEW_GROUPS)
        omitted = total - min(total, MAX_COMMENT_REVIEW_GROUPS)
        text = "\n".join(lines)
        if omitted:
            assert f"- … {omitted} more groups omitted" in text
        else:
            assert "omitted" not in text


def _modulation_digest(count: int) -> ReviewDigest:
    return _digest(
        pattern_modulations=tuple(
            {"symbol": f"sym{i}", "rule_id": f"rule{i}", "reason": "accepted"}
            for i in range(count)
        ),
        review_groups=(),
        impacted=(),
        manual_review_banner=False,
        coverage_warnings=(),
        result_counts={},
        disposition_audit=None,
        show_release_recommendation=False,
        env_matrix_source_sha256=None,
        gate_exit_code=0,
        evidence_summary="",
    )


def test_review_digest_caps_pattern_modulations_and_discloses_the_remainder() -> None:
    """ADR-067: a modulated finding is a disposition, so the bound must
    disclose what it cut rather than silently shortening the ledger."""
    text = render_review_digest(_modulation_digest(MAX_REVIEW_PATTERN_MODULATIONS + 3))
    assert "Pattern-modulated findings" in text
    assert "sym0" in text
    assert f"sym{MAX_REVIEW_PATTERN_MODULATIONS}" not in text
    assert "… 3 more omitted; export JSON for details" in text


def test_review_digest_lists_every_pattern_modulation_when_under_the_cap() -> None:
    text = render_review_digest(_modulation_digest(MAX_REVIEW_PATTERN_MODULATIONS))
    for i in range(MAX_REVIEW_PATTERN_MODULATIONS):
        assert f"sym{i}" in text
    assert "more omitted; export JSON for details" not in text
