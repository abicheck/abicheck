"""Unit tests for the scaling benchmark harness (``scripts/benchmark_scaling.py``).

These are fast and stdlib-only: they cover the baseline-regression comparison
logic and assert that *every* registered scenario builds and runs without error
at a tiny size (so a mis-wired scenario fails here rather than silently in CI).
The script is loaded by path because ``scripts/`` is not an installed package.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "benchmark_scaling.py"
_spec = importlib.util.spec_from_file_location("benchmark_scaling", _PATH)
assert _spec and _spec.loader
bench = importlib.util.module_from_spec(_spec)
# Register before exec so dataclasses can resolve ``from __future__`` annotations
# via ``sys.modules[cls.__module__]`` during class creation.
sys.modules["benchmark_scaling"] = bench
_spec.loader.exec_module(bench)


# ── Baseline regression comparison ────────────────────────────────────────────
def test_baseline_points_parses_scenarios() -> None:
    base = {"scenarios": {"add_remove": {"points": [{"size": 500, "seconds": 0.1}]}}}
    assert bench._baseline_points(base) == {("add_remove", 500): 0.1}


def test_baseline_points_tolerates_garbage() -> None:
    assert bench._baseline_points({}) == {}
    assert bench._baseline_points({"scenarios": "nope"}) == {}
    assert bench._baseline_points({"scenarios": {"x": "bad"}}) == {}


def test_check_regressions_flags_slowdown() -> None:
    bp = {("s", 1000): 0.2}
    msgs = bench.check_regressions([bench.Point(1000, 0.4, 1000)], "s", bp, 0.5)
    assert len(msgs) == 1
    assert "+100%" in msgs[0]


def test_check_regressions_within_tolerance_ok() -> None:
    bp = {("s", 1000): 0.2}
    # +25% is under the 50% tolerance.
    assert bench.check_regressions([bench.Point(1000, 0.25, 1000)], "s", bp, 0.5) == []


def test_check_regressions_skips_below_floor() -> None:
    # Baseline below the 0.05s noise floor → not compared even on a huge slowdown.
    bp = {("s", 1000): 0.01}
    assert bench.check_regressions([bench.Point(1000, 1.0, 1000)], "s", bp, 0.5) == []


def test_check_regressions_skips_unknown_size() -> None:
    # Size absent from the baseline (e.g. a scenario new in this PR) is skipped.
    bp = {("s", 500): 0.2}
    assert bench.check_regressions([bench.Point(1000, 5.0, 1000)], "s", bp, 0.5) == []


# ── Every scenario is wired correctly ─────────────────────────────────────────
@pytest.mark.parametrize("scenario", list(bench.SCENARIOS))
def test_every_scenario_builds_and_runs(scenario: str) -> None:
    spec = bench.SCENARIOS[scenario]
    if spec.needs_demangler and not bench._has_demangler():
        pytest.skip(f"{scenario} needs a demangler")
    size = min(spec.sizes[0], 80)
    count = spec.run(spec.build(size))
    assert isinstance(count, int)
    assert count >= 0


def test_versioned_rename_churn_exercises_scheme_collapse() -> None:
    """The ICU/OpenSSL scenario must actually hit the versioned-scheme path.

    A scenario that merely "builds and runs" is a weak guard — assert the shape
    it is meant to stress: ``~2 × n`` removed/added churn findings *and* the
    single ``versioned_symbol_scheme_detected`` collapse finding that no other
    scenario produces. If a refactor stops the scheme recogniser firing on this
    input, this fails rather than the benchmark silently measuring a cheaper
    path.
    """
    from abicheck.checker import ChangeKind, compare

    old, new = bench._build_versioned_rename_churn(200)
    # Detection (default): 2×n churn + the advisory.
    result = compare(old, new)
    kinds = [c.kind for c in result.changes]
    assert kinds.count(ChangeKind.FUNC_REMOVED) == 200
    assert kinds.count(ChangeKind.FUNC_ADDED) == 200
    assert ChangeKind.VERSIONED_SYMBOL_SCHEME_DETECTED in kinds

    # The scenario's registered run enables collapse, so the suppression /
    # reclassification branch must actually fire (churn folded away). Guard it
    # here so the benchmark can't silently revert to measuring detection only.
    assert bench.SCENARIOS["versioned_rename_churn"].run is bench._run_compare_collapse
    collapsed = compare(old, new, collapse_versioned_symbols=True)
    assert len(collapsed.changes) < len(result.changes)


def test_segments_fast_path_matches_full_scan() -> None:
    """The ``_segments`` plain-name fast path must equal the char-scan result."""
    from abicheck.diff_namespaces import _segments

    # Plain names (fast path) and names that need the scan (``::`` / templates).
    assert _segments("u_strlen_75") == ["u_strlen_75"]
    assert _segments("") == []
    assert _segments("ns::experimental::sort<int>") == ["ns", "experimental", "sort"]
    assert _segments("sort<int>") == ["sort"]
    assert _segments("foo<bar>::baz") == ["foo", "baz"]


def test_measure_records_peak_memory() -> None:
    pts = bench.measure("add_remove", [50], repeat=1, track_memory=True)
    assert len(pts) == 1
    assert pts[0].peak_mb is not None
    assert pts[0].peak_mb >= 0.0


def test_measure_can_skip_memory() -> None:
    pts = bench.measure("add_remove", [50], repeat=1, track_memory=False)
    assert pts[0].peak_mb is None
