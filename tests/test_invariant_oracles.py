"""Unit tests for the invariant oracles in :mod:`tests.oracles`.

Each oracle must fire on a planted violation of its own invariant and stay
silent on a clean input. The planted inputs are hand-written public-shaped
results, not outputs of the detectors under test.
"""

from __future__ import annotations

import pytest

from tests.oracles.absence import absence_violations
from tests.oracles.conservation import conservation_violations
from tests.oracles.producer import allowlist_growth, producer_violations
from tests.oracles.routes import RouteOutcome, route_violations

# -- A: absence typing ----------------------------------------------------------


@pytest.mark.parametrize("verdict", ["NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK"])
@pytest.mark.parametrize("status", ["partial", "failed", "not_comparable"])
def test_absence_fires_on_compatibility_claim_without_evidence(
    verdict: str, status: str
) -> None:
    problems = absence_violations(
        {"verdict": verdict, "analysis_assurance": {"status": status}}
    )
    assert problems == [
        f"verdict {verdict} asserts compatibility while analysis_assurance.status is {status!r}"
    ]


@pytest.mark.parametrize("status", ["complete", "not_requested"])
def test_absence_silent_when_evidence_is_present_or_not_asked(status: str) -> None:
    assert (
        absence_violations(
            {"verdict": "NO_CHANGE", "analysis_assurance": {"status": status}}
        )
        == []
    )


def test_absence_silent_on_breaking_verdict_with_partial_evidence() -> None:
    # A break is a claim of incompatibility; partial evidence does not forbid it.
    assert (
        absence_violations(
            {"verdict": "BREAKING", "analysis_assurance": {"status": "partial"}}
        )
        == []
    )


# -- B: producer equivalence ----------------------------------------------------


def test_producer_silent_when_kinds_agree() -> None:
    assert producer_violations({"func_removed"}, {"func_removed"}) == []


def test_producer_fires_on_undeclared_difference() -> None:
    assert producer_violations(
        {"func_removed", "enum_member_removed"}, {"func_removed"}
    ) == ["undeclared: enum_member_removed (only castxml)"]


def test_producer_declared_difference_is_accepted() -> None:
    allow = [("enum_member_removed", "castxml")]
    assert (
        producer_violations(
            {"func_removed", "enum_member_removed"}, {"func_removed"}, allow
        )
        == []
    )


def test_producer_flags_stale_allowlist_entry() -> None:
    allow = [("enum_member_removed", "castxml")]
    assert producer_violations({"func_removed"}, {"func_removed"}, allow) == [
        "stale: enum_member_removed (only castxml)"
    ]


def test_allowlist_growth_flags_new_entry_only() -> None:
    baseline = [("a", "clang")]
    assert allowlist_growth([("a", "clang"), ("b", "castxml")], baseline) == [
        "allowlist grew: b (only castxml)"
    ]


# -- C: route equivalence -------------------------------------------------------


def test_routes_agree_silently() -> None:
    same = RouteOutcome(4, "BREAKING", frozenset({"func_removed"}))
    assert route_violations({"live": same, "stored": same}) == []


def test_routes_fire_on_exit_verdict_and_kind_drift() -> None:
    live = RouteOutcome(4, "BREAKING", frozenset({"func_removed"}))
    stored = RouteOutcome(0, "NO_CHANGE", frozenset())
    assert route_violations({"live": live, "stored": stored}) == [
        "stored: exit 0 != 4 (live)",
        "stored: verdict NO_CHANGE != BREAKING (live)",
        "stored: kinds differ from live (extra [], missing ['func_removed'])",
    ]


def test_single_route_has_nothing_to_compare() -> None:
    assert route_violations({"live": RouteOutcome(0, "NO_CHANGE", frozenset())}) == []


# -- D: count conservation ------------------------------------------------------


def _report(
    n: int, counts: dict[str, int] | None = None, detected: int | None = None
) -> dict:
    report: dict = {
        "changes": [{"kind": "func_removed", "symbol": f"f{i}"} for i in range(n)]
    }
    if counts is not None:
        report["disposition_audit"] = {
            "counts": counts,
            "detected_total": n if detected is None else detected,
        }
    return report


def test_conservation_silent_when_totals_match() -> None:
    assert (
        conservation_violations(
            _report(3, {"gating": 2, "suppressed": 1}), {"sarif": 3, "oneline": 3}
        )
        == []
    )


def test_conservation_fires_on_renderer_total_drift() -> None:
    assert conservation_violations(_report(3), {"oneline": 2}) == [
        "oneline totals 2, JSON lists 3"
    ]


def test_conservation_fires_on_disposition_leak() -> None:
    problems = conservation_violations(_report(3, {"gating": 1}, detected=3), {})
    assert problems == ["disposition_audit counts sum to 1, detected_total is 3"]


def test_conservation_fires_on_double_counted_finding() -> None:
    report = {"changes": [{"id": "x1"}, {"id": "x1"}, {"id": "x2"}]}
    assert conservation_violations(report, {}) == ["finding counted more than once: x1"]


def test_distinct_finding_ids_on_one_symbol_are_not_duplicates() -> None:
    # Two parameter findings on the same function share kind and symbol but
    # carry distinct per-finding ids; the serializer writes finding_id.
    report = {
        "changes": [
            {"kind": "param_type_changed", "symbol": "f", "finding_id": "a1"},
            {"kind": "param_type_changed", "symbol": "f", "finding_id": "a2"},
        ]
    }
    assert conservation_violations(report, {}) == []


def test_repeated_finding_id_is_a_duplicate() -> None:
    report = {
        "changes": [
            {"kind": "param_type_changed", "symbol": "f", "finding_id": "a1"},
            {"kind": "param_type_changed", "symbol": "f", "finding_id": "a1"},
        ]
    }
    assert conservation_violations(report, {}) == ["finding counted more than once: a1"]


@pytest.mark.parametrize(
    ("audit", "expected"),
    [
        (
            {"counts": {"gating": 1}},
            ["disposition_audit.detected_total is missing or not an integer: None"],
        ),
        (
            {"counts": {"gating": "1"}, "detected_total": 1},
            ["disposition_audit.counts has non-integer values for ['gating']"],
        ),
        (
            {"counts": None, "detected_total": 1},
            ["disposition_audit.counts is missing or not a mapping"],
        ),
    ],
)
def test_malformed_disposition_audit_is_a_violation_not_a_crash(
    audit: dict, expected: list[str]
) -> None:
    assert conservation_violations({"changes": []}, {}) == []
    assert (
        conservation_violations({"changes": [], "disposition_audit": audit}, {})
        == expected
    )
