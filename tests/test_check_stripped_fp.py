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


def _row(status: str | None, *, result_status: str = "PASS") -> dict[str, object]:
    row: dict[str, object] = {
        "case_id": "case_break",
        "got": "COMPATIBLE",
        "status": result_status,
        "mode": "stripped-headers",
    }
    if status is not None:
        row["analysis_assurance"] = {"status": status}
    return row


def test_clean_downgrade_requires_analysis_assurance_receipt() -> None:
    guard = _guard_module()

    false_positives, downgrades, errors = guard._classify_results(
        [_row(None)],
        {"case_break": {"expected": "BREAKING", "min_evidence": "L1"}},
        "stripped-headers",
        {},
    )

    assert false_positives == []
    assert downgrades == []
    assert len(errors) == 1
    assert "without a DWARF-dependent partial analysis_assurance" in errors[0]


def test_clean_downgrade_requires_an_incomplete_analysis_assurance_status() -> None:
    guard = _guard_module()

    false_positives, downgrades, errors = guard._classify_results(
        [_row("complete")],
        {"case_break": {"expected": "BREAKING", "min_evidence": "L1"}},
        "stripped-headers",
        {},
    )

    assert false_positives == []
    assert downgrades == []
    assert len(errors) == 1
    assert "status='complete'" in errors[0]


def test_l0_func_removed_is_not_waived_by_partial_dwarf_assurance() -> None:
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial", "dwarf_context_status": "asymmetric"
    }

    false_positives, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "min_evidence": "L0",
                "expected_kinds": ["func_removed"],
            }
        },
        "stripped-headers",
        {},
    )

    assert false_positives == []
    assert downgrades == []
    assert len(errors) == 1


def test_failed_and_not_comparable_assurance_are_validation_errors() -> None:
    guard = _guard_module()
    entry = {
        "case_break": {
            "expected": "BREAKING",
            "min_evidence": "L1",
            "known_gap": "A reviewed but inapplicable evidence gap",
            "known_gap_observed": ["COMPATIBLE"],
        }
    }
    for assurance_status in ("failed", "not_comparable"):
        row = _row(assurance_status, result_status="XFAIL")
        row["analysis_assurance"] = {
            "status": assurance_status, "dwarf_context_status": "asymmetric"
        }
        _, downgrades, errors = guard._classify_results(
            [row], entry, "stripped-headers", {}
        )
        assert downgrades == []
        assert len(errors) == 1


def test_dwarf_dependent_partial_downgrade_is_reported_not_failed() -> None:
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial", "dwarf_context_status": "asymmetric"
    }

    false_positives, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "min_evidence": "L1",
                "expected_kinds": ["type_size_changed"],
            }
        },
        "stripped-headers",
        {},
    )

    assert false_positives == []
    assert len(downgrades) == 1
    assert errors == []


def test_symmetric_stripping_requires_per_side_dwarf_receipt() -> None:
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "dwarf_context_status": "not_evaluated",
        "debug_evidence": {
            "old": {"basic": "not_available"},
            "new": {"basic": "not_available"},
        },
    }

    false_positives, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "min_evidence": "L1",
                "expected_kinds": ["type_size_changed"],
            }
        },
        "stripped-headers",
        {},
    )

    assert false_positives == []
    assert len(downgrades) == 1
    assert errors == []


def test_symmetric_stripping_does_not_waive_without_per_side_receipt() -> None:
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "dwarf_context_status": "not_evaluated",
    }

    _, downgrades, errors = guard._classify_results(
        [row],
        {"case_break": {"expected": "BREAKING", "min_evidence": "L1"}},
        "stripped-headers",
        {},
    )

    assert downgrades == []
    assert len(errors) == 1


def test_text_only_known_gap_does_not_waive_full_cli_verdict() -> None:
    guard = _guard_module()
    row = _row("complete", result_status="XFAIL")
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
    assert downgrades == []
    assert len(errors) == 1


def test_exact_observed_known_gap_xfail_is_reported_not_failed() -> None:
    guard = _guard_module()
    row = _row("complete", result_status="XFAIL")
    row["platform"] = "linux"

    _, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "known_gap": "GCC omits calling-convention metadata",
                "known_gap_observed": ["COMPATIBLE"],
                "known_gap_toolchains": ["gcc"],
            }
        },
        "release-headers",
        {"compiler_c": "gcc"},
    )

    assert errors == []
    assert len(downgrades) == 1
    assert "known_gap" in downgrades[0]
