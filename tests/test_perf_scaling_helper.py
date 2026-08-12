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
    """Median-of-3 must recover the true exponent even with an outlier
    present, in a fixture that would *actually* fail under mean or min
    aggregation instead -- not one where all three aggregators happen to
    agree (a constant-multiplier or negligible outlier changes a mean's
    fitted *intercept*, not its slope, so a naive fixture can pass under
    mean/min/median alike without exercising the median requirement at
    all; caught by review on the first version of this test)."""
    sizes = (500, 900, 1400, 2000)

    def true_value(n: int) -> float:
        return 0.001 * n  # exponent 1

    def outlier(n: int) -> float:
        # Quadratic (exponent 2) and strictly below true_value(n) at every
        # tested size: never the sorted-middle element against two equal
        # true_value(n) samples (so it can't become the median regardless
        # of magnitude), but large enough -- and differently-shaped enough
        # -- to visibly bend both a mean (true_value*2/3 plus a growing,
        # not-negligible third term) and a min (which would just report the
        # outlier outright) away from true_value's own exponent of 1.
        return 4e-7 * n**2

    assert all(outlier(n) < true_value(n) for n in sizes), (
        "fixture precondition: outlier must never be the largest sample, "
        "or median would stop recovering true_value exactly"
    )

    calls: dict[int, int] = dict.fromkeys(sizes, 0)

    def fn(n: int) -> float:
        calls[n] += 1
        # Two of three repeats are the true value, so the median always
        # recovers it exactly regardless of the outlier's magnitude; the
        # third repeat is the outlier.
        return outlier(n) if calls[n] == 2 else true_value(n)

    exponent = measure_scaling_exponent(fn, sizes, repeats=3)
    assert exponent == pytest.approx(1.0, abs=1e-6)

    # Prove this fixture actually distinguishes the aggregators -- computed
    # independently via the same least-squares formula the helper uses, not
    # by calling the (median-only) helper again.
    def _fit(vals: list[float]) -> float:
        xs = [math.log(n) for n in sizes]
        ys = [math.log(v) for v in vals]
        mean_x = sum(xs) / len(xs)
        mean_y = sum(ys) / len(ys)
        num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
        den = sum((x - mean_x) ** 2 for x in xs)
        return num / den

    mean_exponent = _fit([(2 * true_value(n) + outlier(n)) / 3 for n in sizes])
    min_exponent = _fit([min(true_value(n), outlier(n)) for n in sizes])
    assert abs(mean_exponent - 1.0) > 0.1, "fixture doesn't actually distinguish mean"
    assert abs(min_exponent - 1.0) > 0.1, "fixture doesn't actually distinguish min"
