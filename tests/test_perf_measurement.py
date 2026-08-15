# Copyright 2026 Nikolay Petrov
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

"""Unit tests for ``scripts/perf_measurement.py``.

Pure-stdlib and fast: pins the median-not-fastest reporting contract and the
combined relative/absolute regression-threshold rule both
``benchmark_scaling.py`` and ``check_header_graph_perf.py`` rely on.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "scripts" / "perf_measurement.py"
_spec = importlib.util.spec_from_file_location("perf_measurement", _PATH)
assert _spec and _spec.loader
pm = importlib.util.module_from_spec(_spec)
sys.modules["perf_measurement"] = pm
_spec.loader.exec_module(pm)


class TestSummarizeSamples:
    def test_rejects_empty_input(self) -> None:
        with pytest.raises(ValueError):
            pm.summarize_samples([])

    def test_single_sample_is_its_own_stats(self) -> None:
        s = pm.summarize_samples([1.0])
        assert (s.median, s.min, s.max, s.p95) == (1.0, 1.0, 1.0, 1.0)
        # A lone sample has no variance to report.
        assert s.cv is None

    def test_median_is_not_the_minimum(self) -> None:
        # The whole point of this module: a single fast outlier must not win.
        s = pm.summarize_samples([1.0, 10.0, 10.0, 10.0, 10.0])
        assert s.median == 10.0
        assert s.min == 1.0
        assert s.max == 10.0

    def test_median_of_even_count_is_the_midpoint_average(self) -> None:
        s = pm.summarize_samples([1.0, 2.0, 3.0, 4.0])
        assert s.median == 2.5

    def test_p95_degrades_to_max_below_twenty_samples(self) -> None:
        # Nearest-rank p95 over 5 samples is meaningless as a "5% tail"; it
        # should read as the observed max rather than claim false precision.
        s = pm.summarize_samples([1.0, 2.0, 3.0, 4.0, 100.0])
        assert s.p95 == s.max == 100.0

    def test_p95_is_a_real_tail_estimate_at_larger_n(self) -> None:
        samples = list(range(1, 101))  # 1..100
        s = pm.summarize_samples([float(x) for x in samples])
        assert s.p95 == 95.0
        assert s.max == 100.0
        assert s.p95 < s.max

    def test_cv_is_zero_for_identical_samples(self) -> None:
        s = pm.summarize_samples([5.0, 5.0, 5.0])
        assert s.cv == 0.0

    def test_cv_is_none_for_non_positive_mean(self) -> None:
        s = pm.summarize_samples([0.0, 0.0])
        assert s.cv is None
        s2 = pm.summarize_samples([-1.0, 1.0])  # mean == 0
        assert s2.cv is None

    def test_cv_reflects_relative_spread(self) -> None:
        tight = pm.summarize_samples([10.0, 10.1, 9.9, 10.0])
        loose = pm.summarize_samples([1.0, 20.0, 1.0, 20.0])
        assert tight.cv is not None and loose.cv is not None
        assert tight.cv < loose.cv


class TestCombinedRegressionThreshold:
    def test_relative_floor_wins_on_a_large_baseline(self) -> None:
        # 15% of 10s (1.5s) dwarfs a 100ms absolute floor.
        assert pm.combined_regression_threshold(10.0, 0.15, 0.1) == pytest.approx(1.5)

    def test_absolute_floor_wins_on_a_tiny_baseline(self) -> None:
        # 15% of 10ms (1.5ms) is noise; the 100ms floor protects it.
        assert pm.combined_regression_threshold(0.01, 0.15, 0.1) == pytest.approx(0.1)

    def test_zero_min_delta_reduces_to_pure_percentage(self) -> None:
        # Back-compat shape: a caller that never asks for an absolute floor
        # gets exactly the old pure-relative-tolerance behaviour.
        assert pm.combined_regression_threshold(2.0, 0.5, 0.0) == pytest.approx(1.0)


class TestPositiveIntArg:
    def test_accepts_positive_values(self) -> None:
        assert pm.positive_int_arg("1") == 1
        assert pm.positive_int_arg("5") == 5

    def test_rejects_zero(self) -> None:
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            pm.positive_int_arg("0")

    def test_rejects_negative(self) -> None:
        import argparse

        with pytest.raises(argparse.ArgumentTypeError):
            pm.positive_int_arg("-1")
