#!/usr/bin/env python3
# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
"""Report each test's unique contribution to line coverage.

Reads a coverage data file recorded with per-test contexts::

    pytest tests/ --cov=abicheck --cov-context=test --cov-report=

and, for every test, counts the lines it covers and the lines **no other
test** covers. A test with zero unique lines adds nothing to line coverage
that the rest of the suite does not already give; it may still be valuable
(it can assert a value another test only executes), so this is a review
queue, never a deletion list and never a gate. Read it together with the
test-assertion-density gate in ``check_ai_readiness.py`` (weak asserts) and
the mutation lane (which test actually *kills* a mutant).

Sections:

* zero-unique tests ranked by how many lines they cover (a large, fully
  shadowed test is the most likely duplicate of a cheaper one);
* zero-unique tests ranked by runtime, when ``--durations`` is given (the
  cheapest place to start a review is the slow test that duplicates a
  faster one);
* files whose tests are mostly zero-unique;
* lines covered by exactly one test (the suite's single points of
  protection: deleting that test drops the line out of coverage).

Exits 0 always: a missing or unreadable input is reported, not raised, so
the weekly CI report never fails its job. ``--json PATH`` writes the
per-test rows; ``--durations PATH`` reads a pytest ``--junitxml`` report
(one ``<testcase time="...">`` per test, setup+call+teardown summed by
pytest itself).
"""

from __future__ import annotations

import argparse
import collections
import json
import pathlib
import xml.etree.ElementTree as ET  # nosec B405 - parses the junit report this run wrote
from collections.abc import Iterable, Mapping
from typing import NamedTuple

#: pytest-cov suffixes each context with the phase that ran it.
_PHASES = ("|setup", "|run", "|teardown")


class Contribution(NamedTuple):
    test: str
    covered: int
    unique: int


def context_test(context: str) -> str | None:
    """``tests/x.py::T::t[p]`` for a pytest-cov context; ``None`` for none."""
    if not context:
        return None
    for phase in _PHASES:
        if context.endswith(phase):
            context = context[: -len(phase)]
            break
    return context or None


def tally(
    lines: Iterable[tuple[str, int, Iterable[str]]],
) -> tuple[dict[str, Contribution], collections.Counter[str]]:
    """Per-test covered/unique counts from ``(file, line, contexts)`` rows.

    Also returns, per source file, how many of its lines exactly one test
    covers.
    """
    covered: collections.Counter[str] = collections.Counter()
    unique: collections.Counter[str] = collections.Counter()
    single: collections.Counter[str] = collections.Counter()
    for file, _line, contexts in lines:
        tests = {t for c in contexts if (t := context_test(c))}
        for t in tests:
            covered[t] += 1
        if len(tests) == 1:
            (only,) = tests
            unique[only] += 1
            single[file] += 1
    return (
        {t: Contribution(t, covered[t], unique[t]) for t in covered},
        single,
    )


def read_coverage(path: pathlib.Path) -> Iterable[tuple[str, int, list[str]]]:
    """Every measured ``(file, line, contexts)`` in a coverage data file."""
    from coverage import CoverageData  # dev dependency; imported lazily

    data = CoverageData(basename=str(path))
    data.read()
    for file in sorted(data.measured_files()):
        for line, contexts in sorted((data.contexts_by_lineno(file) or {}).items()):
            yield file, line, contexts


def dotted_id(nodeid: str) -> str:
    """The junit ``classname.name`` spelling of a pytest node id.

    ``tests/test_a.py::TestB::test_c[1]`` becomes ``tests.test_a.TestB.test_c[1]``,
    the form pytest writes into ``--junitxml`` (``classname`` is the dotted
    module and class path, ``name`` the function with its parameters). The
    node id's own text is never split on dots, so parameter ids survive.
    """
    file, _, rest = nodeid.partition("::")
    module = file.removesuffix(".py").replace("/", ".")
    if not rest:
        return module
    # Parameters are copied verbatim into junit's ``name``, so a ``::`` inside
    # ``[...]`` (a parametrized node id) must not be split.
    head, bracket, params = rest.partition("[")
    return ".".join([module, *head.split("::")]) + bracket + params


def junit_durations(path: pathlib.Path) -> dict[str, float]:
    """Seconds per test in a pytest ``--junitxml`` report, keyed by junit id.

    Keys are ``classname.name`` as written by pytest; a test that appears
    more than once (a rerun) has its times summed. A missing or non-numeric
    ``time`` counts as zero.
    """
    tree = ET.parse(path)  # nosec B314 - our own CI's report, not external input
    seconds: collections.Counter[str] = collections.Counter()
    for case in tree.iter("testcase"):
        key = ".".join(
            part for part in (case.get("classname"), case.get("name")) if part
        )
        if not key:
            continue
        try:
            seconds[key] += float(case.get("time") or 0.0)
        except ValueError:
            seconds[key] += 0.0
    return dict(seconds)


