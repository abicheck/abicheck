from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from abicheck.cli_scan import _run_baseline_compare


class _Verdict:
    def __init__(self, value: str) -> None:
        self.value = value


def test_scan_baseline_compare_preserves_hard_l0_elf_removal(monkeypatch) -> None:
    """source/full scan must not filter away old-only ELF removals.

    Regression for case97: richer header/source public-surface scoping can miss a
    macro-conditioned old export, but the hard L0 func_removed_elf_only finding
    remains authoritative and must be folded into the final scan verdict.
    """

    old_snap = SimpleNamespace(build_source=None)
    new_snap = SimpleNamespace(build_source=None)
    hard_l0 = SimpleNamespace(kind=SimpleNamespace(value="func_removed_elf_only"))
    calls: list[dict[str, object]] = []

    def fake_resolve_input(*args, **kwargs):  # noqa: ANN002, ANN003
        return old_snap

    def fake_prepare_embedded_build_source(
        old,
        new,
        collect_mode,
        extra_changes,
        *args,
        **kwargs,  # noqa: ANN001, ANN002, ANN003
    ):
        return list(extra_changes), [], {}, None

    def fake_compare_snapshots(
        old,
        new,
        suppression=None,
        *,
        policy="strict_abi",
        policy_file=None,
        extra_changes,
        scope_to_public_surface,
        force_public_symbols=None,
        pattern_verdicts=False,
        env_matrix=None,
        collapse_versioned_symbols=False,
        contract_evaluation=False,
        contract_mode=None,
    ):  # noqa: ANN001
        calls.append(
            {
                "extra_changes": list(extra_changes),
                "scope_to_public_surface": scope_to_public_surface,
            }
        )
        if not scope_to_public_surface:
            return SimpleNamespace(
                breaking=[hard_l0],
                source_breaks=[],
                risk=[],
                compatible=[],
                verdict=_Verdict("BREAKING"),
            )
        return SimpleNamespace(
            breaking=list(extra_changes),
            source_breaks=[],
            risk=[],
            compatible=[],
            verdict=_Verdict("BREAKING" if extra_changes else "NO_CHANGE"),
        )

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve_input)
    monkeypatch.setattr("abicheck.service.compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        "abicheck.cli_buildsource.prepare_embedded_build_source",
        fake_prepare_embedded_build_source,
    )

    verdict, exit_code, summary = _run_baseline_compare(
        Path("old.so"),
        Path("new.so"),
        new_snap,
        [],
        "c++",
        "graph-full",
        [],
        [],
        [],
        [],
    )

    assert verdict == "BREAKING"
    assert exit_code == 4
    assert summary["breaking"] == 1
    assert calls[0]["scope_to_public_surface"] is False
    assert calls[1]["scope_to_public_surface"] is True
    assert hard_l0 in calls[1]["extra_changes"]
    # The breaking finding itself is preserved, not just its count (a failing
    # `scan --baseline` must name what broke, not just report "breaking=1").
    # `finding_id` is the canonical identity ADR-049 Phase 5 §6.4 added so a
    # scan finding can be joined to its `compare` counterpart; its value is a
    # content hash, so it is checked for presence here and pinned by
    # `tests/test_scan_compare_parity.py` against `compare`'s own.
    (finding,) = summary["findings"]
    assert finding.pop("finding_id")
    # canonical_finding_id (schema 2.36) is finding_id's backend-independent
    # sibling -- see finding_identity.report_canonical_finding_id.
    assert finding.pop("canonical_finding_id")
    assert finding == {
        "bucket": "breaking",
        "kind": "func_removed_elf_only",
        "symbol": None,
        "description": None,
        "source_location": None,
    }


