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
        "status": "partial",
        "dwarf_context_status": "asymmetric",
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
            "status": assurance_status,
            "dwarf_context_status": "asymmetric",
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
        "status": "partial",
        "dwarf_context_status": "asymmetric",
        "debug_evidence": {
            "old": {"basic": "parsed", "advanced": "parsed"},
            "new": {"basic": "not_available", "advanced": "not_available"},
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


def test_missing_or_unknown_evidence_channel_does_not_waive_downgrade() -> None:
    guard = _guard_module()
    entry = {"min_evidence": "L1", "expected_kinds": ["type_size_changed"]}
    for state in ({"advanced": "not_available"}, {"basic": "bogus"}):
        assurance = {
            "status": "partial",
            "debug_evidence": {"old": {"basic": "parsed"}, "new": state},
        }
        assert not guard._dwarf_evidence_loss_allows_downgrade(entry, assurance)


def test_advanced_only_gap_does_not_waive_basic_layout_kind() -> None:
    """A receipt must name the channel the missing detector actually needs."""
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "dwarf_context_status": "asymmetric",
        "debug_evidence": {
            "old": {"basic": "parsed", "advanced": "parsed"},
            "new": {"basic": "parsed", "advanced": "not_available"},
        },
    }

    _, downgrades, errors = guard._classify_results(
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

    assert downgrades == []
    assert len(errors) == 1


def test_advanced_only_gap_waives_advanced_detector_kind() -> None:
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "dwarf_context_status": "asymmetric",
        "debug_evidence": {
            "old": {"basic": "parsed", "advanced": "parsed"},
            "new": {"basic": "parsed", "advanced": "not_available"},
        },
    }

    _, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "min_evidence": "L1",
                "expected_kinds": ["calling_convention_changed"],
            }
        },
        "stripped-headers",
        {},
    )

    assert len(downgrades) == 1
    assert errors == []


def test_advanced_not_supported_waives_advanced_detector_kind() -> None:
    """P2 review, fresh evidence: "not_supported" (a BTF/CTF-sourced side's
    advanced channel -- neither format carries calling-convention/value-ABI/
    frame-register facts at all) proves capability loss just as much as the
    other non-parsed states -- an L1 advanced-only downgrade backed by it
    must be waived, not flagged as an unproven regression."""
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "dwarf_context_status": "asymmetric",
        "debug_evidence": {
            "old": {"basic": "parsed", "advanced": "parsed"},
            "new": {"basic": "parsed", "advanced": "not_supported"},
        },
    }

    _, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "min_evidence": "L1",
                "expected_kinds": ["calling_convention_changed"],
            }
        },
        "stripped-headers",
        {},
    )

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


def test_clean_header_evidence_blocks_basic_channel_downgrade() -> None:
    """P1 review: diff_types.py's layout facts come from EITHER DWARF or
    header-AST parsing, so losing DWARF alone doesn't prove a basic-channel
    kind was undetectable when header evidence was present and clean on
    this exact run -- release-headers/stripped-headers lanes keep headers
    on both sides. A BREAKING->clean regression here must be reported as an
    error, not silently waived."""
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "dwarf_context_status": "not_evaluated",
        "header_context_status": "clean",
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
    assert downgrades == []
    assert len(errors) == 1


def test_drift_detected_header_evidence_also_blocks_basic_downgrade() -> None:
    """header_context_status="drift_detected" still means header evidence
    was PRESENT (just flagged a context drift), so the same guard applies."""
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "header_context_status": "drift_detected",
        "debug_evidence": {
            "old": {"basic": "not_available"},
            "new": {"basic": "not_available"},
        },
    }

    _, downgrades, errors = guard._classify_results(
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

    assert downgrades == []
    assert len(errors) == 1


def test_missing_header_evidence_still_allows_basic_channel_downgrade() -> None:
    """When header_context_status is anything other than clean/drift_detected
    (not_evaluated, asymmetric, or simply absent from the receipt), the
    basic-channel downgrade is still allowed -- headers genuinely were not
    available as an alternate source either."""
    guard = _guard_module()
    for header_status in ("not_evaluated", "asymmetric", None):
        row = _row("partial")
        assurance: dict[str, object] = {
            "status": "partial",
            "debug_evidence": {
                "old": {"basic": "not_available"},
                "new": {"basic": "not_available"},
            },
        }
        if header_status is not None:
            assurance["header_context_status"] = header_status
        row["analysis_assurance"] = assurance

        _, downgrades, errors = guard._classify_results(
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

        assert len(downgrades) == 1, header_status
        assert errors == [], header_status


def test_clean_header_evidence_does_not_block_advanced_only_downgrade() -> None:
    """header_context_status="clean" only gates BASIC-channel kinds --
    advanced-channel kinds (calling convention/value-ABI/etc.) have no
    header equivalent at all, so their downgrade must remain unaffected."""
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "header_context_status": "clean",
        "debug_evidence": {
            "old": {"basic": "parsed", "advanced": "parsed"},
            "new": {"basic": "parsed", "advanced": "not_available"},
        },
    }

    _, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "min_evidence": "L1",
                "expected_kinds": ["calling_convention_changed"],
            }
        },
        "stripped-headers",
        {},
    )

    assert len(downgrades) == 1
    assert errors == []


def test_integer_model_changed_is_not_associated_with_any_dwarf_channel() -> None:
    """P1 review: integer_model_changed (diff_integer_model) reads header/L2
    typedef facts, never DWARF-advanced facts. It must never be waivable on
    the strength of a DWARF-only evidence-loss receipt, regardless of which
    channel(s) are missing."""
    guard = _guard_module()
    row = _row("partial")
    row["analysis_assurance"] = {
        "status": "partial",
        "debug_evidence": {
            "old": {"basic": "parsed", "advanced": "parsed"},
            "new": {"basic": "not_available", "advanced": "not_available"},
        },
    }

    _, downgrades, errors = guard._classify_results(
        [row],
        {
            "case_break": {
                "expected": "BREAKING",
                "min_evidence": "L1",
                "expected_kinds": ["integer_model_changed"],
            }
        },
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
