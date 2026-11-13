"""Ratchet on BREAKING catalog cases whose break a consumer asserts at runtime.

``run_example_runtime_smoke.py`` used to flag only clean verdicts contradicted
at runtime; a BREAKING case whose old consumer kept running (NO_RUNTIME_SIGNAL)
or only printed differently was never tracked. The committed ledger
(``skills-src/evaluation/validation/data/runtime_consumer_proof.json``) splits
every BREAKING case by proof strength. The runtime lane marks a recorded
consumer-assertion case that runs without asserting as PROOF_REGRESSED (a lane
failure); this module keeps the ledger complete, consistent and no-shrink.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_RUNNER = (
    _ROOT / "skills-src/evaluation/validation/scripts/run_example_runtime_smoke.py"
)

#: Measured 2026-11-13 at 509cb0d (Linux, gcc 13.3, Debug): 90 of 118 BREAKING
#: cases. Raise it when the ledger grows; never lower it to admit a lost proof.
CONSUMER_ASSERTION_FLOOR = 90
#: What a BREAKING case without a consumer assertion may record.
_OTHER_PROOFS = {"none", "output_only", "SKIP", "unmeasured"}


def _runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_runtime_smoke_ledger", _RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    return _runner()


@pytest.fixture(scope="module")
def ledger(runner: ModuleType) -> dict:
    return json.loads(runner.CONSUMER_PROOF_LEDGER.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def truth(runner: ModuleType) -> dict:
    return json.loads(runner.GROUND_TRUTH.read_text(encoding="utf-8"))["verdicts"]


def test_consumer_assertion_count_never_shrinks(ledger: dict) -> None:
    got = len(ledger["consumer_assertion"])
    assert got >= CONSUMER_ASSERTION_FLOOR, (
        f"{got} BREAKING cases keep a consumer-assertion proof, below the floor "
        f"{CONSUMER_ASSERTION_FLOOR}: restore the lost proof instead of dropping it"
    )
    assert got == CONSUMER_ASSERTION_FLOOR, (
        f"ledger grew to {got}; raise CONSUMER_ASSERTION_FLOOR to match"
    )


def test_every_breaking_case_has_exactly_one_proof_row(
    ledger: dict, truth: dict
) -> None:
    """The denominator stays complete: a new BREAKING case must be classified."""
    breaking = {c for c, e in truth.items() if e.get("expected") == "BREAKING"}
    asserted = set(ledger["consumer_assertion"])
    other = ledger["breaking_without_consumer_assertion"]
    assert not asserted & set(other)
    assert asserted | set(other) == breaking
    assert set(other.values()) <= _OTHER_PROOFS


def test_recorded_proofs_stay_runnable_breaking_cases(
    runner: ModuleType, ledger: dict, truth: dict
) -> None:
    """A recorded proof whose case left BREAKING or stopped running on Linux
    would silently stop being checked by the runtime lane."""
    assert ledger["platform"] == "linux"
    bad = []
    for case in ledger["consumer_assertion"]:
        entry = truth[case]
        reason = None
        if entry.get("skip") or entry.get("stub_pair") or entry.get("bundle") is True:
            reason = "not runnable"
        elif entry.get("category") == "bundle":
            reason = "bundle"
        elif "linux" not in entry.get("platforms", ["linux"]):
            reason = "not on linux"
        elif not (runner.example_catalog.case_dir(case) / "CMakeLists.txt").exists():
            reason = "no CMakeLists.txt"
        if reason:
            bad.append(f"{case}: {reason}")
    assert not bad, bad


def test_ledger_summary_matches_its_lists(ledger: dict) -> None:
    other = list(ledger["breaking_without_consumer_assertion"].values())
    s = ledger["summary"]
    assert s["consumer_assertion"] == len(ledger["consumer_assertion"])
    assert s["output_only"] == other.count("output_only")
    assert s["none"] == other.count("none")
    assert s["breaking"] == len(ledger["consumer_assertion"]) + len(other)


def _row(case: str, status: str, proof: str | None, expected: str = "BREAKING") -> dict:
    row = {"case_id": case, "status": status, "expected": expected}
    if proof is not None:
        row["proof"] = proof
        row["runtime_signal"] = {
            "consumer_assertion": "nonzero",
            "none": "no_runtime_signal",
        }.get(proof, "stdout_changed")
    return row


@pytest.mark.parametrize(
    ("status", "proof", "regressed"),
    [
        ("DEMONSTRATED", "consumer_assertion", False),
        ("NO_RUNTIME_SIGNAL", "none", True),  # the old consumer kept working
        ("DEMONSTRATED", "output_only", True),  # only printed differently
        ("SKIP", None, False),  # did not run here: nothing to compare
        ("BUILD_ERROR", None, False),  # already a lane failure on its own
    ],
)
def test_a_lost_consumer_assertion_fails_the_lane(
    runner: ModuleType, status: str, proof: str | None, regressed: bool
) -> None:
    ledger = {"platform": "linux", "consumer_assertion": ["case_x"]}
    rows = [_row("case_x", status, proof), _row("case_y", "NO_RUNTIME_SIGNAL", "none")]
    got = runner.apply_consumer_proof_ledger(rows, ledger, "linux")
    assert got == (["case_x"] if regressed else [])
    assert (rows[0]["status"] == "PROOF_REGRESSED") is regressed
    assert ("PROOF_REGRESSED" in runner.PROOF_FAILURE_STATUSES) is True
    assert rows[1]["status"] == "NO_RUNTIME_SIGNAL"  # unrecorded: untouched
    # Another platform's run is not judged against a Linux measurement.
    other = [_row("case_x", "NO_RUNTIME_SIGNAL", "none")]
    assert runner.apply_consumer_proof_ledger(other, ledger, "macos") == []


def test_breaking_proof_summary_and_ledger_writer(runner: ModuleType) -> None:
    rows = [
        _row("a", "DEMONSTRATED", "consumer_assertion"),
        _row("b", "DEMONSTRATED", "output_only"),
        _row("c", "NO_RUNTIME_SIGNAL", "none"),
        _row("d", "SKIP", None),
        _row("e", "DEMONSTRATED", "consumer_assertion", expected="COMPATIBLE"),
    ]
    assert runner.breaking_proof_summary(rows) == {
        "breaking": 4, "consumer_assertion": 1, "output_only": 1, "none": 1, "not_run": 1,
    }  # fmt: skip
    led = runner.consumer_proof_ledger(rows, "linux", "abc123")
    assert led["consumer_assertion"] == ["a"]
    assert led["breaking_without_consumer_assertion"] == {
        "b": "output_only", "c": "none", "d": "SKIP",
    }  # fmt: skip