def test_scan_baseline_compare_does_not_promote_advisory_l0_findings(
    monkeypatch,
) -> None:
    """Only explicit hard L0 removals are preserved; crosschecks stay advisory."""

    old_snap = SimpleNamespace(build_source=None)
    new_snap = SimpleNamespace(build_source=None)
    advisory = SimpleNamespace(
        kind=SimpleNamespace(value="header_build_context_mismatch")
    )

    def fake_resolve_input(*args, **kwargs):  # noqa: ANN002, ANN003
        return old_snap

    def fake_prepare_embedded_build_source(
        old,
        new,
        collect_mode,
        extra_changes,
        *args,
        **kwargs,  # noqa: ANN001, ANN002, ANN003
    ):
        return list(extra_changes), [], {}, None

    def fake_compare_snapshots(
        old,
        new,
        suppression=None,
        *,
        policy="strict_abi",
        policy_file=None,
        extra_changes,
        scope_to_public_surface,
        force_public_symbols=None,
        pattern_verdicts=False,
        env_matrix=None,
        collapse_versioned_symbols=False,
        contract_evaluation=False,
        contract_mode=None,
    ):  # noqa: ANN001
        if not scope_to_public_surface:
            return SimpleNamespace(
                breaking=[advisory],
                source_breaks=[],
                risk=[],
                compatible=[],
                verdict=_Verdict("BREAKING"),
            )
        assert extra_changes == []
        return SimpleNamespace(
            breaking=[],
            source_breaks=[],
            risk=[],
            compatible=[],
            verdict=_Verdict("NO_CHANGE"),
        )

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve_input)
    monkeypatch.setattr("abicheck.service.compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        "abicheck.cli_buildsource.prepare_embedded_build_source",
        fake_prepare_embedded_build_source,
    )

    verdict, exit_code, summary = _run_baseline_compare(
        Path("old.so"),
        Path("new.so"),
        new_snap,
        [],
        "c++",
        "graph-full",
        [],
        [],
        [],
        [],
    )

    assert verdict == "NO_CHANGE"
    assert exit_code == 0
    # No findings key at all when there is nothing to report (back-compat with
    # consumers that only ever read the four counts) -- `exit` (CLI cleanup
    # phase two, PR E) is unconditional, unlike `findings`, since every real
    # comparison has a compatibility contribution to report.
    #
    # `effective_config_digest`/`effective_config_fields` (CLI cleanup phase
    # two, PR B) are asserted separately below rather than folded into this
    # literal: a hardcoded hash here would break on every unrelated field
    # this repo's own digest ever grows (see effective_config_digest.py).
    digest = summary.pop("effective_config_digest")
    fields = summary.pop("effective_config_fields")
    assert summary == {
        "breaking": 0,
        "api_break": 0,
        "risk": 0,
        "compatible": 0,
        "exit": {
            "code": 0,
            "reasons": ["clean"],
            "compatibility_contribution": 0,
            "contract_coverage_contribution": 0,
            "analysis_assurance_contribution": 0,
            "crosscheck_promotion_contribution": 0,
            "operational_error_contribution": 0,
            "evidence_contract_error_contribution": 0,
            "budget_overflow_contribution": 0,
            "not_comparable_contribution": 0,
            "removed_required_library_contribution": 0,
            "incomplete_scope_contribution": 0,
            "no_comparison_completed_contribution": 0,
        },
    }
    assert "findings" not in summary
    assert digest.startswith("sha256:")
    assert fields["_tier"] == "baseline"
    assert fields["gate.exit_code_scheme"] == "legacy"


def test_scan_baseline_compare_truncates_large_finding_lists(monkeypatch) -> None:
    """A large baseline diff caps embedded findings and flags the truncation."""
    from abicheck.cli_scan_baseline import _MAX_BASELINE_FINDINGS

    old_snap = SimpleNamespace(build_source=None)
    new_snap = SimpleNamespace(build_source=None)
    many_breaks = [
        SimpleNamespace(
            kind=SimpleNamespace(value="func_removed"),
            symbol=f"sym{i}",
            description=f"removed sym{i}",
            source_location=None,
        )
        for i in range(_MAX_BASELINE_FINDINGS + 5)
    ]

    def fake_resolve_input(*args, **kwargs):  # noqa: ANN002, ANN003
        return old_snap

    def fake_prepare_embedded_build_source(
        old,
        new,
        collect_mode,
        extra_changes,
        *args,
        **kwargs,  # noqa: ANN001, ANN002, ANN003
    ):
        return list(extra_changes), [], {}, None

    def fake_compare_snapshots(
        old,
        new,
        suppression=None,
        *,
        policy="strict_abi",
        policy_file=None,
        extra_changes,
        scope_to_public_surface,
        force_public_symbols=None,
        pattern_verdicts=False,
        env_matrix=None,
        collapse_versioned_symbols=False,
        contract_evaluation=False,
        contract_mode=None,
    ):  # noqa: ANN001
        return SimpleNamespace(
            breaking=many_breaks,
            source_breaks=[],
            risk=[],
            compatible=[],
            verdict=_Verdict("BREAKING"),
        )

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve_input)
    monkeypatch.setattr("abicheck.service.compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        "abicheck.cli_buildsource.prepare_embedded_build_source",
        fake_prepare_embedded_build_source,
    )

    _, _, summary = _run_baseline_compare(
        Path("old.so"),
        Path("new.so"),
        new_snap,
        [],
        "c++",
        "graph-full",
        [],
        [],
        [],
        [],
    )

    assert summary["breaking"] == len(many_breaks)
    assert len(summary["findings"]) == _MAX_BASELINE_FINDINGS
    assert summary["findings_truncated"] is True


