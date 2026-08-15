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

"""Baseline-regression comparison for ``scripts/benchmark_scaling.py``.

Split out of ``benchmark_scaling.py`` (rather than grown inline) purely to
keep that file under the AI-readiness file-size gate's line cap — see this
repo's `CLAUDE.md` "Files that are large" guidance: prefer extending a
split-out sibling module over growing the parent toward the 2000-line hard
cap. Depends only on ``perf_measurement.py``'s shared threshold math, and
takes any object exposing ``.size``/``.seconds`` (duck-typed, not a real
import of ``benchmark_scaling.Point``) — so there is no import cycle back to
the parent module.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

# No sys.path mutation here (CodeRabbit review): this is an imported helper
# module, not a script entry point, so it must not have global side effects
# merely from being imported (scripts/CLAUDE.md's own "No global side effects
# at import time" convention). Its only current caller, benchmark_scaling.py,
# already puts this file's own directory on sys.path before importing it, so
# `perf_measurement` is importable by the time this module loads; a future
# caller that doesn't go through benchmark_scaling.py first would need to do
# the same setup itself (the same pattern tests/test_perf_measurement.py
# already uses to load perf_measurement.py standalone).
from perf_measurement import combined_regression_threshold


class _TimedPoint(Protocol):
    size: int
    seconds: float


def baseline_points_from_report(
    baseline: dict[str, Any],
) -> dict[tuple[str, int], float]:
    """Map ``(scenario, size) -> seconds`` from a ``benchmark_scaling.py``
    report's JSON (its own ``{"scenarios": {name: {"points": [...]}}}`` shape).
    """
    out: dict[tuple[str, int], float] = {}
    scenarios = baseline.get("scenarios", {})
    if not isinstance(scenarios, dict):
        return out
    for name, body in scenarios.items():
        if not isinstance(body, dict):
            continue
        for pt in body.get("points", []):
            if isinstance(pt, dict) and "size" in pt and "seconds" in pt:
                out[(name, int(pt["size"]))] = float(pt["seconds"])
    return out


def load_baseline(
    baseline_path: Path, regress_tolerance: float
) -> dict[tuple[str, int], float]:
    """Load baseline scaling JSON and return its (scenario, size) -> seconds mapping.

    Prints a summary line on success and a warning on failure; returns an empty
    dict when the file cannot be read or parsed. The caller (``main()``) is
    responsible for treating an empty result as a hard failure when
    ``--baseline`` was explicitly given -- this function only loads, never gates.
    """
    import json

    try:
        points = baseline_points_from_report(json.loads(baseline_path.read_text()))
        print(
            f"Comparing against baseline {baseline_path} "
            f"({len(points)} points, tolerance "
            f"{regress_tolerance * 100:.0f}%)"
        )
        return points
    except (OSError, ValueError) as e:
        print(f"WARNING: could not load baseline {baseline_path}: {e}")
        return {}


def matched_baseline_points(
    current: Sequence[_TimedPoint],
    scenario: str,
    baseline: dict[tuple[str, int], float],
    *,
    floor_seconds: float = 0.05,
) -> list[_TimedPoint]:
    """The subset of *current* that has a usable *baseline* entry for *scenario*.

    "Usable" mirrors ``check_regressions``'s own skip conditions (a present,
    ``>= floor_seconds`` baseline value and a positive current time) — used to
    distinguish "every point checked out fine" from "the baseline covered
    nothing this run measured" (e.g. a stale/mistargeted ``--baseline`` file,
    or a scenario-name/size-sweep change), the same "matched vs. reported OK"
    distinction ``check_header_graph_perf.py``'s own ``matched_points`` already
    draws for its baseline. Without this, a completely non-overlapping
    ``--baseline`` silently produces zero ``check_regressions`` failures and
    reads as a clean pass.
    """
    return [
        p
        for p in current
        if (base := baseline.get((scenario, p.size))) is not None
        and base >= floor_seconds
        and p.seconds > 0
    ]


def check_regressions(
    current: Sequence[_TimedPoint],
    scenario: str,
    baseline: dict[tuple[str, int], float],
    tolerance: float,
    *,
    floor_seconds: float = 0.05,
    min_delta_seconds: float = 0.0,
) -> list[str]:
    """Return regression messages where *current* is slower than *baseline*.

    A point regresses when its time exceeds the baseline's by more than
    ``max(tolerance * baseline, min_delta_seconds)`` — see
    ``perf_measurement.combined_regression_threshold``: a relative-only rule
    (``min_delta_seconds=0``, the default) is a pure percentage tolerance
    (e.g. ``0.5`` = 50 %); a caller wanting "stable synthetic PR scenario"
    behaviour (``max(15%, 100ms)``) passes both. Points whose *baseline* time
    is below ``floor_seconds`` are skipped outright — sub-50 ms timings are
    dominated by noise and would flag spuriously regardless of the threshold
    rule. Sizes absent from the baseline (e.g. a scenario new in this PR) are
    skipped, so the comparison is over the intersection only.
    """
    msgs: list[str] = []
    for p in current:
        base = baseline.get((scenario, p.size))
        if base is None or base < floor_seconds or p.seconds <= 0:
            continue
        delta = p.seconds - base
        allowed = combined_regression_threshold(base, tolerance, min_delta_seconds)
        if delta > allowed:
            ratio = delta / base
            msgs.append(
                f"{scenario} @ size={p.size}: {p.seconds:.3f}s vs baseline "
                f"{base:.3f}s (+{ratio * 100:.0f}%, +{delta:.3f}s; allowed "
                f"+{allowed:.3f}s = max({tolerance * 100:.0f}%, "
                f"{min_delta_seconds:.3f}s))"
            )
    return msgs
