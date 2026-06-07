"""Safety harness for verdict stability during targeted compatibility fixes.

Goal: while fixing only cases 49/50/51/54/62, ensure verdicts for all
other benchmark example cases remain unchanged.

This test compares current checker output against committed benchmark baselines
(`benchmark_reports/case*/case*_abicheck.txt`) using stored snapshots from
`benchmark_reports/_build/case*/snap_v{1,2}.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from abicheck.checker import compare
from abicheck.serialization import load_snapshot

REPO_DIR = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_DIR / "benchmark_reports"
BUILD_DIR = BENCH_DIR / "_build"

# Only these cases are allowed to change during the current fix batch.
ALLOWED_TO_CHANGE = {
    "case49_executable_stack",
    "case50_soname_inconsistent",
    "case51_protected_visibility",
    "case54_used_reserved_field",
    "case62_type_field_added_compatible",
}


def _baseline_verdict(case_dir: Path) -> str:
    report = case_dir / f"{case_dir.name}_abicheck.txt"
    if not report.exists():
        pytest.skip(f"baseline report missing: {report}")
    raw = report.read_text(encoding="utf-8")

    # Some stored reports contain trailing warning lines after the JSON payload.
    # Parse only the first JSON object.
    decoder = json.JSONDecoder()
    data, _ = decoder.raw_decode(raw)
    return str(data["verdict"]).upper()


def _current_verdict(case_name: str) -> str:
    snap_old = BUILD_DIR / case_name / "snap_v1.json"
    snap_new = BUILD_DIR / case_name / "snap_v2.json"
    if not snap_old.exists() or not snap_new.exists():
        pytest.skip(f"snapshot artifacts missing: {snap_old} / {snap_new}")

    old = load_snapshot(snap_old)
    new = load_snapshot(snap_new)
    result = compare(old, new)
    return result.verdict.value.upper()


def _all_case_names() -> list[str]:
    names = [p.name for p in BENCH_DIR.glob("case*") if p.is_dir()]
    return sorted(names)


@pytest.mark.integration
@pytest.mark.parametrize("case_name", _all_case_names())
def test_verdict_stable_for_non_target_cases(case_name: str) -> None:
    """Non-target cases must keep their baseline verdicts exactly."""
    if case_name in ALLOWED_TO_CHANGE:
        pytest.skip("target case for current fix batch")

    case_dir = BENCH_DIR / case_name
    baseline = _baseline_verdict(case_dir)
    current = _current_verdict(case_name)

    assert current == baseline, (
        f"{case_name}: verdict drift detected outside target scope: "
        f"baseline={baseline!r}, current={current!r}"
    )


@pytest.mark.integration
@pytest.mark.parametrize("case_name", sorted(ALLOWED_TO_CHANGE))
def test_target_cases_match_committed_post_fix_baseline(case_name: str) -> None:
    """Document current post-fix baseline for the originally targeted cases.

    This remains intentionally strict: when baseline artifacts change, reviewers
    get explicit diffs for the historically sensitive target set.
    """
    case_dir = BENCH_DIR / case_name
    baseline = _baseline_verdict(case_dir)

    expected_post_fix = {
        "case49_executable_stack": "COMPATIBLE",
        "case50_soname_inconsistent": "COMPATIBLE_WITH_RISK",
        "case51_protected_visibility": "COMPATIBLE",
        "case54_used_reserved_field": "COMPATIBLE",
        "case62_type_field_added_compatible": "COMPATIBLE",
    }[case_name]

    assert baseline == expected_post_fix
