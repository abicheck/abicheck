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
    c = Change(ChangeKind.CALLING_CONVENTION_CHANGED, "cb", "calling convention changed")
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
        changes=[Change(ChangeKind.SYMBOL_VERSION_REQUIRED_ADDED, "libc.so.6", "New GLIBC_2.34 requirement")],
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
