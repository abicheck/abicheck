#!/usr/bin/env python3
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

"""Full-CLI L2 *scaling* gate: how cost grows with headers and with libraries.

``check_l2_cli_perf.py`` gates one small fixture against the base branch, which
catches a constant-factor regression but not a change in *shape*: a per-library
step that starts re-walking every other library's headers costs almost nothing
at one library and dominates at twenty. This harness sweeps two axes through the
real CLI, on real compiled fixtures, and gates the growth rate on each:

* ``headers`` -- one library, ``compare --depth headers`` with N public
  headers (the single-library L2 path);
* ``libraries`` -- a directory ``compare`` of N libraries (the release fan-out,
  "one public contract, many providers"), which is the multi-library path a
  real product release takes.

**The gated number is the marginal exponent, not the raw one.** Every CLI run
pays a fixed floor (interpreter start, imports, config discovery, report
writing) of ~1 s that dwarfs the per-unit cost at these small sizes, so a raw
log-log fit of wall time sits near zero and passes whatever the product does.
The sweep therefore always measures ``n = 1`` as the floor and fits
``log(wall(n) - wall(1))`` against ``log(n - 1)`` over the remaining points: the
slope of the work the axis *adds*. A linear step reads ~1.0, a step that
re-does all previous units' work reads ~2.0.

Correctness is validated outside the timed window, exactly like the parent
harness: a run that got fast by falling back to binary-only evidence, or by
comparing fewer libraries than it was handed, is a failure, not a fast point.
Each repetition runs with its own empty ``XDG_CACHE_HOME`` (cold application
cache) and records peak concurrent process-tree RSS, which is gated against an
absolute ceiling.

Usage::

    python scripts/check_l2_scaling_perf.py --require-toolchain \\
        --json-out reports/perf/l2_scaling.json --markdown

Self-skips (exit 0) without a C++ compiler or off Linux; ``--require-toolchain``
turns that into a failure for a CI job that installed one.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import statistics
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import l2_cli_fixture as fixtures  # noqa: E402
from l2_cli_argv import _cli, _header_args  # noqa: E402
from l2_cli_validation import (  # noqa: E402
    _validate_break_findings,
    _validate_l2_reached,
)
from perf_measurement import finite_positive_float_arg, positive_int_arg  # noqa: E402
from perf_receipt import run_measured  # noqa: E402

#: Sizes per axis. ``1`` is always the floor point; the rest must not be evenly
#: spaced in log(n - 1), or an OLS fit over three points collapses onto its two
#: endpoints (see ``tests/_perf_scaling.py``).
DEFAULT_SIZES: dict[str, tuple[int, ...]] = {
    "headers": (1, 4, 12, 30),
    "libraries": (1, 3, 6, 10),
}
#: Headers per library on the library axis: more than one, so each member has a
#: real public surface to reconcile against the others.
LIBRARY_AXIS_HEADERS = 2
DEFAULT_REPEAT = 3
DEFAULT_TIMEOUT_SECONDS = 600.0
#: Default marginal-exponent budgets, per axis. The header axis is linear
#: (~1.0). The library axis is still super-linear: every member of a release
#: is dumped against the whole release's union header set, so per-member work
#: grows with the member count. It measures ~1.4 over these sizes (it was
#: ~1.64 before contract path resolution and the C++20 scan were memoized).
#: The budget catches it getting worse. Lower it toward ~1.1 when members stop
#: receiving the union set (docs/contribute/known-gaps.md, "Multi-library L2
#: compare scales quadratically with library count").
DEFAULT_MAX_EXPONENT: dict[str, float] = {"headers": 1.4, "libraries": 1.7}
DEFAULT_MAX_RSS_MB = 1024.0
#: Below this marginal cost (seconds) the largest point is indistinguishable from
#: the floor, and any exponent fitted through it is noise.
MIN_MARGINAL_SECONDS = 0.25
#: A point whose margin over the floor is positive but below this is left out
#: of the fit as unresolved. Its log is dominated by timing noise: margins of
#: 0.01 s, 0.10 s and 2.0 s at 3/6/10 libraries fit an exponent of ~3.4 purely
#: through the first point. A point at or *below* the floor is different: it
#: stays in (clamped), because a small n becoming faster than n=1 is itself a
#: signal the fit must not drop.
NOISE_MARGIN_SECONDS = 0.05


@dataclass
class Point:
    axis: str
    n: int
    walls: list[float] = field(default_factory=list)
    peak_rss_mb: float | None = None
    problems: list[str] = field(default_factory=list)

    @property
    def median_wall(self) -> float:
        return statistics.median(self.walls) if self.walls else math.nan


# ── fixtures and argv ─────────────────────────────────────────────────────────
def _spec(axis: str, n: int) -> fixtures.FixtureSpec:
    if axis == "headers":
        return fixtures.FixtureSpec(shape="simple", headers=n, libraries=1)
    return fixtures.FixtureSpec(
        shape="simple", headers=LIBRARY_AXIS_HEADERS, libraries=n
    )


def _release_dirs(fixture: fixtures.BuiltFixture, root: Path) -> tuple[Path, Path]:
    """Each side's libraries gathered into one directory, as a release ships."""
    out = []
    for side, libs in (("old", fixture.old), ("new", fixture.new)):
        directory = root / f"{side}_release"
        directory.mkdir(parents=True, exist_ok=True)
        for lib in libs:
            shutil.copy2(lib.so, directory / lib.so.name)
        out.append(directory)
    return out[0], out[1]


