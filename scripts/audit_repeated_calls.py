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

The whole-package ratchet (``--write-repeat-baseline``, gated by
``tests/test_compare_repeat_audit.py``) counts every first-party function
except dunders and the memo machinery, keyed by ``path(qualname)``.

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
import time
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
    thunk: Callable[[], object],
    *,
    include: Callable[[str], bool] | None = None,
    repeat_seconds: dict[str, float] | None = None,
    site_calls: dict[str, int] | None = None,
) -> list[RepeatedCall]:
    """Run *thunk*; return every first-party call repeated with identical
    argument fingerprints, most repeated first.

    *include* (``"path:line(name)"`` -> bool) narrows which functions are
    fingerprinted, which keeps the profile hook cheap when only a few
    functions matter.

    *repeat_seconds*, when given, is filled with the inclusive wall time
    spent inside the *repeat* calls of each site (every call after the
    first with the same fingerprint). This is measured per call, not
    apportioned from a cumulative total: a memoised function whose first
    call does all the work and whose repeats are cache hits reports the
    cost of the cache hits, not an average that includes the miss.
    Generator/coroutine frames are counted but not timed (their frames
    return on every ``yield``).

    *site_calls*, when given, is filled with every site's total call count,
    unique-argument calls included -- the denominator a repeated share needs.
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
    # (frame, site, start) for every timed repeat call still on the stack.
    timed: list[tuple[object, str, float]] = []
    clock = time.perf_counter

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
        if event == "return":
            if timed and timed[-1][0] is frame:
                _f, site, start = timed.pop()
                if repeat_seconds is not None:
                    repeat_seconds[site] = (
                        repeat_seconds.get(site, 0.0) + clock() - start
                    )
            return
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
        if site_calls is not None:
            site_calls[site] = site_calls.get(site, 0) + 1
        if (
            repeat_seconds is not None
            and seen[key] > 1
            and not code.co_flags & _RESUMABLE
        ):
            timed.append((frame, site, clock()))

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
    # The construction behind build_surface_graph's scope-shared memo: the
    # wrapper may repeat (a cache hit under ``--pattern-verdicts`` plus
    # ``--surface-metrics``), the build may not.
    "_build_surface_graph",
    "compute_export_surface",
    "demangle_batch",
    "detect_antipatterns",
    "recognise_idioms",
    "resolve_public_surface",
)

#: Hot per-declaration helpers whose *call count per declaration* is pinned
#: (``per_decl_x10:<name>``: ten times the worst calls-per-declaration ratio,
#: rounded up). A growth gate cannot see a helper going from 3 to 9 calls per
#: declaration -- that is still linear -- but every such step is a real
#: constant-factor regression on a large library.
PER_DECL_FUNCTIONS: tuple[str, ...] = (
    "canonicalize_type_name",
    "demangle_one_batched",
    "in_public_surface",
    "in_source_declaration_index",
    "qualified_declaration_name",
    "type_identifiers",
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
    """How many child processes *thunk* starts through ``subprocess.Popen``.

    Counted from a cold demangler: ``demangle`` latches "c++filt is missing"
    process-wide, and a latch left by an earlier caller in the same process
    (a test that faked a missing binary) would make every later count read
    0. That made the recorded budget depend on test order, not on the code.
    """
    import subprocess

    from abicheck.demangle import _reset_demangle_batch_cache

    _reset_demangle_batch_cache()
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


def _profile_named_calls(
    thunk: Callable[[], object], names: tuple[str, ...]
) -> dict[str, int]:
    """Total calls of each first-party function whose bare name is in *names*."""
    import cProfile
    import pstats

    profiler = cProfile.Profile()
    profiler.enable()
    try:
        thunk()
    finally:
        profiler.disable()
    totals = dict.fromkeys(names, 0)
    for (filename, _line, func), stat in pstats.Stats(profiler).stats.items():  # type: ignore[attr-defined]
        if filename.startswith(PACKAGE_ROOT) and func in totals:
            totals[func] += stat[1]
    return totals


def _declaration_count(*snapshots: object) -> int:
    """Declarations plus their parameters, fields and enumerators: the
    entities a per-declaration helper is legitimately asked about. Counting
    declarations alone made a 200-parameter signature look like 200 calls
    of waste."""
    total = 0
    for snap in snapshots:
        d = snap.declarations  # type: ignore[attr-defined]
        total += len(d.variables)
        total += sum(1 + len(f.params) for f in d.functions)
        total += sum(1 + len(t.fields) for t in d.types)
        total += sum(1 + len(e.members) for e in d.enums)
    return max(total, 1)


def measure_budgets(
    tag: str = "budget", modes: tuple[str, ...] | None = None
) -> dict[str, dict[str, int]]:
    """The cost figures the budget file pins, per ``compare()`` mode.

    * ``repeats:<function>`` -- the most same-argument repeat calls of a
      budgeted function in one ``compare()``, over every workload and both
      :data:`BUDGET_SIZES`;
    * ``subprocess_spawns`` -- the most child processes one ``compare()``
      started;
    * ``per_decl_x10:<function>`` -- for :data:`PER_DECL_FUNCTIONS`, ten
      times the worst calls-per-declaration ratio, rounded up.

    Each run salts its names with a fresh *tag*, so a process-wide cache
    (the demangler's) never hides a spawn a cold run would make.
    """
    from abicheck.checker import compare
    from abicheck.demangle import _reset_demangle_batch_cache

    out: dict[str, dict[str, int]] = {}
    for mode in modes or tuple(MODES):
        _reset_demangle_batch_cache()
        kwargs = MODES[mode]
        figures: dict[str, int] = {f"repeats:{name}": 0 for name in BUDGETED_FUNCTIONS}
        figures["subprocess_spawns"] = 0
        figures.update({f"per_decl_x10:{name}": 0 for name in PER_DECL_FUNCTIONS})
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
                old, new = build(n, f"{tag}_decl_{mode}_{workload}_{n}_")
                decls = _declaration_count(old, new)
                for name, calls in _profile_named_calls(
                    lambda: compare(old, new, **kwargs), PER_DECL_FUNCTIONS
                ).items():
                    key = f"per_decl_x10:{name}"
                    figures[key] = max(figures[key], -(-calls * 10 // decls))
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


# -- whole-package repeat audit (test-time ratchet) --------------------------

#: Where the whole-package audit's ratchet lives. Unlike
#: :data:`BUDGET_FILE` (a reviewed list of expensive functions), this covers
#: *every* first-party function, so it sees a newly-introduced repeat in a
#: helper nobody thought to list.
REPEAT_BASELINE_FILE = REPO / "tests" / "perf_repeat_baseline.json"
REPEAT_AUDIT_N = 40
REPEAT_AUDIT_MODE = "default"

#: Code whose repeats are not recomputation: the memo machinery itself (a
#: repeat there *is* a cache hit) is excluded. Dunders (``__init__``,
#: ``__eq__``, ``__hash__``, ``__getattr__``) are construction/protocol hooks,
#: not pure functions of their arguments, and are excluded too.
_REPEAT_AUDIT_EXCLUDED_FILES = ("abicheck/model/execution_cache.py",)


def repeat_site_key(site: str) -> str:
    """``path:line(qualname)`` -> ``path(qualname)``: line numbers move on
    any unrelated edit, so the ratchet keys on the function, not its line."""
    path, _, rest = site.partition(":")
    return f"{path}({rest.split('(', 1)[1]}" if "(" in rest else site


def _repeat_audit_includes(site: str) -> bool:
    if site.startswith(_REPEAT_AUDIT_EXCLUDED_FILES):
        return False
    qualname = site.rsplit("(", 1)[1].rstrip(")")
    leaf = qualname.rsplit(".", 1)[-1]
    return not (leaf.startswith("__") and leaf.endswith("__"))


def measure_repeat_audit(n: int = REPEAT_AUDIT_N, tag: str = "ra_") -> dict[str, int]:
    """Same-argument repeat calls per first-party function, summed over every
    synthetic workload at size *n* (one ``compare()`` each).

    Deterministic only in a **fresh process**: process-wide caches (the
    demangler's, canonical-spelling memos) warmed by an earlier caller remove
    repeats a cold run makes. :func:`measure_repeat_audit_subprocess` is what
    the gate calls.
    """
    from abicheck.checker import compare

    out: dict[str, int] = {}
    kwargs = MODES[REPEAT_AUDIT_MODE]
    for workload, build in sorted(_workloads().items()):
        old, new = build(n, f"{tag}{workload}_")
        for row in audit_repeated_calls(
            lambda: compare(old, new, **kwargs), include=_repeat_audit_includes
        ):
            key = repeat_site_key(row.function)
            out[key] = out.get(key, 0) + row.wasted
    return dict(sorted(out.items()))


def measure_repeat_audit_subprocess(n: int = REPEAT_AUDIT_N) -> dict[str, int]:
    """:func:`measure_repeat_audit` in a cold child interpreter."""
    import json
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--repeat-audit-json",
            "--n",
            str(n),
        ],
        capture_output=True,
        text=True,
        cwd=REPO,
        check=False,
        timeout=600,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"repeat audit subprocess failed (exit {proc.returncode})\n{proc.stderr[-4000:]}"
        )
    return json.loads(proc.stdout.splitlines()[-1])


def compare_repeat_audit(
    measured: dict[str, int], baseline: dict[str, object]
) -> tuple[list[str], list[str]]:
    """``(errors, improvements)`` of *measured* against *baseline*.

    Errors: the total above the recorded total, or any function above its
    own recorded figure (a function absent from the baseline is recorded at
    0, so a new repeating function is an error even when the total fell).
    Improvements: a total below the recorded one -- the ratchet must be
    re-recorded so the slack cannot be spent by the next regression.
    """
    functions = baseline.get("functions", {})
    assert isinstance(functions, dict)
    recorded_total = int(baseline.get("total", 0))  # type: ignore[arg-type]
    errors = [
        f"{fn}: {count} same-argument repeat call(s), baseline {functions.get(fn, 0)}"
        for fn, count in sorted(measured.items())
        if count > int(functions.get(fn, 0))
    ]
    total = sum(measured.values())
    if total > recorded_total:
        errors.insert(
            0, f"total: {total} same-argument repeat calls, baseline {recorded_total}"
        )
    improvements = (
        [f"total: {total} same-argument repeat calls, baseline {recorded_total}"]
        if total < recorded_total
        else []
    )
    return errors, improvements


def write_repeat_baseline() -> dict[str, object]:
    import json

    measured = measure_repeat_audit_subprocess()
    payload: dict[str, object] = {
        "_comment": (
            "Generated by scripts/audit_repeated_calls.py --write-repeat-baseline "
            "and checked by tests/test_compare_repeat_audit.py: same-argument "
            "repeat calls of every first-party function over one cold compare() "
            "per synthetic workload. Lowering a number is the point; raising one "
            "needs its reason in the PR."
        ),
        "n": REPEAT_AUDIT_N,
        "mode": REPEAT_AUDIT_MODE,
        "total": sum(measured.values()),
        "functions": measured,
    }
    REPEAT_BASELINE_FILE.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return payload


@dataclass(frozen=True)
class CostedRepeat:
    """A function's same-argument repeats, weighted by what they cost."""

    function: str
    repeats: int
    calls: int
    repeat_seconds: float

    @property
    def wasted_seconds(self) -> float:
        # Measured inclusive time of the repeat calls themselves. Inclusive
        # times nest (a repeated caller includes its repeated callees), so
        # rows are a ranking signal, not an additive budget.
        return self.repeat_seconds

    def describe(self) -> str:
        return f"{self.wasted_seconds:8.3f}s  {self.repeats:7d}/{self.calls:<7d} repeated  {self.function}"


def cost_weighted_repeats(
    build: Callable[[str], tuple[object, object]],
    run: Callable[[object, object], object],
) -> list[CostedRepeat]:
    """Rank first-party functions by the time their same-argument repeats cost.

    *build(tag)* returns a fresh ``(old, new)`` pair. The cost of a site is
    the measured time spent inside its *repeat* calls
    (:func:`audit_repeated_calls`'s ``repeat_seconds``). An earlier version
    apportioned cProfile's cumulative time by the repeated share of calls,
    which charged a memoised wrapper's single real computation to its cache
    hits: ``_reconciled_function_surfaces`` (one miss, eleven hits) ranked as
    a top repeat cost while its repeats cost microseconds. Absolute numbers
    include the profile hook's overhead; compare rows, not seconds.
    """
    old, new = build("cost_audit_")
    seconds: dict[str, float] = {}
    totals: dict[str, int] = {}
    rows = audit_repeated_calls(
        lambda: run(old, new), repeat_seconds=seconds, site_calls=totals
    )
    repeats_by_site: dict[str, int] = {}
    for row in rows:
        repeats_by_site[row.function] = (
            repeats_by_site.get(row.function, 0) + row.wasted
        )
    out = [
        CostedRepeat(site, repeats, totals.get(site, repeats), seconds.get(site, 0.0))
        for site, repeats in repeats_by_site.items()
    ]
    return sorted(out, key=lambda r: -r.wasted_seconds)


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
        "--by-cost",
        action="store_true",
        help="rank by estimated time the repeats cost (cumulative time x repeated share) instead of by count",
    )
    parser.add_argument(
        "--write-budgets",
        action="store_true",
        help=f"re-measure and rewrite {BUDGET_FILE.relative_to(REPO)} (a reviewed change: commit it with its reason)",
    )
    parser.add_argument(
        "--repeat-audit-json",
        action="store_true",
        help="print the whole-package same-argument repeat figures (one JSON line) and exit",
    )
    parser.add_argument(
        "--write-repeat-baseline",
        action="store_true",
        help=f"re-measure and rewrite {REPEAT_BASELINE_FILE.relative_to(REPO)} in a cold child process",
    )
    args = parser.parse_args(argv)

    if args.repeat_audit_json:
        import json

        print(json.dumps(measure_repeat_audit(args.n)))
        return 0
    if args.write_repeat_baseline:
        payload = write_repeat_baseline()
        print(
            f"wrote {REPEAT_BASELINE_FILE.relative_to(REPO)} (total {payload['total']})"
        )
        return 0

    if args.write_budgets:
        write_budgets()
        print(f"wrote {BUDGET_FILE.relative_to(REPO)}")
        return 0

    if args.by_cost:
        for name in args.workload or sorted(workloads):
            rows = cost_weighted_repeats(
                lambda tag, name=name: workloads[name](args.n, f"{tag}{name}_"),
                lambda old, new: compare(old, new, **MODES[args.mode]),
            )
            print(
                f"== {name} (n={args.n}, mode={args.mode}): top repeats by estimated cost"
            )
            for r in rows[: args.top]:
                print("  " + r.describe())
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
