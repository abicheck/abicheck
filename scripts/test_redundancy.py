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

"""Report tests whose covered lines, and mutant kills, other tests already provide.

A **review queue, not a deletion list**, and deliberately not a gate: it
reports, exits 0, and never edits a test. Read the "Test-quality gates"
section of ``docs/contribute/agent-guide/quality-gates.md`` before acting on a
finding.

Each test is classified against every other test in the same coverage run:

* ``unique`` -- it covers a line no other test covers, or kills a mutant no
  other test kills.
* ``coverage-redundant`` -- every line it covers is also covered by other
  tests. ``subsumed_by`` names the tests that cover *all* of its lines alone.
  Two identical tests name each other: neither is silently kept.
* ``fully-redundant`` -- coverage-redundant *and* kills no mutant that no
  other test kills. Possible only with a kill map (``--kills``).
* ``unknown`` -- no covered line was recorded for it (skipped, deselected or
  never run), so nothing can be said.

**Kill uniqueness needs data mutmut does not write.** mutmut 3.7.0 records
each mutant's outcome, not which tests killed it (its own TODO in
``mutmut/__main__.py``), and ``mutmut results`` lists only surviving mutants.
Without ``--kills`` every test is ``unique`` or ``coverage-redundant``, never
``fully-redundant``: absent mutation data is never read as "no kills".

**Coverage uniqueness is relative to the run that produced the data.** A
subset run (``pytest tests/test_diff_*.py``) can call a test redundant only
because the test covering its lines was not in that run. ``--scope`` limits
what is *reported*, not what is *compared against*.

Run::

    pytest tests/ -m "not integration and not libabigail and not abicc and not slow and not golden" \\
        --cov=abicheck --cov-context=test
    python scripts/test_redundancy.py --scope 'tests/test_diff_*.py' \\
        --json redundancy.json --markdown redundancy.md

``--kills`` takes a JSON object ``{mutant_key: [killing test node id, ...]}``.
Mutant keys are mutmut's own (``abicheck.diff_types.x_foo__mutmut_3``) and
must attribute to a module under ``mutation_results.split_mutant_key``; a key
that does not is refused, because dropping it could only make a test look
less needed.
"""

from __future__ import annotations

import argparse
import collections
import fnmatch
import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from mutation_results import split_mutant_key

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1

#: A covered source line: ``(absolute source path, line number)``.
Line = tuple[str, int]

#: pytest-cov's ``--cov-context=test`` suffixes each phase of a test with
#: ``|setup``, ``|run`` or ``|teardown``. All three fold into the one test.
_PHASES = frozenset({"setup", "run", "teardown"})

CLASSES = ("unique", "coverage-redundant", "fully-redundant", "unknown")

LIMITATION_NO_KILLS = (
    "No mutant kill data supplied (--kills), so kill uniqueness is unknown for "
    "every test and no test can be fully-redundant. mutmut 3.7.0 does not record "
    "which tests killed each mutant; its tests_by_mangled_function_name is the set "
    "of tests that reach a function, a superset of the killers."
)


def context_test_id(context: str) -> str | None:
    """The pytest node id a coverage context names, or ``None``.

    ``tests/x.py::test_a[1]|run`` -> ``tests/x.py::test_a[1]``. A context that
    is not a test phase (the empty context, or one a user set by hand) is not
    a test and is ignored.
    """
    node, sep, phase = context.rpartition("|")
    if sep and phase in _PHASES and node:
        return node
    return None


def function_of(test_id: str) -> str:
    """``tests/x.py::test_a[1-2]`` -> ``tests/x.py::test_a``: one unit per function."""
    return test_id.split("[", 1)[0]


def file_of(unit_id: str) -> str:
    """``tests/x.py::C::test_a`` -> ``tests/x.py``."""
    return unit_id.split("::", 1)[0]


def load_coverage(path: Path) -> tuple[dict[Line, set[str]], set[str]]:
    """``(test ids per covered line, every test id the data measured)``.

    A test with no covered line still appears in the second value, so it can
    be reported as ``unknown`` rather than silently dropped.
    """
    from coverage import CoverageData  # noqa: PLC0415 -- coverage is a dev dependency

    if not path.is_file():
        raise FileNotFoundError(
            f"no coverage data at {path}: run pytest with --cov=abicheck "
            "--cov-context=test first"
        )
    data = CoverageData(basename=str(path))
    data.read()
    universe = {t for c in data.measured_contexts() if (t := context_test_id(c))}
    lines: dict[Line, set[str]] = {}
    for filename in data.measured_files():
        for lineno, contexts in data.contexts_by_lineno(filename).items():
            tests = {t for c in contexts if (t := context_test_id(c))}
            if tests:
                lines[(filename, lineno)] = tests
    return lines, universe


