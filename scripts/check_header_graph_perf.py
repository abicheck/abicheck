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

"""Header-only (L2) semantic graph performance gate (G31 Phase D).

G31 Phase A made the header-only graph (``abicheck/buildsource/header_graph.py``)
unconditional: every ``dump``/``compare`` that parses headers now pays its
attach cost on every run, not just when a user explicitly opted in via the
now-retired ``--header-graph`` flag. G31 Phase C's in-process AST memoization
(``dumper_cache.py``) closed the worst case of that cost (a second, redundant
``clang -ast-dump=json`` subprocess when the main snapshot pass already used
``--ast-frontend clang``) but the cost is not zero: the default ``castxml``
backend never populates that memo, so ``service._attach_header_graph`` still
pays a genuine second ``clang`` invocation on every dump, on top of whatever
memoized/graph-construction overhead remains on the clang-frontend path.

This script isolates and measures that attach cost directly, sweeping a
synthetic header-size axis, and gates on regression against a documented
baseline — the same discipline ``check_fp_rate.py``/``check_tier_accuracy.py``
apply to correctness and ``benchmark_scaling.py`` applies to comparison
scaling.

Method: build one tiny real ELF ``.so`` + a synthetic public header of size
*N* declarations (reused across every repeat — the binary/header pair is
fixed per size, only the dump/attach calls are repeated), then time, per
repeat:

* ``baseline_ms`` — ``dumper.dump(so, [header], header_backend="clang")``,
  which does **not** itself attach the header graph (see
  ``abicheck/buildsource/CLAUDE.md``'s ``header_graph.py`` row: the attach
  step lives in ``service.py``, not ``dumper.py``).
* ``attach_ms`` — ``service._attach_header_graph(snap, True, False, ...)``
  applied to the snapshot ``baseline_ms`` already produced, in isolation.

``attach_ms`` is the number this gate cares about: the marginal, always-on
cost G31 Phase A introduced. Requires ``clang``/``clang++`` and ``g++`` on
``PATH`` (Linux/ELF only, matching
``tests/test_clang_header_backend_integration.py``'s own scope); self-skips
(exit 0) when unavailable so this never blocks a host without a C++
toolchain.

Usage::

    # Measure and print a table (report-only without --baseline):
    python scripts/check_header_graph_perf.py

    # Establish/refresh the committed baseline:
    python scripts/check_header_graph_perf.py --json-out reports/perf/header_graph.json

    # Gate a PR run against the committed baseline:
    python scripts/check_header_graph_perf.py --baseline reports/perf/header_graph.json \\
        --regress-tolerance 0.5
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Unlike ``check_mutation_score.py``'s ``SURVIVOR_BASELINE`` (a single
# in-module constant), the baseline here is a per-size JSON report on disk
# (``--json-out``/``--baseline``) since it's a curve, not a scalar. A run
# with no ``--baseline`` is report-only — the same "not yet established"
# convention, just file-shaped instead of a module constant. See the module
# docstring's Usage section for how to establish and then gate against one.
DEFAULT_SIZES: tuple[int, ...] = (25, 100, 400)
DEFAULT_REPEAT = 3
DEFAULT_REGRESS_TOLERANCE = 0.5  # attach_ms may grow at most 50% vs. baseline


def _have(tool: str) -> bool:
    return shutil.which(tool) is not None


def _synthesize_header(n: int) -> str:
    """A synthetic public header with *n* structs and *n* free functions.

    Deliberately simple, self-contained declarations (no templates, no
    inheritance) — the point is to scale the *count* of declarations the
    header-graph attach step has to walk, not to exercise any one parsing
    edge case (those are covered by the correctness test suite instead).
    """
    lines = ["#pragma once", "namespace hgperf {", ""]
    for i in range(n):
        lines.append(f"struct S{i} {{ int a; double b; S{i}* next; }};")
    for i in range(n):
        lines.append(f"int fn{i}(const S{i}& in, S{i}* out);")
    lines.append("")
    lines.append("}  // namespace hgperf")
    return "\n".join(lines) + "\n"


def _synthesize_source(n: int) -> str:
    lines = ['#include "api.h"', "namespace hgperf {", ""]
    for i in range(n):
        lines.append(
            f"int fn{i}(const S{i}& in, S{i}* out) {{ *out = in; return in.a; }}"
        )
    lines.append("")
    lines.append("}  // namespace hgperf")
    return "\n".join(lines) + "\n"


def _build_fixture(tmp_dir: Path, n: int) -> tuple[Path, Path]:
    """Compile a real ELF ``.so`` + write its header for size *n*. Returns
    ``(so_path, header_path)``."""
    header = tmp_dir / "api.h"
    header.write_text(_synthesize_header(n))
    src = tmp_dir / "api.cpp"
    src.write_text(_synthesize_source(n))
    so = tmp_dir / "libhgperf.so"
    subprocess.run(
        ["g++", "-shared", "-fPIC", "-O0", "-o", str(so), str(src), f"-I{tmp_dir}"],
        check=True,
        capture_output=True,
    )
    return so, header


def _measure_size(n: int, repeat: int) -> dict[str, Any]:
    from abicheck.compile_context import CompileContext
    from abicheck.dumper import dump
    from abicheck.service import _attach_header_graph

    with tempfile.TemporaryDirectory(prefix="hgperf_") as tmp:
        tmp_dir = Path(tmp)
        so, header = _build_fixture(tmp_dir, n)

        baseline_samples: list[float] = []
        attach_samples: list[float] = []
        for _ in range(repeat):
            t0 = time.perf_counter()
            snap = dump(so, [header], header_backend="clang")
            t1 = time.perf_counter()
            baseline_samples.append((t1 - t0) * 1000.0)

            t2 = time.perf_counter()
            _attach_header_graph(
                snap,
                True,
                False,
                [header],
                [],
                "c++",
                CompileContext(),
                None,
                None,
            )
            t3 = time.perf_counter()
            attach_samples.append((t3 - t2) * 1000.0)

        return {
            "size": n,
            "baseline_ms": min(baseline_samples),
            "attach_ms": min(attach_samples),
            "baseline_ms_samples": baseline_samples,
            "attach_ms_samples": attach_samples,
        }


def measure(sizes: tuple[int, ...], repeat: int) -> list[dict[str, Any]]:
    return [_measure_size(n, repeat) for n in sizes]


def _load_baseline(path: Path) -> dict[int, float]:
    data = json.loads(path.read_text())
    points = data if isinstance(data, list) else data.get("points", [])
    return {int(p["size"]): float(p["attach_ms"]) for p in points}


def check_regressions(
    points: list[dict[str, Any]],
    baseline: dict[int, float],
    tolerance: float,
) -> list[str]:
    """Return one message per size that regressed beyond *tolerance*."""
    failures = []
    for p in points:
        base = baseline.get(int(p["size"]))
        if base is None or base <= 0:
            continue
        current = float(p["attach_ms"])
        allowed = base * (1.0 + tolerance)
        if current > allowed:
            pct = (current / base - 1.0) * 100.0
            failures.append(
                f"size={p['size']}: attach_ms {current:.1f} > baseline {base:.1f} "
                f"x{1 + tolerance:.2f} ({pct:+.0f}%)"
            )
    return failures


def _print_table(points: list[dict[str, Any]]) -> None:
    print(f"{'size':>8} {'baseline_ms':>12} {'attach_ms':>12} {'attach_%':>10}")
    for p in points:
        baseline_ms = p["baseline_ms"]
        attach_ms = p["attach_ms"]
        pct = (attach_ms / baseline_ms * 100.0) if baseline_ms else float("nan")
        print(f"{p['size']:>8} {baseline_ms:>12.1f} {attach_ms:>12.1f} {pct:>9.1f}%")


def _print_markdown(points: list[dict[str, Any]]) -> None:
    print("| size | baseline_ms | attach_ms | attach_% |")
    print("|---:|---:|---:|---:|")
    for p in points:
        baseline_ms = p["baseline_ms"]
        attach_ms = p["attach_ms"]
        pct = (attach_ms / baseline_ms * 100.0) if baseline_ms else float("nan")
        print(f"| {p['size']} | {baseline_ms:.1f} | {attach_ms:.1f} | {pct:.1f}% |")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--sizes",
        type=int,
        nargs="+",
        default=list(DEFAULT_SIZES),
        help="Declaration counts to sweep (default: %(default)s)",
    )
    p.add_argument(
        "--repeat",
        type=int,
        default=DEFAULT_REPEAT,
        help="Repeats per size; the minimum is reported (default: %(default)s)",
    )
    p.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="Path to a previously written --json-out report to gate against",
    )
    p.add_argument(
        "--regress-tolerance",
        type=float,
        default=DEFAULT_REGRESS_TOLERANCE,
        help="Fractional attach_ms growth allowed vs. baseline before failing "
        "(default: %(default)s = 50%%)",
    )
    p.add_argument("--json-out", type=Path, default=None, help="Write a JSON report")
    p.add_argument(
        "--markdown",
        action="store_true",
        help="Print a Markdown table instead of a plain one",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not (_have("clang") and _have("clang++") and _have("g++")):
        print(
            "SKIP: clang/clang++/g++ not all found on PATH — nothing to measure "
            "(the header-only graph attach step needs a real clang install)."
        )
        return 0
    if not sys.platform.startswith("linux"):
        print(
            "SKIP: header-graph perf gate is Linux/ELF-scoped (see module docstring)."
        )
        return 0

    points = measure(tuple(args.sizes), args.repeat)

    if args.markdown:
        _print_markdown(points)
    else:
        _print_table(points)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps({"points": points}, indent=2) + "\n")
        print(f"\nWrote {args.json_out}")

    if args.baseline is None:
        print(
            "\nNo --baseline given: report-only run. Pass a previously written "
            "--json-out report via --baseline to gate future runs against it."
        )
        return 0

    baseline = _load_baseline(args.baseline)
    failures = check_regressions(points, baseline, args.regress_tolerance)
    if failures:
        print("\nFAIL: header-graph attach-cost regression:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nOK: no header-graph attach-cost regression vs. baseline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
