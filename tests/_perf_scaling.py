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

"""Shared sub-quadratic-scaling measurement for the perf integration tests.

``test_perf_dump_scaling.py``, ``test_perf_dwarf_session_scaling.py``, and
``test_performance.py`` each guarded an O(n^2) regression the same way: time
one small size, time one 4x-larger size, and assert the two-point log-log
ratio (the "exponent") stays under a fixed bound. That is a single sample of
a noisy quantity (wall-clock time on a shared CI runner) turned directly into
a pass/fail boundary -- one slow tick on either measurement swings the ratio
by more than the bound's margin, which is exactly what a real run measured
(exponent 1.98 against a 1.9 threshold, on an otherwise unregressed
``main``). This module doesn't change what's being asserted (still a fixed
sub-quadratic ceiling, not a base-vs-head relative regression -- that needs
its own baseline-file design, out of scope here); it changes how the
exponent is estimated, the same way the report's own recommendation did:
more sizes, repeated per-size measurement collapsed with the median (not the
mean, so one outlier tick doesn't drag the estimate), and an ordinary
least-squares fit across every point instead of one point-to-point ratio.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable


def measure_scaling_exponent(
    fn: Callable[[int], float], sizes: tuple[int, ...], *, repeats: int = 3
) -> float:
    """Least-squares log-log scaling exponent of ``fn(n)``'s elapsed seconds.

    ``fn`` is called ``repeats`` times per size; the *median* elapsed time is
    kept for that size (not the min, which would optimistically hide a real
    per-call regression, and not the mean, which lets a single stalled
    scheduler tick dominate a small sample). The exponent is then the slope
    of an ordinary least-squares fit of ``log(elapsed)`` against ``log(n)``
    over all ``sizes`` -- with 3+ sizes this is materially less sensitive to
    any one size's noise than a two-point ratio, since a single outlier only
    pulls the fitted line, it doesn't solely determine it the way it does
    when there are only two points to begin with.
    """
    if len(sizes) < 2:
        raise ValueError("need at least two sizes to estimate a scaling exponent")
    points: list[tuple[float, float]] = []
    for n in sizes:
        samples = [max(fn(n), 1e-6) for _ in range(repeats)]
        points.append((math.log(n), math.log(statistics.median(samples))))
    mean_x = sum(x for x, _ in points) / len(points)
    mean_y = sum(y for _, y in points) / len(points)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in points)
    denominator = sum((x - mean_x) ** 2 for x, _ in points)
    if denominator == 0:
        # Every size collapsed to the same log(n) -- can't happen with
        # distinct positive sizes, but stay defined rather than dividing by
        # zero if a caller ever passes duplicates.
        return 0.0
    return numerator / denominator
