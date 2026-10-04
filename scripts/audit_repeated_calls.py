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

"""Find first-party functions called repeatedly with the same arguments.

A function called twice with the same arguments inside one ``compare()`` is
either legitimately cheap or a missed memoization: re-demangling a
snapshot's names once per detector, rebuilding the same index once per
finding, re-resolving one surface per caller. Call counts alone cannot tell
"called 6 times on 6 inputs" from "called 6 times on one input"; this
audit can.

:func:`audit_repeated_calls` runs a thunk under :func:`sys.setprofile` and,
for every call into ``abicheck/``, fingerprints the call's arguments:

* plain values (``str``/``int``/``float``/``bool``/``None``/``bytes``/enum
  members) and tuples/frozensets of them compare by value;
* every other object compares by **identity** -- the same snapshot object
  passed twice is a repeat, two equal-but-distinct objects are not, and a
  mutable argument mutated between calls still reads as a repeat (the
  report says which, so a reader can judge).

It is an investigation tool, not a gate: some repeats are cheap, and the
fix for an expensive one is a memo with a sound lifetime, which only a
person can choose. The gate built on it
(``tests/test_compare_call_complexity.py``) pins zero repeats for a short,
reviewed list of expensive functions only.

Usage::

    python scripts/audit_repeated_calls.py                 # every workload, n=200
    python scripts/audit_repeated_calls.py --workload type_churn --n 400 --top 30
    python scripts/audit_repeated_calls.py --mode contract --min-repeats 3
"""

from __future__ import annotations

import argparse
import enum
import inspect
import sys
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = str(REPO / "abicheck")

_RESUMABLE = inspect.CO_GENERATOR | inspect.CO_COROUTINE | inspect.CO_ASYNC_GENERATOR
_PLAIN = (str, int, float, bool, bytes, type(None))


def _fingerprint(value: object) -> object:
    if isinstance(value, _PLAIN) or isinstance(value, enum.Enum):
        return value
    if isinstance(value, (tuple, frozenset)) and all(
        isinstance(v, _PLAIN) for v in value
    ):
        return (type(value).__name__, value)
    return ("id", type(value).__name__, id(value))


@dataclass(frozen=True)
class RepeatedCall:
    """One (function, argument fingerprint) seen more than once."""

    function: str
    calls: int
    arguments: str

    @property
    def wasted(self) -> int:
        return self.calls - 1

    def describe(self) -> str:
        return f"{self.calls:6d}x  {self.function}  ({self.arguments})"


def audit_repeated_calls(
    thunk: Callable[[], object], *, include: Callable[[str], bool] | None = None
) -> list[RepeatedCall]:
    """Run *thunk*; return every first-party call repeated with identical
    argument fingerprints, most repeated first.

    *include* (``"path:line(name)"`` -> bool) narrows which functions are
    fingerprinted, which keeps the profile hook cheap when only a few
    functions matter.
    """
    seen: Counter[tuple[str, tuple]] = Counter()
    labels: dict[tuple[str, tuple], str] = {}
    site_names: dict[object, str | None] = {}
    # Objects whose id() is part of a key are kept alive for the whole audit,
    # so an address cannot be recycled by an unrelated object mid-run and
    # read as a repeat.
    pinned: list[object] = []
    # A generator/coroutine frame raises a profiler "call" event on every
    # resumption, not just on entry; a frame already seen is a resumption.
    # Frames are pinned for the same id()-recycling reason as arguments.
    live_frames: dict[int, object] = {}

    def site_of(code) -> str | None:
        name = site_names.get(code, "")
        if name == "":
            fn = code.co_filename
            if not fn.startswith(PACKAGE_ROOT) or code.co_name.startswith("<"):
                name = None
            else:
                rel = Path(fn).relative_to(REPO).as_posix()
                name = f"{rel}:{code.co_firstlineno}({code.co_qualname})"
                if include is not None and not include(name):
                    name = None
            site_names[code] = name
        return name

    def hook(frame, event, _arg):
        if event != "call":
            return
        code = frame.f_code
        site = site_of(code)
        if site is None:
            return
        if code.co_flags & _RESUMABLE:
            if live_frames.get(id(frame)) is frame:
                return
            live_frames[id(frame)] = frame
        nargs = code.co_argcount + code.co_kwonlyargcount
        names = code.co_varnames[:nargs]
        values = [frame.f_locals.get(n) for n in names]
        key = (site, tuple(_fingerprint(v) for v in values))
        if seen[key] == 0:
            pinned.extend(values)
            labels[key] = ", ".join(
                f"{n}={type(v).__name__}" for n, v in zip(names, values, strict=True)
            )
        seen[key] += 1

    previous = sys.getprofile()
    sys.setprofile(hook)
    try:
        thunk()
    finally:
        sys.setprofile(previous)
    out = [
        RepeatedCall(site, n, labels[(site, args)])
        for (site, args), n in seen.items()
        if n > 1
    ]
    return sorted(out, key=lambda r: (-r.calls, r.function))


