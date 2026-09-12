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
from typing import Protocol

# No sys.path mutation here (CodeRabbit review): this is an imported helper
# module, not a script entry point, so it must not have global side effects
# merely from being imported (scripts/CLAUDE.md's own "No global side effects
# at import time" convention). Its only current caller, benchmark_scaling.py,
# already puts this file's own directory on sys.path before importing it, so
# `perf_measurement` is importable by the time this module loads; a future
# caller that doesn't go through benchmark_scaling.py first would need to do
# the same setup itself (the same pattern tests/test_perf_measurement.py
# already uses to load perf_measurement.py standalone).
from perf_measurement import GateThreshold, combined_regression_threshold

#: The CLI-wide regression-gate defaults for ``benchmark_scaling.py``. They
#: live here, next to the resolver that applies them, rather than as
#: ``argparse`` ``default=`` values: those flags default to ``None`` so that
#: "the caller asked for 0.5" can be told apart from "0.5 is the fallback",
#: which is the distinction :func:`resolve_scenario_threshold` needs to stop a
#: built-in per-scenario exception from outranking an explicit threshold.
DEFAULT_REGRESS_TOLERANCE = 0.5
DEFAULT_REGRESS_MIN_DELTA_SECONDS = 0.0


class _TimedPoint(Protocol):
    size: int
    seconds: float


def baseline_points_from_report(
    baseline: object,
) -> dict[tuple[str, int], float]:
    """Map ``(scenario, size) -> seconds`` from a ``benchmark_scaling.py``
    report's JSON (its own ``{"scenarios": {name: {"points": [...]}}}`` shape).

    *baseline* is untyped ``object``, not ``dict[str, Any]`` (CodeRabbit
    review): the caller's only guarantee is "valid JSON", and a syntactically
    valid but structurally wrong top level -- a bare list (``[]``), a string,
    a number -- has no ``.get()`` and previously raised an unhandled
    ``AttributeError`` instead of degrading the same way every other
    malformed-shape case here already does (an unknown-shaped ``scenarios``/
    ``points``/point entry is skipped, not fatal).
    """
    out: dict[tuple[str, int], float] = {}
    if not isinstance(baseline, dict):
        return out
    scenarios = baseline.get("scenarios", {})
    if not isinstance(scenarios, dict):
        return out
    for name, body in scenarios.items():
        if not isinstance(body, dict):
            continue
        points = body.get("points", [])
        if not isinstance(points, list):
            continue
        for pt in points:
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


def resolve_scenario_threshold(
    *,
    scenario: str,
    cli_tolerance: float | None,
    cli_min_delta: float | None,
    default_tolerance: float,
    default_min_delta: float,
    spec_tolerance: float | None = None,
    spec_min_delta: float | None = None,
) -> GateThreshold:
    """The effective threshold for *scenario*, with correct precedence.

    The rule, and the bug it replaces:

    ``benchmark_scaling.py``'s ``Scenario.regress_tolerance`` was a built-in
    per-scenario exception that won **unconditionally** -- it was consulted
    first, and the CLI value was used only when it was ``None``. So
    ``--regress-tolerance 0.1``, an explicit request for a *stricter* gate,
    ran the ``serialize`` scenario at ``1.3`` and still printed ``OK``. A
    built-in allowance silently outranking an explicitly stated stricter
    threshold is not a tunable default; it is a gate that lies about what it
    enforced, and nothing in the run's own output revealed it.

    Here the order is: an explicitly-given CLI value wins over everything
    (``source="explicit"``); otherwise a built-in per-scenario exception
    applies (``source="scenario_default"``); otherwise the CLI default
    (``source="default"``). The distinction a bare ``float`` default cannot
    express -- "the user typed 0.5" vs. "0.5 is the default" -- is why the
    caller passes ``None`` for "not stated" and supplies the default
    separately.

    The returned value is recorded in the report, so a reader can always see
    which of the three sources produced the number that gated them.
    """
    if cli_tolerance is not None or cli_min_delta is not None:
        return GateThreshold(
            tolerance=(
                cli_tolerance
                if cli_tolerance is not None
                else (
                    spec_tolerance if spec_tolerance is not None else default_tolerance
                )
            ),
            min_delta=(
                cli_min_delta
                if cli_min_delta is not None
                else (
                    spec_min_delta if spec_min_delta is not None else default_min_delta
                )
            ),
            source="explicit",
        )
    if spec_tolerance is not None or spec_min_delta is not None:
        return GateThreshold(
            tolerance=spec_tolerance
            if spec_tolerance is not None
            else default_tolerance,
            min_delta=spec_min_delta
            if spec_min_delta is not None
            else default_min_delta,
            source=f"scenario_default:{scenario}",
        )
    return GateThreshold(
        tolerance=default_tolerance, min_delta=default_min_delta, source="default"
    )


def apply_regression_gate(
    current: Sequence[_TimedPoint],
    scenario: str,
    baseline: dict[tuple[str, int], float],
    *,
    cli_tolerance: float | None,
    cli_min_delta: float | None,
    spec_tolerance: float | None = None,
    spec_min_delta: float | None = None,
    record_into: dict[str, object] | None = None,
    floor_seconds: float = 0.05,
) -> list[str]:
    """Resolve *scenario*'s effective threshold, record it, and gate on it.

    One function rather than three steps at the call site, because the three
    have to stay together to be correct: a threshold that is resolved but not
    recorded is a gate whose own output cannot be audited, and a threshold that
    is recorded but then not the one passed to :func:`check_regressions` is
    worse than none at all. Keeping them adjacent in the caller is a
    convention; keeping them in one function is an invariant.

    *record_into* is the scenario's own report dict; the resolved threshold
    lands there under ``effective_threshold``. ``None`` skips recording, for a
    caller with no report to write (a test, or a gate run with no ``--json``).
    """
    threshold = resolve_scenario_threshold(
        scenario=scenario,
        cli_tolerance=cli_tolerance,
        cli_min_delta=cli_min_delta,
        default_tolerance=DEFAULT_REGRESS_TOLERANCE,
        default_min_delta=DEFAULT_REGRESS_MIN_DELTA_SECONDS,
        spec_tolerance=spec_tolerance,
        spec_min_delta=spec_min_delta,
    )
    if record_into is not None:
        record_into["effective_threshold"] = threshold.as_dict()
    print(
        f"  (gate threshold: tolerance={threshold.tolerance} "
        f"min_delta_seconds={threshold.min_delta} source={threshold.source})"
    )
    return check_regressions(
        current,
        scenario,
        baseline,
        threshold.tolerance,
        floor_seconds=floor_seconds,
        min_delta_seconds=threshold.min_delta,
    )
