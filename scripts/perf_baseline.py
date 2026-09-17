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

import argparse
import math
from collections.abc import Mapping, Sequence
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
from perf_measurement import (
    GateThreshold,
    combined_regression_threshold,
    finite_nonnegative_float_arg,
)

#: The CLI-wide regression-gate defaults for ``benchmark_scaling.py``. They
#: live here, next to the resolver that applies them, rather than as
#: ``argparse`` ``default=`` values: those flags default to ``None`` so that
#: "the caller asked for 0.5" can be told apart from "0.5 is the fallback",
#: which is the distinction :func:`resolve_scenario_threshold` needs to stop a
#: built-in per-scenario exception from outranking an explicit threshold.
DEFAULT_REGRESS_TOLERANCE = 0.5
DEFAULT_REGRESS_MIN_DELTA_SECONDS = 0.0

#: Memory defaults are deliberately *tighter* than the timing ones above.
#: A ``tracemalloc`` peak is a count of bytes the interpreter actually
#: allocated, not a wall-clock duration: it does not move with GC timing,
#: scheduler preemption, or a cold cache, so the run-to-run noise that forces
#: a 50% timing tolerance is largely absent. A 20% growth in allocated bytes
#: for the same input is a real change in what the code builds.
DEFAULT_REGRESS_MEMORY_TOLERANCE = 0.20
#: ...paired with an absolute floor, for the same reason the timing rule has
#: one: at a few MiB, import-time and fixture allocations dominate, and a
#: relative rule alone would flag that as a regression.
DEFAULT_REGRESS_MIN_DELTA_MB = 4.0
#: Baseline points below this are skipped outright -- see ``floor_seconds``.
DEFAULT_MEMORY_FLOOR_MB = 8.0


class _TimedPoint(Protocol):
    size: int
    seconds: float


class _MeasuredPoint(Protocol):
    """A point carrying a peak-memory figure as well as a size.

    Separate from :class:`_TimedPoint` on purpose: ``peak_mb`` is optional at
    the source (memory tracking can be switched off with ``--no-memory``, and
    ``rss_mb`` is unavailable on some platforms), so a consumer of this
    protocol must handle ``None`` where a timing consumer never has to.
    """

    size: int
    peak_mb: float | None