#: Whole-snapshot functions expensive enough that every repeated call with
#: the same arguments is worth a reviewed budget line. A function with its
#: own internal cache (``reconciled_public_function_maps``,
#: ``stdlib_namespaces_excluded``) is deliberately not listed: its repeats
#: cost a dict lookup.
BUDGETED_FUNCTIONS: tuple[str, ...] = (
    "build_contract_stage",
    "build_public_use_index",
    "build_surface_graph",
    "compute_export_surface",
    "demangle_batch",
    "detect_antipatterns",
    "recognise_idioms",
    "resolve_public_surface",
)

MODES: dict[str, dict[str, object]] = {
    "default": {},
    "contract": {"contract_evaluation": True, "contract_mode": "public"},
    "patterns_and_metrics": {"pattern_verdicts": True, "surface_metrics": True},
}

BUDGET_FILE = REPO / "tests" / "perf_call_budgets.json"
BUDGET_SIZES = (50, 200)


def budgeted_name(site: str) -> str | None:
    """The :data:`BUDGETED_FUNCTIONS` entry *site* (``path:line(qualname)``) defines, if any."""
    for name in BUDGETED_FUNCTIONS:
        if site.endswith(f"({name})") or site.endswith(f".{name})"):
            return name
    return None


def count_subprocess_spawns(thunk: Callable[[], object]) -> int:
    """How many child processes *thunk* starts through ``subprocess.Popen``."""
    import subprocess

    spawned = 0
    original = subprocess.Popen.__init__

    def counting_init(self, *args, **kwargs):
        nonlocal spawned
        spawned += 1
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = counting_init  # type: ignore[method-assign]
    try:
        thunk()
    finally:
        subprocess.Popen.__init__ = original  # type: ignore[method-assign]
    return spawned


def _workloads():
    sys.path.insert(0, str(REPO / "tests"))
    from _compare_workloads import WORKLOADS

    return WORKLOADS


def measure_budgets(
    tag: str = "budget", modes: tuple[str, ...] | None = None
) -> dict[str, dict[str, int]]:
    """The cost figures the budget file pins, per ``compare()`` mode.

    * ``repeats:<function>`` -- the most same-argument repeat calls of a
      budgeted function in one ``compare()``, over every workload and both
      :data:`BUDGET_SIZES`;
    * ``subprocess_spawns`` -- the most child processes one ``compare()``
      started.

    Each run salts its names with a fresh *tag*, so a process-wide cache
    (the demangler's) never hides a spawn a cold run would make.
    """
    from abicheck.checker import compare

    out: dict[str, dict[str, int]] = {}
    for mode in modes or tuple(MODES):
        kwargs = MODES[mode]
        figures: dict[str, int] = {f"repeats:{name}": 0 for name in BUDGETED_FUNCTIONS}
        figures["subprocess_spawns"] = 0
        for workload, build in sorted(_workloads().items()):
            for n in BUDGET_SIZES:
                old, new = build(n, f"{tag}_{mode}_{workload}_{n}_")
                rows = audit_repeated_calls(
                    lambda: compare(old, new, **kwargs),
                    include=lambda s: budgeted_name(s) is not None,
                )
                for row in rows:
                    key = f"repeats:{budgeted_name(row.function)}"
                    figures[key] = max(figures[key], row.wasted)
                old, new = build(n, f"{tag}_spawn_{mode}_{workload}_{n}_")
                spawns = count_subprocess_spawns(lambda: compare(old, new, **kwargs))
                figures["subprocess_spawns"] = max(figures["subprocess_spawns"], spawns)
        out[mode] = figures
    return out


def write_budgets() -> None:
    import json

    payload = {
        "_comment": (
            "Generated by scripts/audit_repeated_calls.py --write-budgets and checked "
            "exactly by tests/test_compare_cost_budgets.py. Lowering a number is the "
            "point; raising one needs its reason in the PR."
        ),
        "budgets": measure_budgets(),
    }
    BUDGET_FILE.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _main(argv: list[str] | None = None) -> int:
    from abicheck.checker import compare

    workloads = _workloads()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--workload",
        choices=sorted(workloads),
        action="append",
        help="workload(s) to run (default: all)",
    )
    parser.add_argument("--mode", choices=sorted(MODES), default="default")
    parser.add_argument("--n", type=int, default=200, help="workload size")
    parser.add_argument(
        "--top", type=int, default=20, help="rows to print per workload"
    )
    parser.add_argument(
        "--min-repeats",
        type=int,
        default=2,
        help="hide rows called fewer times than this",
    )
    parser.add_argument(
        "--write-budgets",
        action="store_true",
        help=f"re-measure and rewrite {BUDGET_FILE.relative_to(REPO)} (a reviewed change: commit it with its reason)",
    )
    args = parser.parse_args(argv)

    if args.write_budgets:
        write_budgets()
        print(f"wrote {BUDGET_FILE.relative_to(REPO)}")
        return 0

    for name in args.workload or sorted(workloads):
        old, new = workloads[name](args.n, f"audit_{name}_")
        rows = [
            r
            for r in audit_repeated_calls(lambda: compare(old, new, **MODES[args.mode]))
            if r.calls >= args.min_repeats
        ]
        print(
            f"== {name} (n={args.n}, mode={args.mode}): {len(rows)} repeated call sites, {sum(r.wasted for r in rows)} repeat calls"
        )
        for r in rows[: args.top]:
            print("  " + r.describe())
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
