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

"""Empirical complexity exponents of abicheck's hot paths.

Each case runs one real operation at doubling input sizes ``n``, measures
the process **CPU time** of the operation alone (inputs are built outside
the timed region), keeps the **fastest** of a few samples per size, and fits
the log-log slope ``k`` of time against ``n`` by least squares. ``k ~ 1`` is
linear, ``k ~ 2`` quadratic. Every case declares a ``max_exponent``; with
``--strict`` a fitted slope above it fails the run.

Why this exists beside the call-count gates
(``tests/test_compare_call_complexity.py``): a call count sees a function
called too often, not a function whose *body* turned quadratic (a list scan
inside a single call, a sort per element of a C-level loop). A fitted time
exponent sees both. Why CPU time and the fastest sample: wall time on a
shared CI runner includes other tenants; the minimum of several CPU-time
samples is the most repeatable estimate of the work itself.

Each case runs in **its own subprocess**, so process-wide caches warmed by
one case (the demangler's, canonical-spelling memos) cannot flatten another
case's curve; every sample also builds freshly-salted names for the same
reason.

Usage::

    python scripts/complexity_bench.py                 # report every case
    python scripts/complexity_bench.py --strict        # fail on k > max_exponent
    python scripts/complexity_bench.py --case compare_by_types --json out.json
"""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Case:
    """One measured hot path."""

    name: str
    description: str
    sizes: tuple[int, ...]
    max_exponent: float
    samples: int = 3


#: Ceilings sit above the measured slope with room for runner noise
#: (fastest-of-N CPU time still moves by ~10% on shared runners), and
#: below quadratic -- the shape this gate exists to catch. A case whose
#: intended algorithm is linear gets 1.5; never raise a ceiling to make a
#: superlinear regression pass -- fix the algorithm.
CASES: dict[str, Case] = {
    c.name: c
    for c in (
        Case(
            "compare_by_symbols",
            "compare() of two snapshots with n changed function signatures",
            (100, 200, 400, 800),
            1.5,
        ),
        Case(
            "compare_by_types",
            "compare() of two snapshots with n changed record types",
            (50, 100, 200, 400),
            1.5,
        ),
        Case(
            "compare_add_remove",
            "compare() where n functions are removed and n added (old/new matching)",
            (100, 200, 400, 800),
            1.5,
        ),
        Case(
            "rename_matching",
            "compare() of n renamed functions (rename pairing / matching)",
            (100, 200, 400, 800),
            1.5,
        ),
        Case(
            "policy_classification",
            "compute_verdict() + effective_category() over n findings",
            (2000, 4000, 8000, 16000),
            1.4,
            samples=5,
        ),
        Case(
            "history_by_releases",
            "build_longitudinal_history() over n accumulating releases",
            (5, 10, 20, 40),
            1.5,
        ),
    )
}


# -- case bodies (run inside the worker subprocess) ------------------------


def _workload(name: str) -> Callable[[int, str], tuple[object, object]]:
    sys.path.insert(0, str(REPO / "tests"))
    from _compare_workloads import WORKLOADS

    return WORKLOADS[name]


def _compare_case(workload: str) -> Callable[[int, str], Callable[[], object]]:
    def prepare(n: int, tag: str) -> Callable[[], object]:
        from abicheck.checker import compare

        old, new = _workload(workload)(n, tag)
        return lambda: compare(old, new)

    return prepare


def _policy_prepare(n: int, tag: str) -> Callable[[], object]:
    from abicheck.checker_policy import ChangeKind
    from abicheck.checker_types import Change
    from abicheck.policy.classification import (
        compute_verdict,
        effective_category,
        policy_kind_sets,
    )

    kinds = sorted(ChangeKind, key=lambda k: k.value)
    changes = [
        Change(kind=kinds[i % len(kinds)], symbol=f"{tag}s{i}", description="d")
        for i in range(n)
    ]
    # Resolved once, outside the timed region: the bench measures the
    # per-change classification, not the (cached) policy lookup.
    sets = policy_kind_sets("strict_abi")

    def run() -> object:
        for c in changes:
            effective_category(c, *sets)
        return compute_verdict(changes)

    return run


