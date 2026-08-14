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

"""Shared repeated-measurement statistics for the performance-gate scripts.

Both ``scripts/benchmark_scaling.py`` and ``scripts/check_header_graph_perf.py``
time a short callable across ``--repeat`` samples and previously each kept the
*fastest* sample (``min()``) as the reported figure. That systematically hides
regressions rather than surfacing them: the minimum across a handful of runs is
the one run least likely to have hit GC, scheduler preemption, or a cold cache
— i.e. the run *least* representative of what a user actually experiences, and
the one most likely to mask a real per-call slowdown that only shows up
sometimes. It also has no notion of run-to-run noise, so a caller can't tell a
clean measurement from a wildly unstable one.

This module is the one place the fix lives, instead of two independently
hand-rolled (and driftable) copies:

- :func:`summarize_samples` reports the **median** as the primary point
  estimate (robust to a single outlier run in either direction), plus
  ``min``/``max``/``p95`` for tail visibility and a coefficient of variation
  (``cv``) as a noise signal a caller can log or gate on.
- :func:`combined_regression_threshold` implements the "stable synthetic
  scenario" regression rule: a point regresses only once its absolute slowdown
  clears the *larger* of a relative floor (a genuinely large percentage
  regression is caught even when the absolute delta is small) and an absolute
  floor (a tiny baseline, where a 2x-in-milliseconds slowdown is still just
  scheduler jitter, isn't flagged on relative noise alone). See the function's
  own docstring for the exact rule.

Callers are expected to discard one *untimed* warmup sample before collecting
the timed ``--repeat`` samples this module summarizes -- warming up caches
(import machinery, ``functools.lru_cache`` memoizers, OS page cache for a
freshly-written fixture) is a one-time cost a real long-lived process pays
once, not a per-call cost a benchmark should keep re-measuring.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class SampleStats:
    """Summary statistics over a list of repeated timing/measurement samples."""

    #: Primary point estimate -- the value every gate/report should use.
    median: float
    min: float
    max: float
    #: Nearest-rank 95th percentile. Equal to ``max`` whenever there are fewer
    #: than 20 samples (the nearest-rank index collapses to the last one) --
    #: a real p95 isn't statistically meaningful at typical --repeat counts
    #: (5-10), so it degrades to the already-meaningful tail signal instead of
    #: claiming false precision.
    p95: float
    #: Coefficient of variation (population stdev / mean) -- a scale-free
    #: noise signal. ``None`` when it can't be computed: fewer than two
    #: samples, or a zero/negative mean (a scale-free ratio is meaningless
    #: there).
    cv: float | None


def summarize_samples(samples: list[float]) -> SampleStats:
    """Compute :class:`SampleStats` over *samples* (must be non-empty)."""
    if not samples:
        raise ValueError("summarize_samples() requires at least one sample")
    ordered = sorted(samples)
    n = len(ordered)
    p95_index = max(0, math.ceil(0.95 * n) - 1)
    cv: float | None = None
    if n >= 2:
        mean = statistics.fmean(ordered)
        if mean > 0:
            cv = statistics.pstdev(ordered) / mean
    return SampleStats(
        median=statistics.median(ordered),
        min=ordered[0],
        max=ordered[-1],
        p95=ordered[p95_index],
        cv=cv,
    )


def combined_regression_threshold(
    base: float, tolerance: float, min_delta: float
) -> float:
    """The allowed absolute delta over *base* before a point counts as regressed.

    ``max(tolerance * base, min_delta)``: a small baseline (a few milliseconds)
    needs the absolute floor so ordinary run-to-run noise doesn't false-flag a
    100%-relative-but-negligible-absolute "regression"; a large baseline needs
    the relative floor so a genuinely large regression isn't hidden behind a
    fixed absolute floor that would be trivially cleared by noise alone.
    Matches the "stable synthetic PR scenario" tier's ``max(15%, 100ms)`` rule
    (see ``docs/contribute/performance.md`` "Baseline regression").
    """
    return max(tolerance * base, min_delta)