def test_scan_baseline_compare_truncates_large_suppressed_lists(monkeypatch) -> None:
    """A large *suppressed* list caps independently and flags its own truncation."""
    from abicheck.cli_scan_baseline import _MAX_BASELINE_FINDINGS

    old_snap = SimpleNamespace(build_source=None)
    new_snap = SimpleNamespace(build_source=None)
    many_suppressed = [
        SimpleNamespace(
            kind=SimpleNamespace(value="func_removed"),
            symbol=f"sym{i}",
            description=f"removed sym{i}",
            source_location=None,
            suppression_rule=f"rule{i}",
        )
        for i in range(_MAX_BASELINE_FINDINGS + 5)
    ]

    def fake_resolve_input(*args, **kwargs):  # noqa: ANN002, ANN003
        return old_snap

    def fake_prepare_embedded_build_source(
        old,
        new,
        collect_mode,
        extra_changes,
        *args,
        **kwargs,  # noqa: ANN001, ANN002, ANN003
    ):
        return list(extra_changes), [], {}, None

    def fake_compare_snapshots(
        old,
        new,
        suppression=None,
        *,
        policy="strict_abi",
        policy_file=None,
        extra_changes,
        scope_to_public_surface,
        force_public_symbols=None,
        pattern_verdicts=False,
        env_matrix=None,
        collapse_versioned_symbols=False,
        contract_evaluation=False,
        contract_mode=None,
    ):  # noqa: ANN001
        return SimpleNamespace(
            breaking=[],
            source_breaks=[],
            risk=[],
            compatible=[],
            suppressed_changes=many_suppressed,
            verdict=_Verdict("NO_CHANGE"),
        )

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve_input)
    monkeypatch.setattr("abicheck.service.compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        "abicheck.cli_buildsource.prepare_embedded_build_source",
        fake_prepare_embedded_build_source,
    )

    _, _, summary = _run_baseline_compare(
        Path("old.so"),
        Path("new.so"),
        new_snap,
        [],
        "c++",
        "graph-full",
        [],
        [],
        [],
        [],
    )

    assert summary["suppressed_count"] == len(many_suppressed)
    assert len(summary["suppressed"]) == _MAX_BASELINE_FINDINGS
    assert summary["suppressed_truncated"] is True
    # The (separate, unrelated) gating-findings truncation must not fire here.
    assert "findings_truncated" not in summary
    # Codex review: which --suppress rule silenced each finding must be
    # attributable (Change.suppression_rule), not dropped by the projection.
    assert summary["suppressed"][0]["suppression_rule"] == "rule0"


def test_scan_baseline_compare_filters_dependency_scope_by_default(monkeypatch) -> None:
    """Codex review: a `dump`-produced --against baseline defaults to
    dependency_scope="filtered"; scan's own baseline resolve_input() call
    used to leave include_dependencies at its True/"full" default, so the
    new comparability gate hard-failed the routine "scan against a plain
    dump'd baseline" workflow with ScopeMismatchError. Assert the baseline
    resolve_input() call now matches the candidate's own default."""

    old_snap = SimpleNamespace(build_source=None)
    new_snap = SimpleNamespace(build_source=None)
    calls: list[dict[str, object]] = []

    def fake_resolve_input(*args, **kwargs):  # noqa: ANN002, ANN003
        calls.append(kwargs)
        return old_snap

    def fake_prepare_embedded_build_source(
        old,
        new,
        collect_mode,
        extra_changes,
        *args,
        **kwargs,  # noqa: ANN001, ANN002, ANN003
    ):
        return list(extra_changes), [], {}, None

    def fake_compare_snapshots(
        old,
        new,
        suppression=None,
        *,
        policy="strict_abi",
        policy_file=None,
        extra_changes,
        scope_to_public_surface,
        force_public_symbols=None,
        pattern_verdicts=False,
        env_matrix=None,
        collapse_versioned_symbols=False,
        contract_evaluation=False,
        contract_mode=None,
    ):  # noqa: ANN001
        return SimpleNamespace(
            breaking=[],
            source_breaks=[],
            risk=[],
            compatible=[],
            verdict=_Verdict("NO_CHANGE"),
        )

    monkeypatch.setattr("abicheck.service.resolve_input", fake_resolve_input)
    monkeypatch.setattr("abicheck.service.compare_snapshots", fake_compare_snapshots)
    monkeypatch.setattr(
        "abicheck.cli_buildsource.prepare_embedded_build_source",
        fake_prepare_embedded_build_source,
    )

    _run_baseline_compare(
        Path("old.so"),
        Path("new.so"),
        new_snap,
        [],
        "c++",
        "graph-full",
        [],
        [],
        [],
        [],
    )

    # The first resolve_input() call is the baseline (`old_snap`); the
    # l0-removal-preservation pass's two symbols_only=True calls follow.
    assert calls[0]["include_dependencies"] is False
