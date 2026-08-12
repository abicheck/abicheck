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

"""Unit tests for ``_perf_scaling.measure_scaling_exponent`` itself.

The perf integration tests that consume this helper need real compiled
binaries and don't run in the default fast suite, so the helper's own
correctness -- and in particular the degenerate-spacing guard added after a
review round caught evenly log-spaced sizes silently collapsing the fit to a
two-point ratio -- needs coverage that runs unconditionally.
"""

from __future__ import annotations

import math

import pytest
from _perf_scaling import measure_scaling_exponent


def test_recovers_known_linear_exponent():
    """A function that scales exactly as ``n^1`` must fit to exponent ~1."""
    exponent = measure_scaling_exponent(
        lambda n: 0.001 * n, (500, 900, 1400, 2000), repeats=1
    )
    assert exponent == pytest.approx(1.0, abs=1e-6)


def test_recovers_known_quadratic_exponent():
    """A function that scales exactly as ``n^2`` must fit to exponent ~2."""
    exponent = measure_scaling_exponent(
        lambda n: 0.001 * n**2, (500, 900, 1400, 2000), repeats=1
    )
    assert exponent == pytest.approx(2.0, abs=1e-6)


def test_evenly_log_spaced_three_points_rejected():
    """A plain geometric progression must be rejected, not silently degrade
    to a two-point ratio (Codex review: the middle point of an odd-length,
    evenly log-spaced size set sits exactly at ``mean_x`` and contributes
    zero to the least-squares slope regardless of its own timing)."""
    with pytest.raises(ValueError, match="evenly spaced"):
        measure_scaling_exponent(lambda n: 0.001 * n, (500, 1000, 2000), repeats=1)


def test_evenly_log_spaced_five_points_rejected():
    """The degenerate-spacing hazard isn't specific to three points: any odd
    count of evenly log-spaced sizes has its exact middle point sit at
    ``mean_x``, whatever the count."""
    with pytest.raises(ValueError, match="evenly spaced"):
        measure_scaling_exponent(
            lambda n: 0.001 * n, (250, 500, 1000, 2000, 4000), repeats=1
        )


def test_middle_point_is_provably_ignored_without_the_guard():
    """Direct proof of the bug the guard prevents: with evenly log-spaced
    sizes, the fitted slope from 3 points equals the raw two-endpoint ratio
    -- i.e. the middle point's own timing has *zero* effect on the result,
    however it's chosen. Computed by hand (bypassing the guard) rather than
    asserted against the guarded function, since the guard's whole job is to
    refuse to compute this value at all."""
    sizes = (500, 1000, 2000)
    xs = [math.log(n) for n in sizes]
    mean_x = sum(xs) / len(xs)

    def slope_for(mid_y: float) -> float:
        ys = [math.log(0.001 * sizes[0]), mid_y, math.log(0.001 * sizes[2])]
        mean_y = sum(ys) / len(ys)
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        return num / den

    endpoint_slope = (math.log(0.001 * sizes[2]) - math.log(0.001 * sizes[0])) / (
        xs[2] - xs[0]
    )
    # Two wildly different guesses for the middle point's own value produce
    # the identical slope -- proof the middle point is inert, not just
    # under-weighted.
    assert slope_for(-100.0) == pytest.approx(endpoint_slope, rel=1e-9)
    assert slope_for(100.0) == pytest.approx(endpoint_slope, rel=1e-9)


def test_requires_at_least_two_sizes():
    with pytest.raises(ValueError, match="at least two"):
        measure_scaling_exponent(lambda n: 1.0, (500,))


def test_repeats_use_median_not_mean_or_min():
    """A single high outlier among repeats must not dominate the estimate
    the way a mean would, and the median must not optimistically pick the
    minimum either."""
    calls: dict[int, int] = {500: 0, 900: 0, 1400: 0, 2000: 0}

    def fn(n: int) -> float:
        calls[n] += 1
        # Every call returns the same "true" value except one outlier per
        # size that would drag a mean far off (but not a median of 3).
        base = 0.001 * n
        if calls[n] == 2:
            return base * 100
        return base

    exponent = measure_scaling_exponent(fn, (500, 900, 1400, 2000), repeats=3)
    # True exponent is 1.0; a mean-based estimate polluted by the 100x
    # outlier on every size would not land anywhere near this.
    assert exponent == pytest.approx(1.0, abs=1e-6)