def build_argv(axis: str, fixture: fixtures.BuiltFixture, work: Path) -> list[str]:
    report = work / "report.json"
    if axis == "headers":
        old, new = str(fixture.old[0].so), str(fixture.new[0].so)
    else:
        old_dir, new_dir = _release_dirs(fixture, work)
        old, new = str(old_dir), str(new_dir)
    return _cli(
        "compare",
        old,
        new,
        "--depth",
        "headers",
        "-o",
        f"json={report}",
        *_header_args(fixture.old, "old"),
        *_header_args(fixture.new, "new"),
    )


# ── validation (outside the timed window) ─────────────────────────────────────
def validate_release_report(report: dict[str, Any], n_libraries: int) -> list[str]:
    """A directory compare really compared every library, at L2, completely."""
    problems: list[str] = []
    libraries = report.get("libraries") or []
    if len(libraries) != n_libraries:
        problems.append(f"compared {len(libraries)} libraries, expected {n_libraries}")
    for key in ("unmatched_old", "unmatched_new"):
        if report.get(key):
            problems.append(f"{key}={report[key]!r}, expected none")
    scope = (report.get("run_outcome") or {}).get("scope")
    if scope != "complete":
        problems.append(f"run_outcome.scope={scope!r}, expected 'complete'")
    if report.get("verdict") != "BREAKING":
        problems.append(f"verdict={report.get('verdict')!r}, expected BREAKING")
    for entry in libraries:
        name = entry.get("library", "?")
        if entry.get("verdict") != "BREAKING" or not entry.get("breaking"):
            problems.append(f"{name}: verdict={entry.get('verdict')!r}, no break found")
        if entry.get("analysis_assurance_status") != "complete":
            problems.append(
                f"{name}: assurance={entry.get('analysis_assurance_status')!r}"
            )
        surface = entry.get("surface_scope") or {}
        # Public-header scoping only resolves when the header AST was parsed, so
        # this is what distinguishes a real L2 member from a binary fallback.
        if not (surface.get("enabled") and entry.get("scope_resolved")):
            problems.append(f"{name}: public header scope did not resolve")
    return problems


def validate(axis: str, n: int, report_path: Path) -> list[str]:
    if not report_path.is_file():
        return ["no JSON report was written"]
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if axis == "headers":
        return _validate_l2_reached(
            report, sides=("old", "new")
        ) + _validate_break_findings(report)
    return validate_release_report(report, n)


