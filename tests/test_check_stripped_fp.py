"""Focused tests for the reduced-evidence false-positive guard."""
from __future__ import annotations

import importlib.util
from pathlib import Path


def _guard_module():
    path = Path(__file__).with_name("check_stripped_fp.py")
    spec = importlib.util.spec_from_file_location("check_stripped_fp", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(status: str | None) -> dict[str, object]:
    row: dict[str, object] = {
        "case_id": "case_break",
        "got": "COMPATIBLE",
        "status": "PASS",
        "mode": "stripped-headers",
    }
    if status is not None:
        row["analysis_assurance"] = {"status": status}
    return row


def test_clean_downgrade_requires_analysis_assurance_receipt() -> None:
    guard = _guard_module()

    false_positives, downgrades, errors = guard._classify_results(
        [_row(None)],
        {"case_break": {"expected": "BREAKING"}},
        "stripped-headers",
        {},
    )

    assert false_positives == []
    assert downgrades == []
    assert len(errors) == 1
    assert "without explicitly incomplete analysis_assurance" in errors[0]


def test_clean_downgrade_requires_an_incomplete_analysis_assurance_status() -> None:
    guard = _guard_module()

    false_positives, downgrades, errors = guard._classify_results(
        [_row("complete")],
        {"case_break": {"expected": "BREAKING"}},
        "stripped-headers",
        {},
    )

    assert false_positives == []
    assert downgrades == []
    assert len(errors) == 1
    assert "status='complete'" in errors[0]


def test_clean_downgrade_with_partial_analysis_is_reported_not_failed() -> None:
    guard = _guard_module()

    false_positives, downgrades, errors = guard._classify_results(
        [_row("partial")],
        {"case_break": {"expected": "BREAKING"}},
        "stripped-headers",
        {},
    )

    assert false_positives == []
    assert errors == []


def test_known_gap_clean_downgrade_is_reported_not_failed() -> None:
    guard = _guard_module()
    row = _row("complete")
    row["platform"] = "linux"

    false_positives, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "known_gap": "GCC omits calling-convention metadata",
                "known_gap_toolchains": ["gcc"],
            }
        },
        "release-headers",
        {"compiler_c": "gcc"},
    )

    assert false_positives == []
    assert errors == []
    assert len(downgrades) == 1
    assert "known_gap" in downgrades[0]
