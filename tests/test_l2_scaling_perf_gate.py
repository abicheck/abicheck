# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Tests for the full-CLI L2 scaling gate (``scripts/check_l2_scaling_perf.py``).

The real sweep compiles fixtures and runs the CLI, so it lives in the
``l2-cli-perf`` CI job; these tests pin the parts that decide pass/fail --
the marginal-exponent fit, the release-report validation, and the gate -- plus
the workflow wiring that makes the job actually run it.
"""

from __future__ import annotations

import importlib.util
import itertools
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"


def _load(name: str):
    if str(_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate_mod = _load("check_l2_scaling_perf")
Point = gate_mod.Point


def _points(sizes, wall_of) -> list:
    return [Point(axis="libraries", n=n, walls=[wall_of(n)]) for n in sizes]


class TestMarginalExponent:
    """The fit recovers the power law of the *added* work, whatever the floor.

    Oracle: the exponent ``k`` the synthetic walls were generated from -- not
    the fitting helper itself.
    """

    @pytest.mark.parametrize(
        ("k", "floor", "unit", "sizes"),
        list(
            itertools.product(
                (0.8, 1.0, 1.5, 2.0, 3.0),
                (0.3, 1.2, 5.0),
                (0.05, 0.4),
                ((1, 3, 5, 9), (1, 4, 12, 30), (1, 2, 7)),
            )
        ),
    )
    def test_recovers_generating_exponent(self, k, floor, unit, sizes):
        pts = _points(sizes, lambda n: floor + unit * (n - 1) ** k)
        exponent, reason = gate_mod.marginal_exponent(pts)
        if unit * (max(sizes) - 1) ** k < gate_mod.MIN_MARGINAL_SECONDS:
            assert exponent is None and "no measurable work" in reason
        else:
            assert reason is None
            assert exponent == pytest.approx(k, abs=1e-6)

    def test_fixed_floor_does_not_hide_quadratic_growth(self):
        # The raw log-log slope of these walls is well under 1 because the
        # 1.2 s floor dominates -- the very thing the marginal fit exists for.
        pts = _points((1, 3, 5, 9), lambda n: 1.2 + 0.1 * (n - 1) ** 2)
        exponent, _ = gate_mod.marginal_exponent(pts)
        assert exponent == pytest.approx(2.0, abs=1e-6)

    def test_order_of_points_is_irrelevant(self):
        pts = _points((1, 3, 5, 9), lambda n: 1.0 + 0.3 * (n - 1) ** 1.3)
        a, _ = gate_mod.marginal_exponent(pts)
        b, _ = gate_mod.marginal_exponent(list(reversed(pts)))
        assert a == pytest.approx(b)

    def test_missing_floor_is_a_reason_not_a_pass(self):
        exponent, reason = gate_mod.marginal_exponent(
            _points((3, 5, 9), lambda n: float(n))
        )
        assert exponent is None and "floor" in reason

    def test_one_point_above_floor_cannot_be_fitted(self):
        exponent, reason = gate_mod.marginal_exponent(
            _points((1, 9), lambda n: float(n))
        )
        assert exponent is None and "two points" in reason

    def test_point_below_floor_is_clamped_not_dropped(self):
        # n=3 faster than the floor must not quietly reduce the fit to two points.
        walls = {1: 2.0, 3: 1.9, 5: 3.0, 9: 6.0}
        exponent, reason = gate_mod.marginal_exponent(_points(walls, walls.get))
        assert reason is None and exponent is not None and exponent > 2.0


class TestGate:
    def test_exponent_over_budget_fails(self):
        pts = _points((1, 3, 5, 9), lambda n: 1.0 + 0.2 * (n - 1) ** 2)
        exponent, failures = gate_mod.gate(
            "libraries", pts, max_exponent=1.5, max_rss_mb=1024
        )
        assert exponent == pytest.approx(2.0)
        assert any("exceeds budget" in f for f in failures)

    def test_linear_within_budget_passes(self):
        pts = _points((1, 3, 5, 9), lambda n: 1.0 + 0.2 * (n - 1))
        _, failures = gate_mod.gate("libraries", pts, max_exponent=1.5, max_rss_mb=1024)
        assert failures == []

    def test_unfittable_sweep_fails(self):
        pts = _points((1, 3, 5, 9), lambda n: 1.0)
        _, failures = gate_mod.gate("headers", pts, max_exponent=1.5, max_rss_mb=1024)
        assert failures and "cannot fit" in failures[0]

    def test_rss_ceiling_is_enforced_per_point(self):
        pts = _points((1, 3, 5, 9), lambda n: 1.0 + 0.2 * (n - 1))
        pts[2].peak_rss_mb = 2000.0
        _, failures = gate_mod.gate("libraries", pts, max_exponent=1.5, max_rss_mb=1024)
        assert failures == [
            "libraries n=5: peak process-tree RSS 2000 MB exceeds 1024 MB"
        ]

    def test_correctness_problem_fails_even_when_fast(self):
        pts = _points((1, 3, 5, 9), lambda n: 1.0 + 0.2 * (n - 1))
        pts[1].problems.append("compared 2 libraries, expected 3")
        _, failures = gate_mod.gate("libraries", pts, max_exponent=9, max_rss_mb=1e9)
        assert failures == ["libraries n=3: compared 2 libraries, expected 3"]


def _release(n: int, **overrides) -> dict:
    report = {
        "verdict": "BREAKING",
        "unmatched_old": [],
        "unmatched_new": [],
        "run_outcome": {"scope": "complete"},
        "libraries": [
            {
                "library": f"lib{i}.so",
                "verdict": "BREAKING",
                "breaking": 3,
                "analysis_assurance_status": "complete",
                "scope_resolved": True,
                "surface_scope": {"enabled": True},
            }
            for i in range(n)
        ],
    }
    report.update(overrides)
    return report


class TestReleaseValidation:
    def test_complete_release_is_valid(self):
        assert gate_mod.validate_release_report(_release(3), 3) == []

    @pytest.mark.parametrize(
        ("mutate", "needle"),
        [
            (lambda r: r["libraries"].pop(), "compared 2 libraries"),
            (lambda r: r.update(unmatched_new=["x.so"]), "unmatched_new"),
            (lambda r: r["run_outcome"].update(scope="incomplete"), "run_outcome"),
            (lambda r: r.update(verdict="NO_CHANGE"), "verdict="),
            (lambda r: r["libraries"][1].update(breaking=0), "no break found"),
            (
                lambda r: r["libraries"][0].update(scope_resolved=False),
                "did not resolve",
            ),
            (
                lambda r: r["libraries"][2]["surface_scope"].update(enabled=False),
                "did not resolve",
            ),
            (
                lambda r: r["libraries"][0].update(analysis_assurance_status="partial"),
                "assurance",
            ),
        ],
    )
    def test_each_degradation_is_caught(self, mutate, needle):
        report = _release(3)
        mutate(report)
        problems = gate_mod.validate_release_report(report, 3)
        assert any(needle in p for p in problems), problems


class TestArgs:
    def test_sizes_must_include_floor(self):
        with pytest.raises(SystemExit):
            gate_mod.parse_args(["--library-sizes", "2,4,8"])

    def test_sizes_are_sorted_and_deduplicated(self):
        args = gate_mod.parse_args(["--header-sizes", "8,1,3,3"])
        assert args.header_sizes == (1, 3, 8)

    def test_default_sizes_are_not_log_evenly_spaced_above_floor(self):
        import math

        for sizes in gate_mod.DEFAULT_SIZES.values():
            xs = [math.log(n - 1) for n in sizes if n > 1]
            mean = sum(xs) / len(xs)
            assert all(not math.isclose(x, mean, abs_tol=1e-3) for x in xs)


def test_pr_perf_job_runs_the_scaling_gate():
    workflow = (_ROOT / ".github/workflows/performance.yml").read_text(encoding="utf-8")
    job = workflow.split("  l2-cli-perf:", 1)[1].split("\n  l2-cli-extended:", 1)[0]
    assert "scripts/check_l2_scaling_perf.py" in job
    assert "--require-toolchain" in job.split("check_l2_scaling_perf.py", 1)[1]