def _history_prepare(n: int, tag: str) -> Callable[[], object]:
    from abicheck.model import AbiSnapshot, Function, Visibility
    from abicheck.workflows.history import HistoryEntry, build_longitudinal_history

    core = 40

    def fn(name: str, deprecated: bool = False) -> Function:
        return Function(
            name=name,
            mangled=f"_Z{len(name)}{name}v",
            return_type="int",
            visibility=Visibility.PUBLIC,
            deprecated="superseded" if deprecated else None,
        )

    entries = []
    for r in range(n):
        funcs = [fn(f"{tag}core_{i}", deprecated=(i == r % core)) for i in range(core)]
        funcs += [
            fn(f"{tag}add_{a}_{j}")
            for a in range(max(0, r - 2), r + 1)
            for j in range(5)
        ]
        snap = AbiSnapshot(library="libhist.so", version=f"1.{r}.0", functions=funcs)
        entries.append(
            HistoryEntry(index=r, version=f"1.{r}.0", path=f"v{r}.json", snapshot=snap)
        )
    return lambda: build_longitudinal_history(entries)


PREPARERS: dict[str, Callable[[int, str], Callable[[], object]]] = {
    "compare_by_symbols": _compare_case("signature_churn"),
    "compare_by_types": _compare_case("type_churn"),
    "compare_add_remove": _compare_case("add_remove"),
    "rename_matching": _compare_case("rename_churn"),
    "policy_classification": _policy_prepare,
    "history_by_releases": _history_prepare,
}


def measure_in_process(case: Case) -> dict[int, float]:
    """Fastest CPU-time sample per size. Run in a fresh process."""
    prepare = PREPARERS[case.name]
    # One untimed warm-up at the smallest size: imports and one-time
    # registry builds are not part of the operation's growth.
    prepare(case.sizes[0], "warm_")()
    out: dict[int, float] = {}
    for n in case.sizes:
        best = math.inf
        for s in range(case.samples):
            thunk = prepare(n, f"cb{n}_{s}_")
            start = time.process_time()
            thunk()
            best = min(best, time.process_time() - start)
        out[n] = best
    return out


# -- fitting and reporting (parent process) --------------------------------


def fit_exponent(points: dict[int, float]) -> float:
    """Least-squares slope of log(time) against log(n)."""
    pts = [(math.log(n), math.log(max(t, 1e-6))) for n, t in sorted(points.items())]
    if len(pts) < 2:
        raise ValueError("need at least two sizes to fit an exponent")
    mx = sum(x for x, _ in pts) / len(pts)
    my = sum(y for _, y in pts) / len(pts)
    den = sum((x - mx) ** 2 for x, _ in pts)
    return sum((x - mx) * (y - my) for x, y in pts) / den


@dataclass(frozen=True)
class Result:
    case: str
    exponent: float
    max_exponent: float
    seconds: dict[int, float]

    @property
    def ok(self) -> bool:
        return self.exponent <= self.max_exponent


def run_case(case: Case) -> Result:
    try:
        proc = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", case.name],
            capture_output=True,
            text=True,
            cwd=REPO,
            check=False,
            timeout=300,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"{case.name}: worker timed out after {exc.timeout}s"
        ) from exc
    if proc.returncode != 0:
        raise RuntimeError(f"{case.name}: worker failed\n{proc.stderr[-4000:]}")
    seconds = {int(k): v for k, v in json.loads(proc.stdout.splitlines()[-1]).items()}
    return Result(case.name, fit_exponent(seconds), case.max_exponent, seconds)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--case", action="append", choices=sorted(CASES))
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit 1 when a case exceeds its max exponent",
    )
    parser.add_argument("--json", metavar="PATH", help="write results as JSON")
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.worker:
        print(json.dumps(measure_in_process(CASES[args.worker])))
        return 0

    results = [run_case(CASES[name]) for name in args.case or sorted(CASES)]
    for r in results:
        sizes = " ".join(
            f"n={n}:{t * 1000:.1f}ms" for n, t in sorted(r.seconds.items())
        )
        status = "ok" if r.ok else "EXCEEDED"
        print(
            f"{r.case:24s} k={r.exponent:5.2f} (max {r.max_exponent:.2f}) {status:8s} {sizes}"
        )
    if args.json:
        Path(args.json).write_text(
            json.dumps(
                [{**asdict(r), "ok": r.ok} for r in results], indent=2, sort_keys=True
            )
            + "\n",
            encoding="utf-8",
        )
    failed = [r.case for r in results if not r.ok]
    if failed and args.strict:
        print(f"complexity-bench: exponent above ceiling for {', '.join(failed)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
