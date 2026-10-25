# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the reduced-evidence false-positive guard (check_stripped_fp.py).

The guard must treat each row status the way the validate run defines it:
a PASS/FAIL/XFAIL row with a verdict is checked for spurious breaks; an XFAIL
with no verdict is a *declared* gap and is listed, not failed; an ERROR (or an
undeclared no-verdict row) is a crash and fails the guard.
"""

from __future__ import annotations

import json

import pytest

from tests import check_stripped_fp as guard

# A synthetic ground truth keeps each classification independent of the
# catalog. Field names mirror catalog/ground_truth.json.
GT = {
    "compat_case": {"expected": "COMPATIBLE"},
    "risk_case": {"expected": "COMPATIBLE_WITH_RISK"},
    "break_case": {"expected": "BREAKING"},
    "gap_any_variant": {
        "expected": "COMPATIBLE_WITH_RISK",
        "known_gap": "L0-L2 cannot observe the change",
    },
    "gap_debug_only": {
        "expected": "COMPATIBLE",
        "known_gap": "debug-only gap",
        "known_gap_variants": ["debug-headers"],
    },
}

BUCKETS = ("false_positives", "downgrades", "errors", "unverified")


def _row(case: str, status: str, got: str | None, mode: str = "stripped-headers"):
    return {
        "case_id": case,
        "status": status,
        "got": got,
        "mode": mode,
        "message": "m",
    }


def _buckets(row: dict) -> dict[str, list[str]]:
    fp, down, err, unv = guard._classify_results(
        [row], GT, "stripped-headers", {"platform": "linux"}
    )
    return dict(zip(BUCKETS, (fp, down, err, unv), strict=True))


@pytest.mark.parametrize(
    ("row", "populated"),
    [
        pytest.param(_row("compat_case", "PASS", "COMPATIBLE"), (), id="PASS-clean"),
        pytest.param(
            _row("compat_case", "FAIL", "BREAKING"),
            ("false_positives",),
            id="FAIL-spurious-break",
        ),
        pytest.param(
            _row("break_case", "FAIL", "NO_CHANGE"),
            ("downgrades",),
            id="FAIL-evidence-loss-reported",
        ),
        pytest.param(
            _row("gap_any_variant", "XFAIL", "NO_CHANGE"),
            ("downgrades",),
            id="XFAIL-with-verdict-reported-not-fp",
        ),
        pytest.param(
            _row("gap_debug_only", "XFAIL", "BREAKING"),
            ("false_positives",),
            id="XFAIL-with-verdict-fp-when-gap-not-scoped-here",
        ),
        pytest.param(
            _row("gap_any_variant", "XFAIL", None),
            ("unverified",),
            id="XFAIL-no-verdict-declared-gap",
        ),
        pytest.param(
            _row("compat_case", "XFAIL", None),
            ("errors",),
            id="XFAIL-no-verdict-undeclared-is-error",
        ),
        pytest.param(
            _row("compat_case", "ERROR", None),
            ("errors",),
            id="ERROR-is-error",
        ),
        pytest.param(
            _row("compat_case", "PASS", None),
            ("errors",),
            id="PASS-without-verdict-is-error",
        ),
        pytest.param(_row("compat_case", "SKIP", None), (), id="SKIP-benign"),
    ],
)
def test_row_status_lands_in_expected_bucket(row, populated):
    buckets = _buckets(row)
    assert tuple(name for name, msgs in buckets.items() if msgs) == populated
    for name in populated:
        assert len(buckets[name]) == 1


def test_fp_message_names_case_and_both_verdicts():
    buckets = _buckets(_row("compat_case", "FAIL", "BREAKING"))
    assert buckets["false_positives"] == [
        "compat_case: expected COMPATIBLE got BREAKING"
    ]


def test_declared_gap_message_says_fp_invariant_unchecked():
    buckets = _buckets(_row("gap_any_variant", "XFAIL", None))
    assert buckets["unverified"][0].startswith("gap_any_variant: XFAIL with no verdict")
    assert "not checkable" in buckets["unverified"][0]


def _write_results(path, rows):
    path.write_text(
        json.dumps({"platform": "linux", "results": rows}), encoding="utf-8"
    )
    return path


def test_main_passes_on_declared_no_verdict_gap(tmp_path):
    # case98 is a real catalog case that declares known_gap; its release-headers
    # refusal (no verdict) must not fail the guard.
    rows = [_row("case98_cxx_standard_floor_raised", "XFAIL", None, "release-headers")]
    path = _write_results(tmp_path / "r.json", rows)
    assert guard.main([str(path), "release-headers"]) == 0


def test_main_fails_on_error_row(tmp_path):
    rows = [_row("case98_cxx_standard_floor_raised", "ERROR", None, "release-headers")]
    path = _write_results(tmp_path / "r.json", rows)
    assert guard.main([str(path), "release-headers"]) == 1


def test_main_fails_on_fp_even_when_another_row_is_a_declared_gap(tmp_path):
    rows = [
        _row("case98_cxx_standard_floor_raised", "XFAIL", None, "release-headers"),
        _row("case04_no_change", "FAIL", "BREAKING", "release-headers"),
    ]
    path = _write_results(tmp_path / "r.json", rows)
    assert guard.main([str(path), "release-headers"]) == 1
