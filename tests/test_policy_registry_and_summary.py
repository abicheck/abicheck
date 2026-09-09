from abicheck.checker import Change, ChangeKind, DiffResult, Verdict
from abicheck.checker_policy import policy_for, policy_registry_markdown
from abicheck.report_summary import build_summary, compatibility_metrics


def test_policy_registry_has_doc_slug_and_severity() -> None:
    entry = policy_for(ChangeKind.FUNC_REMOVED)
    assert entry.doc_slug == "func_removed"
    assert entry.severity == "error"


def test_policy_registry_markdown_contains_header() -> None:
    md = policy_registry_markdown()
    assert "| ChangeKind | Default verdict | Severity | Doc slug |" in md
    assert "`func_removed`" in md


def test_summary_metrics_include_percentages() -> None:
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libx.so",
        changes=[Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
        verdict=Verdict.BREAKING,
    )
    summary = build_summary(result)
    assert summary.binary_compatibility_pct == 0.0
    assert summary.affected_pct == 0.0


def test_compatibility_metrics_use_old_symbol_count() -> None:
    metrics = compatibility_metrics(
        [Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed")],
        old_symbol_count=10,
    )
    assert metrics.breaking_count == 1
    assert round(metrics.binary_compatibility_pct, 1) == 90.0
    assert round(metrics.affected_pct, 1) == 10.0


def test_compatibility_metrics_no_breaking_is_full_compatibility() -> None:
    metrics = compatibility_metrics(
        [Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added")],
        old_symbol_count=10,
    )
    assert metrics.breaking_count == 0
    assert metrics.binary_compatibility_pct == 100.0
    assert metrics.affected_pct == 0.0


def test_compatibility_metrics_without_old_symbol_count_uses_change_ratio() -> None:
    metrics = compatibility_metrics(
        [
            Change(ChangeKind.FUNC_REMOVED, "_Z3foov", "removed"),
            Change(ChangeKind.FUNC_ADDED, "_Z3barv", "added"),
        ],
    )
    assert metrics.breaking_count == 1
    assert round(metrics.binary_compatibility_pct, 1) == 50.0
    assert metrics.affected_pct == 0.0


def test_compatibility_metrics_honours_named_policy_without_kind_sets() -> None:
    """Codex review on #549: a caller passing only `policy` (e.g. the HTML
    report's fallback for a duck-typed result without _effective_kind_sets())
    must still route through the effective-verdict path — plugin_abi
    downgrades CALLING_CONVENTION_CHANGED from breaking to compatible, so
    counting it via raw ChangeKind membership would wrongly report it (and
    show a contradictory <100% binary compatibility) despite the effective
    verdict being compatible."""
    c = Change(
        ChangeKind.CALLING_CONVENTION_CHANGED, "cb", "calling convention changed"
    )
    metrics = compatibility_metrics([c], old_symbol_count=10, policy="plugin_abi")
    assert metrics.breaking_count == 0
    assert metrics.binary_compatibility_pct == 100.0


def test_policy_for_unknown_kind_falls_back_to_breaking() -> None:
    class _UnknownKind:
        value = "unknown_kind"

    entry = policy_for(_UnknownKind())  # type: ignore[arg-type]
    assert entry.default_verdict == Verdict.BREAKING
    assert entry.severity == "error"
    assert entry.doc_slug == "unknown_kind"


def test_build_summary_risk_count_nonzero() -> None:
    """build_summary() populates risk_count correctly for COMPATIBLE_WITH_RISK changes."""
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libx.so",
        changes=[
            Change(
                ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED,
                "libc.so.6",
                "New GLIBC_2.34 requirement",
            )
        ],
        verdict=Verdict.COMPATIBLE_WITH_RISK,
    )
    summary = build_summary(result)
    assert summary.risk_count == 1
    assert summary.breaking == 0
    assert summary.source_breaks == 0
    assert summary.compatible_additions == 0
    assert summary.total_changes == 1


def test_build_summary_risk_count_zero_for_compatible() -> None:
    """build_summary() returns risk_count=0 for purely COMPATIBLE results."""
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libx.so",
        changes=[Change(ChangeKind.FUNC_ADDED, "_Z3barv", "New function added")],
        verdict=Verdict.COMPATIBLE,
    )
    summary = build_summary(result)
    assert summary.risk_count == 0
    assert summary.compatible_additions == 1
    assert summary.quality_issues == 0


