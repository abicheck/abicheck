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

"""Performance report: where ``compare()`` spends its time, and what repeats.

Not a gate. A reviewer-facing snapshot of the optimization backlog, built
from the same instruments the gates use, so the weekly ``performance.yml``
run turns into a list of candidates instead of "everything passed":

* **hot functions** -- cumulative time per first-party function, summed over
  every workload in ``tests/_compare_workloads.py`` (and, with
  ``--corpus N``, a ``compare()`` of two dumped real C++ libraries of N
  units from ``tests/_cpp_corpus.py`` when g++/castxml are present);
* **costed repeats** -- same-argument repeat calls ranked by the time they
  cost (``audit_repeated_calls.cost_weighted_repeats``);
* **calls per declaration** -- for the hot per-declaration helpers;
* **anti-pattern sites** -- ``perf_antipatterns`` counts per rule.

Cumulative times nest (a caller includes its callees), so rows rank
candidates; they are not additive. Known candidates and decisions live in
``docs/contribute/perf-findings.md`` -- read it before re-investigating one.

Usage::

    python scripts/perf_report.py                      # markdown to stdout
    python scripts/perf_report.py --n 600 --top 25 -o perf-report.md
    python scripts/perf_report.py --corpus 40           # + real dumped C++ library
"""

from __future__ import annotations

import argparse
import cProfile
import pstats
import sys
import tempfile
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO / "scripts"), str(REPO / "tests")]

from audit_repeated_calls import (  # noqa: E402
    PACKAGE_ROOT,
    PER_DECL_FUNCTIONS,
    _declaration_count,
    _profile_named_calls,
    cost_weighted_repeats,
)
from perf_antipatterns import scan_tree  # noqa: E402


def _hot_functions(pairs, top: int) -> list[tuple[str, float, int]]:
    from abicheck.checker import compare

    cumulative: Counter[str] = Counter()
    calls: Counter[str] = Counter()
    for old, new in pairs:
        profiler = cProfile.Profile()
        profiler.enable()
        compare(old, new)
        profiler.disable()
        for (filename, line, func), stat in pstats.Stats(profiler).stats.items():  # type: ignore[attr-defined]
            if filename.startswith(PACKAGE_ROOT):
                site = f"{Path(filename).relative_to(REPO).as_posix()}:{line}({func})"
                cumulative[site] += stat[3]
                calls[site] += stat[1]
    # Drop the entry point itself and thin wrappers that only forward.
    rows = [
        (s, t, calls[s])
        for s, t in cumulative.most_common()
        if "checker.py" not in s or "(compare)" not in s
    ]
    return rows[:top]


def _table(headers: list[str], rows: list[list[str]]) -> str:
    out = [
        "| " + " | ".join(headers) + " |",
        "|" + "|".join("---" for _ in headers) + "|",
    ]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(out)


def build_report(n: int, top: int, corpus: int | None) -> str:
    from _compare_workloads import WORKLOADS

    from abicheck.checker import compare

    sections = [
        f"# abicheck performance report\n\nSynthetic workloads at n={n}; cumulative times nest, so rows rank candidates rather than add up. Decisions on known candidates: `docs/contribute/perf-findings.md`."
    ]

    pairs = [build(n, f"rep_{name}_") for name, build in sorted(WORKLOADS.items())]
    if corpus:
        from _cpp_corpus import build_pair, toolchain_available

        if toolchain_available():
            with tempfile.TemporaryDirectory() as tmp:
                pairs.append(build_pair(Path(tmp), corpus))
            sections.append(
                f"Includes a dumped real C++ library of {corpus} units (`tests/_cpp_corpus.py`)."
            )
        else:
            sections.append(
                "`--corpus` requested but g++/castxml are unavailable; synthetic workloads only."
            )

    hot = _hot_functions(pairs, top)
    sections.append(
        "## Hot functions (cumulative seconds, all workloads)\n\n"
        + _table(
            ["seconds", "calls", "function"],
            [[f"{t:.3f}", str(c), f"`{s}`"] for s, t, c in hot],
        )
    )

    costed: Counter[str] = Counter()
    detail: dict[str, tuple[int, int]] = {}
    for name, build in sorted(WORKLOADS.items()):
        for row in cost_weighted_repeats(
            lambda tag, b=build, nm=name: b(n, f"{tag}{nm}_"),
            lambda o, w: compare(o, w),
        ):
            costed[row.function] += row.wasted_seconds
            r, c = detail.get(row.function, (0, 0))
            detail[row.function] = (r + row.repeats, c + row.calls)
    sections.append(
        "## Same-argument repeats, by estimated cost\n\n"
        + _table(
            ["est. seconds", "repeated / calls", "function"],
            [
                [f"{t:.3f}", f"{detail[f][0]}/{detail[f][1]}", f"`{f}`"]
                for f, t in costed.most_common(top)
            ],
        )
    )

    per_decl_rows = []
    for name, build in sorted(WORKLOADS.items()):
        old, new = build(n, f"pd_{name}_")
        decls = _declaration_count(old, new)
        counts = _profile_named_calls(
            lambda o=old, w=new: compare(o, w), PER_DECL_FUNCTIONS
        )
        per_decl_rows.append(
            [
                name,
                str(decls),
                *(f"{counts[f] / decls:.1f}" for f in PER_DECL_FUNCTIONS),
            ]
        )
    sections.append(
        "## Calls per declaration\n\n"
        + _table(["workload", "entities", *PER_DECL_FUNCTIONS], per_decl_rows)
    )

    sites = list(scan_tree())
    by_rule = Counter(s.rule for s in sites)
    by_file = Counter(s.path for s in sites)
    sections.append(
        "## Anti-pattern sites (`perf_antipatterns`)\n\n"
        + _table(["rule", "sites"], [[r, str(c)] for r, c in by_rule.most_common()])
        + "\n\nMost sites per file: "
        + ", ".join(f"`{f}` ({c})" for f, c in by_file.most_common(5))
    )
    return "\n\n".join(sections) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--n", type=int, default=400, help="synthetic workload size")
    parser.add_argument("--top", type=int, default=20, help="rows per section")
    parser.add_argument(
        "--corpus",
        type=int,
        default=None,
        help="also compare a dumped real C++ library of this many units",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="write markdown here instead of stdout",
    )
    args = parser.parse_args(argv)
    report = build_report(args.n, args.top, args.corpus)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