def baseline_points_from_report(
    baseline: object,
    *,
    field: str = "seconds",
) -> dict[tuple[str, int], float]:
    """Map ``(scenario, size) -> <field>`` from a ``benchmark_scaling.py``
    report's JSON (its own ``{"scenarios": {name: {"points": [...]}}}`` shape).

    *field* selects which measurement to extract: ``"seconds"`` (the default,
    the timing baseline) or ``"peak_mb"`` (the memory baseline). One parser
    rather than two, so a malformed-shape case cannot be handled one way for
    time and another for memory. A point whose *field* is absent or ``null``
    is skipped exactly like a point missing ``size`` -- ``peak_mb`` is
    genuinely ``null`` in a report produced with ``--no-memory``, and that is
    "not measured", never "measured as zero".

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
            if not isinstance(pt, dict) or "size" not in pt:
                continue
            value = pt.get(field)
            if value is None:
                continue
            try:
                parsed = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            # `json.loads` accepts `Infinity`/`NaN`, so a malformed or
            # corrupted baseline can carry one. Neither can gate anything: an
            # infinite baseline makes every finite head value an
            # infinitely-large *improvement*, and every comparison against NaN
            # is False, so both read as "no regression" for any input the run
            # could possibly produce -- a point that silently cannot fail
            # (CodeRabbit, PR #1323). Dropped here, with the other malformed
            # values, so it is absent rather than un-failable; finite negative
            # values are kept, since the floors below already handle those.
            if not math.isfinite(parsed):
                continue
            out[(name, int(pt["size"]))] = parsed
    return out


def load_baseline(
    baseline_path: Path, regress_tolerance: float | None
) -> dict[tuple[str, int], float]:
    """Load baseline scaling JSON and return its (scenario, size) -> seconds mapping.

    Prints a summary line on success and a warning on failure; returns an empty
    dict when the file cannot be read or parsed. The caller (``main()``) is
    responsible for treating an empty result as a hard failure when
    ``--baseline`` was explicitly given -- this function only loads, never gates.

    *regress_tolerance* is the caller's CLI value, ``None`` when unstated, and is
    resolved here via :func:`stated_or_default` rather than at the call site: the
    call site used ``value or DEFAULT``, under which an explicit
    ``--regress-tolerance 0`` printed the 50% default while the gate itself used
    the stated 0.0.
    """
    import json

    regress_tolerance = stated_or_default(regress_tolerance, DEFAULT_REGRESS_TOLERANCE)

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


def check_memory_regressions(
    current: Sequence[_MeasuredPoint],
    scenario: str,
    baseline: dict[tuple[str, int], float],
    tolerance: float,
    *,
    floor_mb: float = DEFAULT_MEMORY_FLOOR_MB,
    min_delta_mb: float = DEFAULT_REGRESS_MIN_DELTA_MB,
) -> list[str]:
    """Return regression messages where *current* allocates more than *baseline*.

    The memory counterpart of :func:`check_regressions`, and deliberately its
    mirror image: a point regresses when its peak exceeds the baseline's by
    more than ``max(tolerance * baseline, min_delta_mb)``, computed by the
    *same* :func:`~perf_measurement.combined_regression_threshold` the timing
    rule uses. Sharing that function is the point -- two hand-rolled
    threshold rules would be free to drift, and the "relative floor OR
    absolute floor" reasoning is identical for bytes and for seconds.

    Why this exists at all: ``benchmark_scaling.py`` has long *recorded*
    ``peak_mb`` and gated it against absolute ceilings
    (``--max-memory-mb``/``--max-rss-mb``), but nothing compared it to the
    base branch. An absolute ceiling only catches a regression that crosses
    it, so a change that doubles a scenario's allocation from 200 MiB to 400
    MiB passed a 2048 MiB ceiling silently, and the gradual drift that the
    timing side already had a baseline gate for had no equivalent on the
    memory side.

    Points absent from the baseline (a scenario new in this PR) are skipped,
    so the comparison is over the intersection only -- and a point whose
    baseline is below *floor_mb* is skipped outright, since at that size the
    figure is dominated by fixture and import allocations rather than by the
    code under test.
    """
    msgs: list[str] = []
    for p in current:
        peak = p.peak_mb
        if peak is None or peak <= 0:
            continue
        base = baseline.get((scenario, p.size))
        if base is None or base < floor_mb:
            continue
        delta = peak - base
        allowed = combined_regression_threshold(base, tolerance, min_delta_mb)
        if delta > allowed:
            ratio = delta / base
            msgs.append(
                f"{scenario} @ size={p.size}: peak {peak:.1f} MiB vs baseline "
                f"{base:.1f} MiB (+{ratio * 100:.0f}%, +{delta:.1f} MiB; "
                f"allowed +{allowed:.1f} MiB = max({tolerance * 100:.0f}%, "
                f"{min_delta_mb:.1f} MiB))"
            )
    return msgs


def matched_memory_baseline_points(
    current: Sequence[_MeasuredPoint],
    scenario: str,
    baseline: dict[tuple[str, int], float],
    *,
    floor_mb: float = DEFAULT_MEMORY_FLOOR_MB,
) -> int:
    """How many of *current*'s points the memory gate could actually compare.

    The memory counterpart of :func:`matched_baseline_points`, and needed for
    the same reason: a ``--baseline`` sharing zero comparable points with this
    run is a gate that passed without checking anything, which must be a hard
    failure rather than a silent OK.
    """
    return sum(
        1
        for p in current
        if p.peak_mb is not None
        and p.peak_mb > 0
        and (base := baseline.get((scenario, p.size))) is not None
        and base >= floor_mb
    )


# ── Absolute ceiling gates ────────────────────────────────────────────────────
#
# The `--max-memory-mb` / `--max-rss-mb` budgets. They live beside the
# baseline-relative gates rather than in benchmark_scaling.py because they are
# the *other half* of the same question -- "is this run's memory acceptable?"
# -- and because keeping both halves adjacent is what makes it obvious that an
# absolute ceiling alone cannot catch a regression that stays under it, which
# is the gap check_memory_regressions() above exists to close.


def check_memory_ceiling(
    scenario: str,
    points: Sequence[_MeasuredPoint],
    max_memory_mb: float,
) -> list[str]:
    """Return a failure message if peak memory of any point exceeds *max_memory_mb*."""
    mem_points = [p for p in points if p.peak_mb is not None]
    if not mem_points:
        return []
    worst_mem = max(mem_points, key=lambda p: p.peak_mb or 0.0)
    if worst_mem.peak_mb is not None and worst_mem.peak_mb > max_memory_mb:
        return [
            f"{scenario}: peak {worst_mem.peak_mb:.1f} MiB at "
            f"size={worst_mem.size} exceeds "
            f"--max-memory-mb={max_memory_mb}"
        ]
    return []


def check_rss_ceiling(
    scenario: str,
    points: Sequence[_MeasuredPoint],
    max_rss_mb: float,
) -> list[str]:
    """Return a failure message if process peak RSS exceeds *max_rss_mb*.

    ``rss_mb`` is a monotonic process high-water mark, so the largest point's
    value is the true peak.
    """
    rss_points = [p for p in points if getattr(p, "rss_mb", None) is not None]
    if not rss_points:
        return []
    worst = max(rss_points, key=lambda p: getattr(p, "rss_mb", 0.0) or 0.0)
    worst_rss = getattr(worst, "rss_mb", None)
    if worst_rss is not None and worst_rss > max_rss_mb:
        return [
            f"{scenario}: process peak RSS {worst_rss:.1f} MiB "
            f"exceeds --max-rss-mb={max_rss_mb}"
        ]
    return []


def add_memory_gate_arguments(parser: argparse.ArgumentParser) -> None:
    """Register the memory-regression gate's CLI surface on *parser*.

    Lives here rather than in ``benchmark_scaling.py``'s own parser for the
    same reason the comparison functions do: this module owns the baseline
    gate, and its flags' help text is where the defaults are explained. It
    also keeps the whole memory gate -- flags, loading, thresholds,
    comparison -- in one reviewable place instead of split across two files.
    """
    parser.add_argument(
        "--regress-memory-tolerance",
        type=finite_nonnegative_float_arg,
        default=None,
        help=(
            "With --baseline: fail when a scenario's peak tracked heap grows by "
            "more than this fraction of the baseline's for the same (scenario, "
            "size). The allowed delta is max(this fraction x baseline, "
            "--regress-min-delta-mb). The memory counterpart of "
            f"--regress-tolerance; defaults to {DEFAULT_REGRESS_MEMORY_TOLERANCE:.2f} "
            "(tighter than the timing default, because an allocation count is "
            "far less noisy run-to-run than a wall-clock duration)."
        ),
    )
    parser.add_argument(
        "--regress-min-delta-mb",
        type=finite_nonnegative_float_arg,
        default=None,
        help=(
            "Absolute MiB floor combined with --regress-memory-tolerance via "
            "max() -- protects a small baseline from being flagged on relative "
            f"noise alone. Default {DEFAULT_REGRESS_MIN_DELTA_MB:.1f} MiB."
        ),
    )


def load_memory_baseline(
    baseline_path: Path,
    *,
    tolerance: float | None,
    track_memory: bool,
) -> dict[tuple[str, int], float]:
    """Load the peak-memory baseline from the *same* report the timing gate uses.

    A ``benchmark_scaling.py`` report already carries ``peak_mb`` on every
    point, so a caller never has to supply (or keep in sync) a second file.

    Deliberately **not** fail-closed the way the timing baseline is: a
    base-branch report produced with ``--no-memory``, or by a build predating
    this gate, legitimately carries no ``peak_mb`` anywhere, and refusing to
    run would make the memory gate impossible to introduce against any
    pre-existing baseline. The timing gate still fails closed on a wholly
    empty or malformed baseline, so that case is caught there rather than
    passing silently here. Returns an empty mapping (an inactive gate, with
    the reason printed) in every such case.
    """
    import json

    if not track_memory:
        print(
            "\nNOTE: --no-memory was given, so the baseline memory-regression "
            "gate is inactive for this run."
        )
        return {}
    try:
        points = baseline_points_from_report(
            json.loads(baseline_path.read_text()), field="peak_mb"
        )
    except (OSError, ValueError) as e:
        print(f"WARNING: could not load memory baseline {baseline_path}: {e}")
        return {}
    if not points:
        print(
            f"NOTE: baseline {baseline_path} carries no peak_mb points "
            "(produced with --no-memory, or by a build predating the "
            "memory-regression gate) -- memory gate inactive this run."
        )
        return points
    effective = stated_or_default(tolerance, DEFAULT_REGRESS_MEMORY_TOLERANCE)
    print(
        f"Comparing peak memory against baseline {baseline_path} "
        f"({len(points)} points, tolerance {effective * 100:.0f}%)"
    )
    return points


def _memory_gate_messages(
    current: Sequence[_MeasuredPoint],
    scenario: str,
    baseline: dict[tuple[str, int], float] | None,
    cli_tolerance: float | None,
    cli_min_delta_mb: float | None,
    record_into: dict[str, object] | None,
) -> list[str]:
    """The memory half of :func:`apply_regression_gate`; inactive when empty."""
    if not baseline:
        return []
    return apply_memory_regression_gate(
        current,
        scenario,
        baseline,
        cli_tolerance=cli_tolerance,
        cli_min_delta_mb=cli_min_delta_mb,
        record_into=record_into,
    )


def apply_memory_regression_gate(
    current: Sequence[_MeasuredPoint],
    scenario: str,
    baseline: dict[tuple[str, int], float],
    *,
    cli_tolerance: float | None,
    cli_min_delta_mb: float | None,
    record_into: dict[str, object] | None = None,
) -> list[str]:
    """Run the memory gate for one scenario and record what it compared.

    The memory counterpart of :func:`apply_regression_gate`, and it records
    into the report for the same reason that one does: a reader must be able
    to see which threshold produced the number that gated them, and how many
    points were actually comparable -- a gate that compared nothing and a gate
    that found nothing are indistinguishable from a bare "OK".
    """
    tolerance = stated_or_default(cli_tolerance, DEFAULT_REGRESS_MEMORY_TOLERANCE)
    min_delta_mb = stated_or_default(cli_min_delta_mb, DEFAULT_REGRESS_MIN_DELTA_MB)
    msgs = check_memory_regressions(
        current, scenario, baseline, tolerance, min_delta_mb=min_delta_mb
    )
    if record_into is not None:
        record_into["memory_regression"] = {
            "compared_points": matched_memory_baseline_points(
                current, scenario, baseline
            ),
            "tolerance": tolerance,
            "min_delta_mb": min_delta_mb,
            "floor_mb": DEFAULT_MEMORY_FLOOR_MB,
            "regressions": msgs,
        }
    return msgs


def total_memory_points_compared(report: Mapping[str, Any]) -> int:
    """How many points the memory gate actually compared, across all scenarios.

    Read back from the report that :func:`apply_memory_regression_gate` already
    writes, rather than threaded through every caller's return value, so the
    number a reader sees in the JSON and the number the gate is judged on are
    the same one by construction.

    Its purpose is the check the timing side has always had: a ``--baseline``
    that shares zero comparable points with what was measured is a gate that
    ran and compared *nothing*, which must fail rather than report OK. Without
    it the memory gate could pass unconditionally whenever the two sides'
    scenario sets or sizes stopped lining up -- exactly the silent no-op the
    timing gate's own zero-overlap check exists to prevent (CodeRabbit,
    PR #1323).
    """
    total = 0
    scenarios = report.get("scenarios", {})
    if not isinstance(scenarios, Mapping):
        return 0
    for body in scenarios.values():
        if not isinstance(body, Mapping):
            continue
        memory = body.get("memory_regression")
        if isinstance(memory, Mapping):
            compared = memory.get("compared_points")
            if isinstance(compared, int) and not isinstance(compared, bool):
                total += compared
    return total


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
    memory_baseline: dict[tuple[str, int], float] | None = None,
    cli_memory_tolerance: float | None = None,
    cli_min_delta_mb: float | None = None,
) -> list[str]:
    """Resolve *scenario*'s effective threshold, record it, and gate on it.

    Gates both axes. *memory_baseline* (empty or ``None`` when the memory gate
    is inactive -- see :func:`load_memory_baseline`) adds the peak-memory
    comparison to the same call, so a caller cannot wire up the timing gate
    and forget the memory one, or run them against two different scenario
    loops. The two axes keep separate thresholds and separate report entries;
    only the invocation is shared.

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
    messages = list(
        _memory_gate_messages(
            current,
            scenario,
            memory_baseline,
            cli_memory_tolerance,
            cli_min_delta_mb,
            record_into,
        )
    )
    messages.extend(
        check_regressions(
            current,
            scenario,
            baseline,
            threshold.tolerance,
            floor_seconds=floor_seconds,
            min_delta_seconds=threshold.min_delta,
        )
    )
    return messages


def stated_or_default(value: float | None, default: float) -> float:
    """*value* unless it was never stated, in which case *default*.

    Exists because ``value or default`` is wrong for a threshold: ``0.0`` is
    falsy, so an explicit ``--regress-tolerance 0`` silently became the 0.5
    default in the one place that *printed* the tolerance, while the code that
    actually gated the run used the stated ``0.0``. The printed number then
    contradicted the number that judged the run.
    """
    return default if value is None else value
