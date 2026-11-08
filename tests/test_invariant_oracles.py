"""Unit tests for the invariant oracles in :mod:`tests.oracles`.

Each oracle must fire on a planted violation of its own invariant and stay
silent on a clean input. The planted inputs are hand-written public-shaped
results, not outputs of the detectors under test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.oracles.absence import (
    absence_violations,
    gate_violations,
    rendered_absence_violations,
)
from tests.oracles.conservation import (
    conservation_violations,
    disposition_violations,
    rendered_audit_violations,
)
from tests.oracles.producer import (
    allowlist_growth,
    producer_violations,
    symbol_violations,
)
from tests.oracles.routes import RouteOutcome, route_violations

_ALLOWLIST_FILE = (
    Path(__file__).resolve().parent / "oracles" / "allowlist_baseline.json"
)

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


# -- A: rendered wording -------------------------------------------------------


def test_rendered_headline_claiming_acceptance_on_partial_evidence_is_a_violation() -> (
    None
):
    text = "Compatibility: NO_CHANGE | Gate: ACCEPTED (exit 0)"
    assert rendered_absence_violations({"terminal": text}, "partial") == [
        "terminal states a clean claim ('Gate: ACCEPTED') while analysis_assurance.status is 'partial'",
        "terminal states a clean claim ('Compatibility: NO_CHANGE') while analysis_assurance.status is 'partial'",
    ]


def test_rendered_headline_is_silent_on_complete_evidence() -> None:
    assert rendered_absence_violations({"terminal": "Gate: ACCEPTED"}, "complete") == []


# -- B: symbol-level pairs ------------------------------------------------------


def test_symbol_pairs_agree_silently() -> None:
    pairs = {("func_removed", "helper")}
    assert symbol_violations(pairs, pairs) == []


def test_symbol_pairs_catch_a_same_kind_on_a_different_symbol() -> None:
    # Kinds agree (both report func_removed) but the symbols differ: a kind-only check misses it.
    assert symbol_violations({("func_removed", "a")}, {("func_removed", "b")}) == [
        "only castxml: func_removed on a",
        "only clang: func_removed on b",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "Compatibility: COMPATIBLE_WITH_RISK | Gate: REJECTED",
        "| **Verdict** | ✅ `COMPATIBLE` |",
        "| **Verdict** | ✅ `COMPATIBLE_WITH_RISK` |",
    ],
)
def test_rendered_compatibility_claim_is_flagged_without_an_accepted_gate(
    text: str,
) -> None:
    assert rendered_absence_violations({"markdown": text}, "partial") != []


def test_negative_audit_counts_cannot_cancel_a_positive_one() -> None:
    audit = {"counts": {"gating": 2, "suppressed": -1}, "detected_total": 1}
    assert conservation_violations({"changes": [], "disposition_audit": audit}, {}) == [
        "disposition_audit has negative values (counts ['suppressed'], detected_total 1)"
    ]


def test_non_mapping_disposition_audit_is_a_violation_and_absent_is_allowed() -> None:
    assert conservation_violations(
        {"changes": [], "disposition_audit": "gating"}, {}
    ) == ["disposition_audit is not a mapping: str"]
    assert conservation_violations({"changes": []}, {}) == []


# -- A: release recommendation and exit code -----------------------------------


@pytest.mark.parametrize("bump", ["none", "patch"])
def test_absence_fires_on_a_compatible_version_bump_without_evidence(bump: str) -> None:
    report = {
        "verdict": "UNCONFIRMED",
        "analysis_assurance": {"status": "partial"},
        "release_recommendation": {"version_bump": bump},
    }
    assert absence_violations(report) == [
        f"release_recommendation.version_bump {bump!r} presumes compatibility "
        "while analysis_assurance.status is 'partial'"
    ]


def test_absence_accepts_the_adr_090_unconfirmed_contract() -> None:
    report = {
        "verdict": "UNCONFIRMED",
        "analysis_assurance": {"status": "partial"},
        "release_recommendation": {"version_bump": "undetermined"},
    }
    assert absence_violations(report) == []
    assert gate_violations(1, "partial") == []


@pytest.mark.parametrize("status", ["partial", "failed", "not_comparable"])
def test_gate_fires_on_exit_0_without_evidence(status: str) -> None:
    assert gate_violations(0, status) == [
        f"exit 0 (gate passed) while analysis_assurance.status is {status!r}"
    ]


@pytest.mark.parametrize(
    ("exit_code", "status"), [(0, "complete"), (0, "not_requested"), (4, "partial")]
)
def test_gate_silent_on_supported_exit(exit_code: int, status: str) -> None:
    assert gate_violations(exit_code, status) == []


@pytest.mark.parametrize(
    "text",
    [
        "**Verdict:** ✅ `NO_CHANGE` — no ABI/API change — safe to merge",
        "**Policy:** `strict_abi` · **Gate:** PASS (exit 0)",
        "<h2>🔵 Compatibility: NO_CHANGE</h2>",
    ],
)
def test_review_and_html_clean_wording_is_flagged(text: str) -> None:
    assert rendered_absence_violations({"review": text}, "partial") != []


# -- D: rendered audit totals and disposition agreement --------------------------


def _audited(changes: list[dict], gating: int, non_gating: int) -> dict:
    return {
        "changes": changes,
        "disposition_audit": {
            "detected_total": gating + non_gating,
            "effective_total": gating,
            "counts": {"gating": gating, "non_gating": non_gating},
        },
        "result_counts": {"gating": gating, "retained": len(changes)},
    }


_TWO = [
    {"kind": "func_removed", "finding_id": "a", "gate_contribution": 4},
    {"kind": "func_added", "finding_id": "b", "gate_contribution": 0},
]


def test_rendered_audit_totals_agree_silently() -> None:
    report = _audited(_TWO, 1, 1)
    rendered = {"markdown": {"detected": 2, "gating": 1}, "html": {"detected": 2}}
    assert rendered_audit_violations(report, rendered) == []


def test_rendered_audit_total_drift_is_named_per_renderer() -> None:
    report = _audited(_TWO, 1, 1)
    rendered = {"review": {"detected": 3, "gating": 1}, "html": {"gating": 0}}
    assert rendered_audit_violations(report, rendered) == [
        "html prints gating 0, JSON audit says 1",
        "review prints detected 3, JSON audit says 2",
    ]


def test_rendered_audit_without_a_json_audit_is_a_violation() -> None:
    assert rendered_audit_violations(
        {"changes": []}, {"markdown": {"detected": 0}}
    ) == ["disposition_audit is missing, so rendered totals have no reference"]


def test_disposition_agrees_silently() -> None:
    assert disposition_violations(_audited(_TWO, 1, 1)) == []


def test_disposition_fires_when_gating_changes_outnumber_effective_total() -> None:
    changes = [{**c, "gate_contribution": 4} for c in _TWO]
    assert disposition_violations(_audited(changes, 1, 1)) == [
        "2 changes contribute to the gate, effective_total is 1"
    ]


def test_disposition_fires_on_effective_total_and_result_count_drift() -> None:
    report = _audited(_TWO, 1, 1)
    report["disposition_audit"]["effective_total"] = 2
    report["result_counts"]["retained"] = 5
    assert disposition_violations(report) == [
        "effective_total 2 != counts.gating 1",
        "1 changes contribute to the gate, effective_total is 2",
        "result_counts.gating 1 != effective_total 2",
        "result_counts.retained 5 != 2 changes listed",
    ]


def test_disposition_requires_a_per_change_gate_contribution() -> None:
    changes = [{"kind": "func_removed", "finding_id": "a"}]
    assert disposition_violations(_audited(changes, 1, 0)) == [
        "changes without gate_contribution: ['func_removed']"
    ]


# -- B: the committed allowlist only shrinks ----------------------------------------


def test_producer_allowlist_has_not_grown_past_its_baseline() -> None:
    """B: the JSON allowlist may only shrink. Compared per case, as (kind, side) pairs."""
    data = json.loads(_ALLOWLIST_FILE.read_text(encoding="utf-8"))
    allowlist, baseline = data["allowlist"], data["baseline"]
    growth: list[str] = []
    for case in sorted(set(allowlist) | set(baseline)):
        current = {(kind, side) for kind, side in allowlist.get(case, [])}
        recorded = {(kind, side) for kind, side in baseline.get(case, [])}
        growth.extend(
            f"{case}: {message}" for message in allowlist_growth(current, recorded)
        )
    assert growth == []
