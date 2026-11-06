# SPDX-License-Identifier: Apache-2.0
"""Unit tests of ``catalog_kind_parity``'s two relations, on hand-written kind
sets (the oracle is the set algebra written out here, not the module)."""

from __future__ import annotations

import json

import catalog_kind_parity as kp
import pytest
from catalog_kind_parity import Cell

LANE = kp.LANE_FIELD
FE = kp.FRONTEND_FIELD


@pytest.mark.parametrize(
    ("poor", "rich"),
    [(set(), set()), ({"a"}, {"a"}), ({"a"}, {"a", "b"}), (set(), {"b", "c"})],
)
def test_superset_passes(poor: set[str], rich: set[str]) -> None:
    assert kp.evidence_monotonicity(poor, rich, shared={}) == ((), ())


def test_lost_kind_fails() -> None:
    res = kp.evidence_monotonicity({"a", "b", "c"}, {"a", "d"}, shared={})
    assert res.undeclared == ("b", "c")
    assert not res.ok


def test_declared_and_shared_losses_pass() -> None:
    res = kp.evidence_monotonicity({"a", "b"}, set(), ["a"], shared={"b": "refined"})
    assert res.ok


def test_stale_lane_exception_is_reported() -> None:
    res = kp.evidence_monotonicity({"a"}, {"a"}, ["a", "z"], shared={})
    assert res == ((), ("a", "z"))


def test_frontend_parity_equal_sets_pass() -> None:
    assert kp.frontend_parity({"a", "b"}, {"b", "a"}).ok


def test_frontend_undeclared_difference_fails() -> None:
    res = kp.frontend_parity({"func_removed"}, {"func_visibility_changed"})
    assert res.undeclared == (
        "func_removed (only castxml)",
        "func_visibility_changed (only clang)",
    )


def test_frontend_declared_or_tracked_difference_passes() -> None:
    res = kp.frontend_parity(
        {"x", "y"}, {"z"}, declared=[("x", "castxml"), ("z", "clang")], tracked={"y"}
    )
    assert res.ok


def test_frontend_declaration_on_wrong_side_does_not_explain() -> None:
    res = kp.frontend_parity({"x"}, set(), declared=[("x", "clang")])
    assert res.undeclared == ("x (only castxml)",)
    assert res.stale == ("x (only clang)",)


def test_stale_frontend_declaration_is_reported() -> None:
    res = kp.frontend_parity({"a"}, {"a"}, declared=[("a", "clang")])
    assert res == ((), ("a (only clang)",))


def _cells(cx_d, cx_b, cl_d, cl_b, status="PASS"):
    def c(k):
        return {"case": Cell(status, frozenset(k))}

    return {
        ("castxml", "debug-headers"): c(cx_d),
        ("castxml", "build-source"): c(cx_b),
        ("clang", "debug-headers"): c(cl_d),
        ("clang", "build-source"): c(cl_b),
    }


def test_check_catalog_reports_loss_and_frontend_difference() -> None:
    problems = kp.check_catalog(_cells({"a"}, set(), {"a"}, {"a"}), {"case": {}})
    assert len(problems) == 2
    assert "build-source lost ['a']" in problems[0]
    assert "castxml/clang kinds differ" in problems[1]


def test_check_catalog_honours_case_declarations() -> None:
    entry = {
        LANE: [{"kinds": ["a"], "reason": "r", "frontends": ["castxml"]}],
        FE: [
            {
                "kinds": ["a"],
                "only_in": "clang",
                "modes": ["build-source"],
                "reason": "r",
                "known_gap": "g",
            }
        ],
    }
    assert kp.check_catalog(_cells({"a"}, set(), {"a"}, {"a"}), {"case": entry}) == []


def test_check_catalog_skips_uncomparable_cells() -> None:
    cells = _cells({"a"}, set(), {"b"}, set(), status="SKIP")
    assert kp.check_catalog(cells, {"case": {}}) == []


@pytest.mark.parametrize(
    "entry",
    [
        {LANE: [{"kinds": ["a"]}]},
        {LANE: [{"kinds": ["a"], "reason": " "}]},
        {LANE: [{"kinds": ["a"], "reason": "r", "frontends": ["gcc"]}]},
        {FE: [{"kinds": ["a"], "only_in": "clang", "reason": "r"}]},
        {FE: [{"kinds": [], "only_in": "clang", "reason": "r", "known_gap": "g"}]},
    ],
)
def test_malformed_declarations_are_rejected(entry: dict) -> None:
    assert kp.validate_entry("c", entry)


def test_ground_truth_declarations_are_well_formed() -> None:
    import example_catalog

    verdicts = json.loads(example_catalog.GROUND_TRUTH_PATH.read_text())["verdicts"]
    errors = [e for c, v in verdicts.items() for e in kp.validate_entry(c, v)]
    assert errors == []


def test_ground_truth_known_gaps_name_open_entries() -> None:
    import example_catalog
    import expected_kinds_gate

    headings = expected_kinds_gate.known_gap_headings()
    verdicts = json.loads(example_catalog.GROUND_TRUTH_PATH.read_text())["verdicts"]
    cited = {
        obj["known_gap"]
        for v in verdicts.values()
        for field in (kp.LANE_FIELD, kp.FRONTEND_FIELD)
        for obj in v.get(field, [])
        if "known_gap" in obj
    }
    assert len(cited) >= 2
    assert [g for g in cited if not any(h.startswith(g) for h in headings)] == []