def test_build_summary_quality_issues_splits_out_public_surface_shrank() -> None:
    """A net public-surface *shrink* is COMPATIBLE (informational, not a
    break) but is not a genuine addition -- it must be visible in
    quality_issues, not silently folded into "additions" with nothing to
    distinguish it from real API growth (the field-name mislabel a CI
    dashboard reading compatible_additions alone would otherwise hit once
    surface metrics became unconditional -- ADR-027 Phase 5).
    """
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libx.so",
        changes=[
            Change(
                ChangeKind.PUBLIC_SURFACE_SHRANK,
                symbol=None,
                description="public surface shrank: 439 -> 425 declarations (-14)",
            )
        ],
        verdict=Verdict.COMPATIBLE,
    )
    summary = build_summary(result)
    assert summary.risk_count == 0
    assert summary.compatible_additions == 1
    assert summary.quality_issues == 1


def test_build_summary_quality_issues_does_not_shadow_real_additions() -> None:
    """A mixed compatible batch (one real addition, one surface-shrink
    roll-up) reports both counts independently: compatible_additions stays
    the historical total, quality_issues names only the non-addition
    subset -- so `compatible_additions - quality_issues` recovers the real
    addition count, mirroring cli_compare_release_pairwise.py's
    ADDITION_KINDS-based `quality_issues` derivation for the release path.
    """
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libx.so",
        changes=[
            Change(ChangeKind.FUNC_ADDED, "_Z3barv", "New function added"),
            Change(
                ChangeKind.PUBLIC_SURFACE_SHRANK,
                symbol=None,
                description="public surface shrank: 439 -> 425 declarations (-14)",
            ),
        ],
        verdict=Verdict.COMPATIBLE,
    )
    summary = build_summary(result)
    assert summary.compatible_additions == 2
    assert summary.quality_issues == 1
    assert summary.compatible_additions - summary.quality_issues == 1


def test_build_summary_quality_issues_derives_from_effective_category(
    tmp_path,
) -> None:
    """Codex review, PR #1181: a bare `c.kind not in ADDITION_KINDS` test
    reads the finding's *raw*, policy-independent kind -- but a policy can
    globally override an ADDITION_KINDS member out of the effective
    compatible kind set (here, `func_added` overridden to `break`), and a
    per-finding `effective_verdict` override can independently bring one
    specific finding back to COMPATIBLE without going through a matching
    `reclassify:` selector rule. `result.compatible` (effective-verdict
    based) includes that finding, but `classify_effective_change` --
    the canonical resolver `report.finding`'s own category split already
    uses -- correctly reads it as QUALITY_ISSUES (its kind is no longer in
    the override-adjusted compatible set, and no reclassify rule backs the
    COMPATIBLE result), not ADDITION. `quality_issues` must agree with that,
    not with the static kind-set test the old implementation used."""
    from pathlib import Path

    from abicheck.policy_file import PolicyFile

    p = Path(tmp_path) / "policy.yaml"
    p.write_text("overrides:\n  func_added: break\n", encoding="utf-8")
    pf = PolicyFile.load(p)

    demoted = Change(
        ChangeKind.FUNC_ADDED,
        "_Z3foov",
        "New function added",
        effective_verdict=Verdict.COMPATIBLE,
    )
    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libx.so",
        changes=[demoted],
        verdict=Verdict.COMPATIBLE,
        policy_file=pf,
    )
    assert result.compatible == [demoted]
    summary = build_summary(result)
    assert summary.compatible_additions == 1
    assert summary.quality_issues == 1


def test_review_digest_additions_count_excludes_quality_issues() -> None:
    """Codex review, fresh evidence: the Markdown review digest's own
    additions_count/quality_issues_count are two rows in the same
    rendered table, so they must not overlap -- unlike the JSON summary
    (where compatible_additions stays the historical whole-bucket total
    by design), the digest's "Additions" row must show only genuine
    additions, mirroring pr_comment.py's identical derivation for the
    release path. Before this fix, a mixed batch (1 addition + 1 quality
    finding) rendered "Additions: 2" / "Quality issues: 1" -- overlapping
    rows that still summed to more real additions than actually occurred.
    """
    from abicheck.reporter_markdown import compute_review_digest

    result = DiffResult(
        old_version="1.0",
        new_version="2.0",
        library="libx.so",
        changes=[
            Change(ChangeKind.FUNC_ADDED, "_Z3barv", "New function added"),
            Change(
                ChangeKind.PUBLIC_SURFACE_SHRANK,
                symbol=None,
                description="public surface shrank: 439 -> 425 declarations (-14)",
            ),
        ],
        verdict=Verdict.COMPATIBLE,
    )
    digest = compute_review_digest(result)
    assert digest.additions_count == 1
    assert digest.quality_issues_count == 1
