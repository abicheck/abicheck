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

import argparse
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


def positive_int_arg(value: str) -> int:
    """``argparse`` ``type=`` for a ``--repeat``/``--sizes``-shaped option:
    reject <= 0.

    Both perf-gate scripts sweep a list of sizes and time a number of
    repeats; either accepting a plain ``type=int`` lets ``0`` or a negative
    value through, which is not merely a *meaningless* measurement but an
    actual crash for ``--repeat`` specifically -- zero timed repeats leaves
    :func:`summarize_samples` an empty list, and it raises ``ValueError``
    rather than degrading (CodeRabbit review; this generalizes
    ``check_header_graph_perf.py``'s own originally-local ``_positive_int``,
    now shared here so the two scripts' validation can't independently
    drift). Rejecting at parse time gives a clear ``argparse`` usage error
    instead of either failure mode.
    """
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {value!r}")
    return parsed


def finite_nonnegative_float_arg(value: str) -> float:
    """``argparse`` ``type=`` for a ``--regress-tolerance``/``--regress-min-delta-*``
    -shaped option: reject non-finite and negative values.

    :func:`combined_regression_threshold` computes
    ``max(tolerance * base, min_delta)`` and a caller then fails only when the
    measured delta exceeds that allowed value. A ``nan`` tolerance/min-delta
    makes every such comparison ``False`` (a ``float`` comparison against
    ``nan`` is never true), and an ``inf`` value (accepted by plain
    ``type=float``, including via a literal ``"inf"``/overflowing string like
    ``"1e309"``) makes the allowed delta infinite -- both silently neuter the
    regression gate into always reporting success regardless of how badly a
    measurement regressed (Codex review, fresh evidence). A negative value is
    also meaningless here (it would demand *faster* than baseline, or a
    negative floor, just to pass). Rejecting all of these at parse time gives
    a clear ``argparse`` usage error instead of a silently-neutered gate.
    Generalizes ``check_header_graph_perf.py``'s own originally-local
    ``_finite_nonnegative_float``, now shared here (mirroring
    :func:`positive_int_arg` above) so the two scripts' validation can't
    independently drift -- this bug's own shape (present in
    ``benchmark_scaling.py`` but not its sibling) is exactly what a second,
    independently-hand-rolled copy already caused once.
    """
    parsed = float(value)
    if not math.isfinite(parsed) or parsed < 0:
        raise argparse.ArgumentTypeError(
            f"must be a finite, non-negative number, got {value!r}"
        )
    return parsed


@dataclass(frozen=True)
class GateThreshold:
    """One metric's resolved regression threshold, as reported in a receipt.

    Kept as a value object rather than two loose floats so a report can state
    the *effective* threshold that actually gated a metric. A gate whose
    thresholds are only implied by CLI defaults (or, worse, silently replaced
    by a per-scenario/per-metric exception) cannot be audited from its own
    output -- which is exactly how ``benchmark_scaling.py``'s indefinite
    ``serialize`` ``regress_tolerance=1.3`` override stayed invisible to every
    reader of its JSON report for as long as it did.

    ``source`` names where the two numbers came from (``"default"``,
    ``"explicit"``, ``"metric_override"``, ...) so "the strict value I passed
    was honored" is checkable rather than assumed.
    """

    tolerance: float
    min_delta: float
    source: str = "default"

    def allowed_delta(self, base: float) -> float:
        """The absolute delta over *base* this threshold permits."""
        return combined_regression_threshold(base, self.tolerance, self.min_delta)

    def as_dict(self) -> dict[str, float | str]:
        return {
            "tolerance": self.tolerance,
            "min_delta": self.min_delta,
            "source": self.source,
        }


def resolve_threshold(
    *,
    default: GateThreshold,
    explicit_tolerance: float | None = None,
    explicit_min_delta: float | None = None,
) -> GateThreshold:
    """Fold an optional per-metric override onto *default*.

    The override is *narrowing only in provenance*, never in authority: a
    value the caller stated explicitly always wins, and the returned
    ``source`` records that it did. This is the opposite of the precedence
    bug this module's sibling gates shipped with -- a built-in per-scenario
    exception that outranked an explicitly-requested stricter threshold, so
    ``--regress-tolerance 0.1`` silently ran at 1.3 and the run still printed
    ``OK``. Only a caller-stated value may relax or tighten a default here;
    a built-in exception has to go through the same door, under its own
    ``source`` label, and is therefore visible in the receipt.
    """
    if explicit_tolerance is None and explicit_min_delta is None:
        return default
    return GateThreshold(
        tolerance=(
            default.tolerance if explicit_tolerance is None else explicit_tolerance
        ),
        min_delta=(
            default.min_delta if explicit_min_delta is None else explicit_min_delta
        ),
        source="metric_override",
    )


def is_gateable(value: object) -> bool:
    """True when *value* is a real, finite, strictly-positive measurement.

    Every regression gate in this repository compares ``current > base +
    allowed``. That comparison is ``False`` for a ``nan`` on *either* side and
    for ``base = inf``, so a single non-finite number anywhere in a baseline
    or a sample set turns the gate into an unconditional pass while it still
    prints ``OK`` -- the exact silent-pass shape
    :func:`finite_nonnegative_float_arg` rejects for the *threshold* inputs,
    applied to the *measured* inputs too. ``0`` and negatives are excluded as
    well: neither is a plausible wall-clock duration, and a zero baseline
    makes every relative threshold vacuous.

    Deliberately a positive predicate ("this number may be gated on") rather
    than a ``reject_bad()`` validator, so a caller must name what it does
    with the rejects -- skip-and-report, or fail -- instead of a bare
    exception deciding that for it.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(value) and value > 0