def load_kills(path: Path) -> dict[str, set[str]]:
    """``{mutant key: killing test ids}`` from a ``--kills`` JSON file.

    Fails on any key that does not attribute to a module: an unattributable
    mutant could be one a test uniquely kills, and dropping it would let that
    test be called redundant on evidence that was thrown away.
    """
    doc = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a JSON object of mutant key -> test ids")
    kills: dict[str, set[str]] = {}
    unattributed: list[str] = []
    for mutant, killers in doc.items():
        if not (isinstance(killers, list) and all(isinstance(t, str) for t in killers)):
            raise ValueError(f"{path}: {mutant!r} must map to a list of test ids")
        if split_mutant_key(mutant) is None:
            unattributed.append(mutant)
        else:
            kills[mutant] = set(killers)
    if unattributed:
        raise ValueError(
            f"{path}: {len(unattributed)} mutant key(s) do not attribute to a module "
            f"(refusing rather than dropping them): {unattributed[:10]}"
        )
    return kills


@dataclass(frozen=True)
class UnitResult:
    """One test case or one test function, classified against the rest of the run."""

    unit: str
    covered_lines: int
    unique_lines: int
    #: Other units covering *every* line of this one alone. Empty for a
    #: coverage-redundant unit means its lines are covered only by several
    #: units together (``redundant_only_by_union``).
    subsumed_by: tuple[str, ...]
    #: ``None`` when no kill map was supplied.
    kills: int | None
    unique_kills: int | None
    classification: str

    def as_dict(self) -> dict[str, object]:
        redundant = self.classification in ("coverage-redundant", "fully-redundant")
        return {
            "id": self.unit,
            "file": file_of(self.unit),
            "classification": self.classification,
            "covered_lines": self.covered_lines,
            "unique_lines": self.unique_lines,
            "subsumed_by": list(self.subsumed_by),
            "redundant_only_by_union": redundant and not self.subsumed_by,
            "kills": self.kills,
            "unique_kills": self.unique_kills,
        }


def _classify(covered: int, unique_lines: int, unique_kills: int | None) -> str:
    if covered == 0:
        return "unknown"
    if unique_lines or unique_kills:
        return "unique"
    if unique_kills is None:
        return "coverage-redundant"
    return "fully-redundant"


def _subsumers(
    unit: str, own: list[Line], line_units: Mapping[Line, frozenset[str]]
) -> tuple[str, ...]:
    """Units that cover every line in *own* by themselves, other than *unit*."""
    common: frozenset[str] | None = None
    for line in own:
        common = line_units[line] if common is None else common & line_units[line]
        if not common:
            return ()
    return tuple(sorted((common or frozenset()) - {unit}))


def analyse(
    lines: Mapping[Line, set[str]],
    universe: Iterable[str],
    unit_of: Callable[[str], str],
    kills: Mapping[str, set[str]] | None,
) -> list[UnitResult]:
    """Classify every unit (``unit_of`` applied to each test id) in *universe*.

    Lines and kills are first projected onto units, so the same function
    serves per-case and per-function aggregation: a line is unique to a unit
    when only that unit covers it.
    """
    line_units = {
        line: frozenset(unit_of(t) for t in tests) for line, tests in lines.items()
    }
    lines_of: dict[str, list[Line]] = collections.defaultdict(list)
    unique_lines: collections.Counter[str] = collections.Counter()
    for line, units in line_units.items():
        for unit in units:
            lines_of[unit].append(line)
        if len(units) == 1:
            unique_lines[next(iter(units))] += 1

    kills_of: collections.Counter[str] = collections.Counter()
    unique_kills: collections.Counter[str] = collections.Counter()
    if kills is not None:
        for killers in kills.values():
            killer_units = frozenset(unit_of(t) for t in killers)
            for unit in killer_units:
                kills_of[unit] += 1
            if len(killer_units) == 1:
                unique_kills[next(iter(killer_units))] += 1

    units = {unit_of(t) for t in universe} | set(lines_of)
    results = []
    for unit in sorted(units):
        own = lines_of.get(unit, [])
        uniq = unique_lines[unit]
        subsumed = _subsumers(unit, own, line_units) if own and not uniq else ()
        uniq_kills = None if kills is None else unique_kills[unit]
        results.append(
            UnitResult(
                unit=unit,
                covered_lines=len(own),
                unique_lines=uniq,
                subsumed_by=subsumed,
                kills=None if kills is None else kills_of[unit],
                unique_kills=uniq_kills,
                classification=_classify(len(own), uniq, uniq_kills),
            )
        )
    return results


def build_report(
    coverage_file: Path,
    scope: list[str],
    lines: Mapping[Line, set[str]],
    universe: set[str],
    kills: Mapping[str, set[str]] | None,
) -> dict[str, object]:
    """The JSON-serializable report: per level, a summary and every in-scope unit."""

    def in_scope(unit: str) -> bool:
        return not scope or any(
            fnmatch.fnmatchcase(file_of(unit), pat) for pat in scope
        )

    levels: dict[str, object] = {}
    for level, unit_of in (
        ("case", lambda test: test),
        ("function", function_of),
    ):
        results = [
            r for r in analyse(lines, universe, unit_of, kills) if in_scope(r.unit)
        ]
        levels[level] = {
            "summary": {
                c: sum(r.classification == c for r in results) for c in CLASSES
            },
            "units": [r.as_dict() for r in results],
        }

    killer_tests = {t for killers in (kills or {}).values() for t in killers}
    mutation: dict[str, object] = {
        "available": kills is not None,
        "mutants": len(kills or {}),
        "killers_absent_from_coverage": len(killer_tests - universe),
        "limitation": None if kills is not None else LIMITATION_NO_KILLS,
    }
    return {
        "schema": SCHEMA_VERSION,
        "coverage_file": str(coverage_file),
        "scope": scope,
        "tests_measured": len(universe),
        "mutation": mutation,
        "levels": levels,
    }