def timings_for(tests: Iterable[str], junit: Mapping[str, float]) -> dict[str, float]:
    """Seconds for each coverage test id that the junit report also names."""
    return {t: junit[k] for t in tests if (k := dotted_id(t)) in junit}


def _test_file(test: str) -> str:
    return test.split("::", 1)[0]


def summary(
    rows: dict[str, Contribution],
    single: collections.Counter[str],
    root: pathlib.Path,
    top: int,
    seconds: Mapping[str, float] | None = None,
) -> str:
    zero = sorted((r for r in rows.values() if r.unique == 0), key=lambda r: -r.covered)
    per_file = collections.Counter(_test_file(t) for t in rows)
    zero_file = collections.Counter(_test_file(r.test) for r in zero)
    mostly = sorted(
        ((f, zero_file[f], n) for f, n in per_file.items() if n >= 5),
        key=lambda x: (-x[1] / x[2], -x[2]),
    )
    out = [
        f"tests with coverage: {len(rows)}",
        f"zero-unique tests: {len(zero)} ({100 * len(zero) / max(len(rows), 1):.1f}%)",
        f"lines covered by exactly one test: {sum(single.values())}",
        "",
        f"largest zero-unique tests (lines covered) -- top {top}:",
        *(f"  {r.covered:>6}  {r.test}" for r in zero[:top]),
        "",
        *_slowest_zero_unique(zero, seconds, top),
        "",
        f"test files that are mostly zero-unique (>=5 tests) -- top {top}:",
        *(f"  {z:>4}/{n:<4} {100 * z / n:5.1f}%  {f}" for f, z, n in mostly[:top]),
        "",
        f"source files most dependent on single tests -- top {top}:",
    ]
    for file, n in single.most_common(top):
        try:
            shown = pathlib.Path(file).resolve().relative_to(root).as_posix()
        except ValueError:
            shown = file
        out.append(f"  {n:>6}  {shown}")
    return "\n".join(out)


def _slowest_zero_unique(
    zero: list[Contribution], seconds: Mapping[str, float] | None, top: int
) -> list[str]:
    """The runtime section: zero-unique tests by seconds, slowest first."""
    head = f"slowest zero-unique tests (seconds) -- top {top}:"
    if seconds is None:
        return [head, "  (no --durations report given)"]
    timed = sorted(
        ((seconds[r.test], r.test) for r in zero if r.test in seconds),
        key=lambda x: -x[0],
    )
    untimed = len(zero) - len(timed)
    lines = [head, *(f"  {s:>9.2f}  {t}" for s, t in timed[:top])]
    if not timed:
        lines.append("  (no zero-unique test appears in the durations report)")
    if untimed:
        lines.append(
            f"  ({untimed} zero-unique test(s) absent from the durations report)"
        )
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument(
        "--data",
        type=pathlib.Path,
        default=pathlib.Path(".coverage"),
        help="Coverage data file recorded with --cov-context=test.",
    )
    parser.add_argument(
        "--durations",
        type=pathlib.Path,
        help="pytest --junitxml report: adds the slowest zero-unique tests section.",
    )
    parser.add_argument("--json", type=pathlib.Path, help="Write per-test rows.")
    parser.add_argument("--top", type=int, default=30)
    args = parser.parse_args(argv)

    if not args.data.exists():
        print(f"no coverage data at {args.data}: record with --cov-context=test")
        return 0
    try:
        rows, single = tally(read_coverage(args.data))
    except Exception as exc:  # noqa: BLE001 -- report, never fail the weekly job
        print(f"cannot read coverage data {args.data}: {exc}")
        return 0
    if not rows:
        print("no per-test contexts found: record with --cov-context=test")
        return 0

    seconds: dict[str, float] | None = None
    if args.durations:
        try:
            seconds = timings_for(rows, junit_durations(args.durations))
        except (OSError, ET.ParseError) as exc:
            print(f"cannot read durations {args.durations}: {exc}")

    root = pathlib.Path(__file__).resolve().parent.parent
    print(summary(rows, single, root, args.top, seconds))
    if args.json:
        doc = [
            {**r._asdict(), "seconds": (seconds or {}).get(r.test)}
            for r in sorted(rows.values())
        ]
        args.json.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
        print(f"\nwrote {len(doc)} rows to {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