# ── measurement ───────────────────────────────────────────────────────────────
def measure_point(
    axis: str, n: int, root: Path, *, repeat: int, timeout: float
) -> Point:
    point = Point(axis=axis, n=n)
    work = root / f"{axis}-{n}"
    fixture = fixtures.build(_spec(axis, n), work / "fixture")
    argv = build_argv(axis, fixture, work)
    rss: list[float] = []
    for index in range(repeat):
        cache = work / f"cache-{index}"
        cache.mkdir(parents=True, exist_ok=True)
        (work / "report.json").unlink(missing_ok=True)
        run = run_measured(
            argv,
            cwd=work,
            env={**os.environ, "XDG_CACHE_HOME": str(cache)},
            timeout=timeout,
            sample_rss=True,
        )
        if run.timed_out or run.exit_code != 4:
            point.problems.append(
                f"run {index}: exit={run.exit_code} timed_out={run.timed_out} "
                f"(expected exit 4); stderr tail: {run.stderr[-400:]!r}"
            )
            return point
        point.walls.append(run.wall_seconds)
        if run.rss is not None and run.rss.sampled_peak_tree_bytes:
            rss.append(run.rss.sampled_peak_tree_bytes / (1024 * 1024))
        # Validate every repetition: a fallback on one run is still a fallback.
        point.problems += [
            f"run {index}: {p}" for p in validate(axis, n, work / "report.json")
        ]
        shutil.rmtree(cache, ignore_errors=True)
    point.peak_rss_mb = max(rss) if rss else None
    return point


def marginal_exponent(points: list[Point]) -> tuple[float | None, str | None]:
    """OLS slope of log(wall(n) - wall(1)) against log(n - 1).

    Returns ``(exponent, None)`` or ``(None, reason)`` when the sweep cannot
    support a fit -- which the gate treats as a failure, never as a pass.
    """
    by_n = {p.n: p.median_wall for p in points}
    if 1 not in by_n:
        return None, "no n=1 floor point was measured"
    floor = by_n[1]
    rest = sorted((n, wall) for n, wall in by_n.items() if n > 1)
    if len(rest) < 2:
        return None, "need at least two points above the floor"
    largest_margin = rest[-1][1] - floor
    if largest_margin < MIN_MARGINAL_SECONDS:
        return None, (
            f"largest point is only {largest_margin:.3f}s above the floor "
            f"(< {MIN_MARGINAL_SECONDS}s): the axis adds no measurable work, so "
            "any fitted exponent would be noise -- enlarge the sweep"
        )
    xs, ys = [], []
    for n, wall in rest:
        margin = wall - floor
        if 0 < margin < NOISE_MARGIN_SECONDS:
            continue  # unresolved: see NOISE_MARGIN_SECONDS
        # A point at or under the floor carries no marginal signal; clamp it to
        # a tiny positive margin instead of dropping it, so a regression that
        # makes small n *faster* than n=1 cannot shrink the fit to two points.
        xs.append(math.log(n - 1))
        ys.append(math.log(max(margin, 1e-3)))
    if len(xs) < 2:
        return None, (
            f"fewer than two points are resolvably above the floor (a margin "
            f"under {NOISE_MARGIN_SECONDS}s is timing noise) -- enlarge the sweep"
        )
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    denom = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / denom
    return slope, None


def gate(
    axis: str, points: list[Point], *, max_exponent: float, max_rss_mb: float
) -> tuple[float | None, list[str]]:
    failures = [f"{axis} n={p.n}: {msg}" for p in points for msg in p.problems]
    if failures:
        return None, failures
    exponent, reason = marginal_exponent(points)
    if exponent is None:
        failures.append(f"{axis}: cannot fit a marginal exponent: {reason}")
    elif exponent > max_exponent:
        failures.append(
            f"{axis}: marginal cost exponent {exponent:.2f} exceeds budget "
            f"{max_exponent:.2f} -- the per-unit work grows with the number of "
            "units (see docs/contribute/performance.md, 'L2 scaling gate')"
        )
    for p in points:
        if p.peak_rss_mb is not None and p.peak_rss_mb > max_rss_mb:
            failures.append(
                f"{axis} n={p.n}: peak process-tree RSS {p.peak_rss_mb:.0f} MB "
                f"exceeds {max_rss_mb:.0f} MB"
            )
    return exponent, failures