def _row(unit: Mapping[str, object]) -> str:
    kills = (
        "unknown"
        if unit["kills"] is None
        else f"{unit['unique_kills']}/{unit['kills']}"
    )
    subsumed = ", ".join(f"`{s}`" for s in unit["subsumed_by"]) or (
        "(covered by several tests together)" if unit["redundant_only_by_union"] else ""
    )
    return (
        f"| `{unit['id']}` | {unit['classification']} | {unit['covered_lines']} | "
        f"{unit['unique_lines']} | {kills} | {subsumed} |"
    )


_TABLE_HEADER = (
    "| test | classification | covered lines | unique lines | unique kills / kills | subsumed by |\n"
    "|---|---|---:|---:|---:|---|"
)


def render_summary(report: Mapping[str, object], top: int) -> str:
    """Headline counts plus the largest redundant findings, for the job log and PR body."""
    mutation = report["mutation"]
    assert isinstance(mutation, dict)
    lines = [
        "# Test redundancy report (report only, not a gate)",
        "",
        f"- Coverage: `{report['coverage_file']}`, {report['tests_measured']} test id(s) measured",
        f"- Scope: {', '.join(f'`{s}`' for s in report['scope']) or 'all tests'}",
        "- Mutation: "
        + (
            f"{mutation['mutants']} mutant(s) with recorded killers"
            if mutation["available"]
            else f"not available -- {mutation['limitation']}"
        ),
        "",
        "| level | unique | coverage-redundant | fully-redundant | unknown |",
        "|---|---:|---:|---:|---:|",
    ]
    levels = report["levels"]
    assert isinstance(levels, dict)
    for level, body in levels.items():
        s = body["summary"]
        lines.append(
            f"| {level} | {s['unique']} | {s['coverage-redundant']} | "
            f"{s['fully-redundant']} | {s['unknown']} |"
        )
    for level, body in levels.items():
        redundant = [u for u in body["units"] if u["classification"] != "unique"]
        redundant.sort(key=lambda u: (-u["covered_lines"], u["id"]))
        if not redundant:
            continue
        lines += [
            "",
            f"## Largest non-unique {level}s (top {min(top, len(redundant))} of {len(redundant)})",
            "",
            _TABLE_HEADER,
            *(_row(u) for u in redundant[:top]),
        ]
    return "\n".join(lines) + "\n"


def render_detail(report: Mapping[str, object]) -> str:
    """Per-file tables of every non-unique unit. ``unique`` rows are left out: they are the default."""
    levels = report["levels"]
    assert isinstance(levels, dict)
    out = ["# Per-file detail (non-unique units only)"]
    for level, body in levels.items():
        by_file: dict[str, list[Mapping[str, object]]] = collections.defaultdict(list)
        for unit in body["units"]:
            if unit["classification"] != "unique":
                by_file[unit["file"]].append(unit)
        out.append(f"\n## {level} level")
        for path in sorted(by_file):
            out += ["", f"### `{path}` ({len(by_file[path])})", "", _TABLE_HEADER]
            out += [_row(u) for u in sorted(by_file[path], key=lambda u: str(u["id"]))]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--coverage-file",
        type=Path,
        default=REPO_ROOT / ".coverage",
        help="coverage data written by pytest --cov-context=test (default: %(default)s)",
    )
    parser.add_argument(
        "--scope",
        action="append",
        default=[],
        metavar="GLOB",
        help="report only tests whose file matches GLOB, e.g. 'tests/test_diff_*.py' "
        "(repeatable). Comparison still uses every test in the coverage data.",
    )
    parser.add_argument(
        "--kills",
        type=Path,
        default=None,
        help="JSON {mutant key: [killing test ids]}; without it no test can be fully-redundant",
    )
    parser.add_argument("--json", type=Path, help="write the full report as JSON")
    parser.add_argument(
        "--markdown",
        type=Path,
        help="write the summary and per-file detail as markdown",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=20,
        help="findings listed in the summary (default: %(default)s)",
    )
    args = parser.parse_args(argv)

    try:
        lines, universe = load_coverage(args.coverage_file)
        kills = load_kills(args.kills) if args.kills else None
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1

    report = build_report(args.coverage_file, args.scope, lines, universe, kills)
    summary = render_summary(report, args.top)
    print(summary, end="")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.markdown:
        args.markdown.write_text(
            summary + "\n" + render_detail(report), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
