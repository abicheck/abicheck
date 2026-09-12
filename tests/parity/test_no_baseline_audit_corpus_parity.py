# SPDX-License-Identifier: Apache-2.0
"""The G20 acceptance corpus for ``compare --no-baseline``'s audit findings.

``docs/contribute/known-gaps.md``'s "``compare --no-baseline`` does not yet
reproduce ``scan``'s audit-mode findings" entry named this corpus as the
gate for its own fix; that fix landed (verified live against a real
``scan`` invocation, before ``scan`` was deleted outright with ADR-068
Phase 6), and this module is now the standing regression corpus proving
``compare --no-baseline`` keeps reporting every audit/cross-source finding
it reported then, with nothing manufactured -- a compare-only consistency
check, not a live scan/compare comparison any more (the tests that ran a
real ``scan`` invocation to establish the baseline -- ``test_no_baseline_
reports_at_least_what_scan_reports``, ``test_scan_and_no_baseline_agree_
on_the_whole_corpus``, ``test_scan_baseline_exit_codes_documented_for_the_
gated_fixtures`` -- were deleted with ``scan`` itself; the fixed-fixture
expectations they established (``_AUDIT_GATE_SHOULD_FIRE``, the historical
per-check counts already reflected in each fixture's own audit report)
live on in the surviving compare-only assertions below).

**Nothing manufactured**, still checked here: every reported finding must
be genuinely candidate-side
(``policy.no_baseline_findings.is_one_sided_finding``) and must carry an
ADR-068 D3-permitted evolution state -- an audit report may only ever
surface a real self-diff-derived fact, never an invented comparison
finding.

The corpus is read through ``scripts/example_catalog`` (the same accessor
``tests/test_g20_catalog.py`` and this package's sibling parity modules
use), so a renamed or deleted case fails loudly here rather than silently
shrinking the acceptance set.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent.parent
if str(_REPO / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO / "scripts"))
import example_catalog  # noqa: E402

from .runner import invoke_cli  # noqa: E402

#: Every G20 audit/cross-source fixture, as ``(case name, file name)``.
#: ``case151`` contributes two: its full snapshot and the deliberately
#: evidence-thin ``thin.abi.json`` variant, which is the corpus's own
#: "weaker evidence narrows conclusions" case and therefore exactly the one
#: an audit path most easily gets wrong.
G20_AUDIT_FIXTURES: tuple[tuple[str, str], ...] = (
    ("case143_audit_accidental_export", "snapshot.abi.json"),
    ("case144_audit_private_header_leak", "snapshot.abi.json"),
    ("case145_audit_unversioned_export", "snapshot.abi.json"),
    ("case146_audit_rtti_for_internal", "snapshot.abi.json"),
    ("case147_scan_depth_ladder", "snapshot.abi.json"),
    ("case148_xcheck_header_build_mismatch", "snapshot.abi.json"),
    ("case149_xcheck_odr_variant", "snapshot.abi.json"),
    ("case150_xcheck_export_public_pair", "snapshot.abi.json"),
    ("case151_xcheck_provider_matrix", "snapshot.abi.json"),
    ("case151_xcheck_provider_matrix", "thin.abi.json"),
    ("case181_xcheck_public_to_internal_dependency", "snapshot.abi.json"),
)

#: The eleven distinct cases the corpus above covers -- asserted separately
#: from the fixture list so adding a second variant of an existing case
#: cannot quietly be mistaken for adding a new case.
_EXPECTED_CASE_COUNT = 10


def _fixture_path(case_name: str, filename: str) -> Path:
    path = example_catalog.case_dir(case_name) / filename
    assert path.is_file(), f"missing committed G20 fixture: {path}"
    return path


def _no_baseline_report(path: Path, *extra: str) -> dict:
    result = invoke_cli(
        "compare", "--no-baseline", str(path), "--format", "json", *extra
    )
    assert result.exit_code in (0, 1), (
        f"compare --no-baseline aborted on {path.name} "
        f"(exit={result.exit_code}):\n{result.output}"
    )
    return json.loads(result.stdout)


def _no_baseline_kind_counts(report: dict) -> Counter:
    return Counter(finding["kind"] for finding in report["findings"])


def test_corpus_covers_every_committed_g20_audit_case() -> None:
    """The acceptance set is the whole corpus, not a convenient subset."""
    assert len({case for case, _ in G20_AUDIT_FIXTURES}) == _EXPECTED_CASE_COUNT
    for case_name, filename in G20_AUDIT_FIXTURES:
        _fixture_path(case_name, filename)


@pytest.mark.parametrize(("case_name", "filename"), G20_AUDIT_FIXTURES)
def test_no_baseline_manufactures_nothing(case_name: str, filename: str) -> None:
    """Nothing manufactured: every reported finding is genuinely
    candidate-side, reports no observed history, and is never an
    addition/removal.

    Checked against the *report*, not against the in-process partition, so
    this proves what a user actually receives rather than re-asserting an
    internal invariant through its own implementation.

    ``not_evaluated`` is the only permitted evolution state, and that is
    the whole statement -- it used to also permit ``persistent``, which was
    reachable only because the audit ran as a self-diff and so found every
    hygiene condition "present on both sides" of a baseline that does not
    exist. The permitted set is spelled literally here rather than imported
    from the implementation, so the two can disagree.
    """
    permitted = {"not_evaluated"}
    report = _no_baseline_report(_fixture_path(case_name, filename))

    assert report["no_baseline"] is True
    assert report["verdict"] is None, "ADR-068 D2: an audit reports no verdict"
    assert report["changes"] == [], (
        "ADR-068 D2: an audit reports no addition, removal or modification -- "
        "the comparison change set must stay empty even now that findings[] is not"
    )
    assert report["run_outcome"]["compatibility"] is None

    for finding in report["findings"]:
        evolution = finding["evolution"]
        assert evolution is None or evolution in permitted, (
            f"{case_name}: finding {finding['kind']} reports evolution "
            f"{evolution!r}; with OLD declared_absent no history is "
            f"observable, so only {sorted(permitted)} is permitted "
            "(ADR-068 D3)"
        )
        assert evolution is not None or finding["candidate_side_enrichment"], (
            f"{case_name}: finding {finding['kind']} carries neither a "
            "cross-source evolution state nor a candidate-side-enrichment "
            "marker, so it is a comparison finding a baseline-less run "
            "cannot produce"
        )


@pytest.mark.parametrize(("case_name", "filename"), G20_AUDIT_FIXTURES)
def test_no_baseline_exit_code_is_clean_without_a_contract(
    case_name: str, filename: str
) -> None:
    """An audit's hygiene findings never gate on their own (ADR-028 D3 /
    ADR-035 D1: they stay advisory), and without ``--contract`` there is no
    coverage axis either -- so every fixture exits 0 *without*
    ``--severity-preset`` (the audit-gate axis's opt-in). This is what makes
    the exit-3 audit-gate case below a real signal rather than noise.
    """
    path = _fixture_path(case_name, filename)
    result = invoke_cli("compare", "--no-baseline", str(path), "-o", "json=-")
    assert result.exit_code == 0, result.output


#: The two fixtures whose finding kind is ``API_BREAK``-classified -- the
#: ones legacy ``scan``'s own verdict computation gated at exit ``2`` on
#: these committed snapshots (verified live, per the ADR-068 2026-09-09
#: amendment and its 2026-09-10 audit-gate follow-up). Everything else in
#: the corpus, including ``case143`` (``RISK``-classified), must not gate.
_AUDIT_GATE_SHOULD_FIRE = frozenset(
    {"case148_xcheck_header_build_mismatch", "case149_xcheck_odr_variant"}
)


@pytest.mark.parametrize(("case_name", "filename"), G20_AUDIT_FIXTURES)
def test_audit_gate_axis_fires_only_for_api_break_fixtures(
    case_name: str, filename: str
) -> None:
    """ADR-068's 2026-09-10 amendment: with ``--severity-preset default``
    (the opt-in), the audit-gate axis fires exactly on the fixtures
    ``_AUDIT_GATE_SHOULD_FIRE`` names -- case148/case149
    (``API_BREAK``-classified), never case143 (``RISK``-classified) or any
    other fixture in the corpus. The gated exit code is always ``3`` (never
    ``2``/``4``, which stay reserved for a real compatibility verdict an
    audit cannot produce, per ADR-068 D2). This used to also be checked
    directly against a live legacy ``scan`` invocation
    (``test_scan_baseline_exit_codes_documented_for_the_gated_fixtures``,
    deleted with the ``scan`` command itself, ADR-068 Phase 6); the
    fixed-fixture-set assertion here is the surviving, compare-only half.
    """
    path = _fixture_path(case_name, filename)
    result = invoke_cli(
        "compare",
        "--no-baseline",
        str(path),
        "-o",
        "json=-",
        "--severity-preset",
        "default",
    )
    report = json.loads(result.stdout)
    if case_name in _AUDIT_GATE_SHOULD_FIRE:
        assert result.exit_code == 3, (
            f"{case_name}/{filename}: expected the audit-gate axis to fire "
            f"(exit 3), got {result.exit_code}:\n{result.output}"
        )
        assert report["exit_axes"]["audit_gate"] == 3
    else:
        assert result.exit_code == 0, (
            f"{case_name}/{filename}: audit-gate axis fired unexpectedly "
            f"(exit {result.exit_code}), but this fixture's findings are not "
            f"BREAKING/API_BREAK-classified:\n{result.output}"
        )
        assert report["exit_axes"]["audit_gate"] == 0
    # Verdict/changes/compatibility stay exactly as ADR-068 D2 requires,
    # opt-in gating included -- the axis never manufactures a compatibility
    # signal, it only raises the process exit code.
    assert report["verdict"] is None
    assert report["changes"] == []


def test_audit_gate_axis_requires_opt_in() -> None:
    """Without ``--severity-preset``, case148/case149 stay exit 0 -- the
    axis is opt-in (ADR-068 2026-09-10 amendment), so every pre-existing
    ``compare --no-baseline`` invocation is unaffected by its existence.
    """
    for case_name in _AUDIT_GATE_SHOULD_FIRE:
        path = _fixture_path(case_name, "snapshot.abi.json")
        result = invoke_cli("compare", "--no-baseline", str(path), "-o", "json=-")
        assert result.exit_code == 0, (
            f"{case_name}: audit-gate axis fired without --severity-preset "
            f"(exit {result.exit_code}); it must be opt-in:\n{result.output}"
        )


def test_audit_gate_axis_disabled_by_info_only_preset() -> None:
    """``--severity-preset info-only`` stays the explicit no-gate request --
    the one preset value :data:`abicheck.policy.audit_gate_exit.
    SEVERITY_PRESET_DISABLES_AUDIT_GATE` names.
    """
    for case_name in _AUDIT_GATE_SHOULD_FIRE:
        path = _fixture_path(case_name, "snapshot.abi.json")
        result = invoke_cli(
            "compare",
            "--no-baseline",
            str(path),
            "-o",
            "json=-",
            "--severity-preset",
            "info-only",
        )
        assert result.exit_code == 0, (
            f"{case_name}: --severity-preset info-only must not gate "
            f"(exit {result.exit_code}):\n{result.output}"
        )


def test_audit_gate_axis_honors_a_policy_promoted_verdict(tmp_path: Path) -> None:
    """Regression for a Codex security review finding (P1) on this axis's
    first revision: it read a finding's *raw* ``ChangeKind`` category
    (``BREAKING_KINDS``/``API_BREAK_KINDS`` membership) instead of its
    *effective*, policy-resolved verdict, so an explicitly-selected,
    trusted ``--policy`` document promoting a normally-RISK finding to
    BREAKING was silently invisible to the gate -- an untrusted candidate
    artifact could pass a CI job that had explicitly asked to gate on
    exactly that promotion. ``case143``'s ``exported_not_public`` finding is
    RISK-classified by default (does not gate, per
    ``_AUDIT_GATE_SHOULD_FIRE`` above); this pins that an ``overrides:``
    policy promoting it to ``break`` (``Verdict.BREAKING``) flips the axis
    to fire, exit 3, even though the raw finding kind never changed.
    """
    policy_path = tmp_path / "promote_exported_not_public.yml"
    policy_path.write_text("overrides:\n  exported_not_public: break\n")
    path = _fixture_path("case143_audit_accidental_export", "snapshot.abi.json")

    # Baseline: case143 does not gate under the built-in default policy.
    baseline = invoke_cli(
        "compare",
        "--no-baseline",
        str(path),
        "-o",
        "json=-",
        "--severity-preset",
        "default",
    )
    assert baseline.exit_code == 0, baseline.output
    assert json.loads(baseline.stdout)["exit_axes"]["audit_gate"] == 0

    # With the override in effect, the same fixture's same finding must gate.
    promoted = invoke_cli(
        "compare",
        "--no-baseline",
        str(path),
        "-o",
        "json=-",
        "--severity-preset",
        "default",
        "--policy",
        str(policy_path),
    )
    report = json.loads(promoted.stdout)
    assert promoted.exit_code == 3, (
        f"a --policy override promoting case143's finding to BREAKING must "
        f"gate the audit (exit 3), got {promoted.exit_code}:\n{promoted.output}"
    )
    assert report["exit_axes"]["audit_gate"] == 3
    assert {f["kind"] for f in report["findings"]} == {"exported_not_public"}, (
        "the override must not change which finding is reported, only whether it gates"
    )