# ── CLI ───────────────────────────────────────────────────────────────────────
def _sizes_arg(value: str) -> tuple[int, ...]:
    sizes = tuple(sorted({positive_int_arg(v) for v in value.split(",")}))
    if sizes[0] != 1 or len(sizes) < 3:
        raise argparse.ArgumentTypeError(
            "sizes must include 1 (the floor) and at least two larger values"
        )
    return sizes


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument(
        "--axis",
        choices=sorted(DEFAULT_SIZES),
        action="append",
        help="Axis to sweep (repeatable; default: all).",
    )
    p.add_argument(
        "--header-sizes",
        type=_sizes_arg,
        default=DEFAULT_SIZES["headers"],
        help="Comma-separated header counts, must include 1.",
    )
    p.add_argument(
        "--library-sizes",
        type=_sizes_arg,
        default=DEFAULT_SIZES["libraries"],
        help="Comma-separated library counts, must include 1.",
    )
    p.add_argument(
        "--repeat",
        type=positive_int_arg,
        default=DEFAULT_REPEAT,
        help="Timed repetitions per point (median is used).",
    )
    p.add_argument(
        "--max-exponent-headers",
        type=finite_positive_float_arg,
        default=DEFAULT_MAX_EXPONENT["headers"],
        help="Marginal-exponent budget for the header axis.",
    )
    p.add_argument(
        "--max-exponent-libraries",
        type=finite_positive_float_arg,
        default=DEFAULT_MAX_EXPONENT["libraries"],
        help="Marginal-exponent budget for the library axis.",
    )
    p.add_argument(
        "--max-rss-mb",
        type=finite_positive_float_arg,
        default=DEFAULT_MAX_RSS_MB,
        help="Ceiling on any point's peak process-tree RSS (MB).",
    )
    p.add_argument(
        "--timeout",
        type=finite_positive_float_arg,
        default=DEFAULT_TIMEOUT_SECONDS,
        help="Per-run timeout in seconds.",
    )
    p.add_argument("--json-out", type=Path, help="Write a JSON receipt here.")
    p.add_argument(
        "--markdown",
        action="store_true",
        help="Print the table as Markdown (for a job summary).",
    )
    p.add_argument(
        "--require-toolchain",
        action="store_true",
        help="Fail instead of skipping when g++ is unavailable.",
    )
    return p.parse_args(argv)


def _print(results: dict[str, Any], *, markdown: bool) -> None:
    sep = " | " if markdown else "  "
    header = ["axis", "n", "median s", "peak RSS MB", "exponent", "budget"]
    if markdown:
        print("| " + sep.join(header) + " |")
        print("|" + "---|" * len(header))
    else:
        print(sep.join(f"{h:>12}" for h in header))
    for axis, data in results["axes"].items():
        exp = data["marginal_exponent"]
        for p in data["points"]:
            median = statistics.median(p["walls"]) if p["walls"] else math.nan
            row = [
                axis,
                str(p["n"]),
                f"{median:.3f}",
                f"{p['peak_rss_mb']:.0f}" if p["peak_rss_mb"] else "n/a",
                f"{exp:.2f}" if exp is not None else "n/a",
                f"{data['max_exponent']:.2f}",
            ]
            print(
                ("| " + sep.join(row) + " |")
                if markdown
                else sep.join(f"{c:>12}" for c in row)
            )
    for failure in results["failures"]:
        print(f"FAIL: {failure}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if sys.platform != "linux" or not fixtures.compiler_available():
        msg = "L2 scaling gate needs Linux and g++"
        if args.require_toolchain:
            print(f"ERROR: {msg} (--require-toolchain)", file=sys.stderr)
            return 1
        print(f"SKIP: {msg}")
        return 0
    axes = args.axis or sorted(DEFAULT_SIZES)
    sizes = {"headers": args.header_sizes, "libraries": args.library_sizes}
    budgets = {
        "headers": args.max_exponent_headers,
        "libraries": args.max_exponent_libraries,
    }
    results: dict[str, Any] = {"schema": 1, "axes": {}, "failures": []}
    with tempfile.TemporaryDirectory(prefix="abicheck-l2-scaling-") as tmp:
        for axis in axes:
            points = [
                measure_point(
                    axis, n, Path(tmp), repeat=args.repeat, timeout=args.timeout
                )
                for n in sizes[axis]
            ]
            exponent, failures = gate(
                axis, points, max_exponent=budgets[axis], max_rss_mb=args.max_rss_mb
            )
            results["axes"][axis] = {
                "points": [asdict(p) for p in points],
                "marginal_exponent": exponent,
                "max_exponent": budgets[axis],
                "max_rss_mb": args.max_rss_mb,
            }
            results["failures"] += failures
    _print(results, markdown=args.markdown)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    return 1 if results["failures"] else 0


if __name__ == "__main__":
    sys.exit(main())
