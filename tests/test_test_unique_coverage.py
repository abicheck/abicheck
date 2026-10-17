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

"""Unit tests for scripts/test_unique_coverage.py.

The tally oracle is hand-computed from the fixture in ``_FIXTURE`` below
(not from the script's own helpers): each row is one source line and the
tests whose contexts cover it.
"""

from __future__ import annotations

import collections
import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "test_unique_coverage.py"


def _load():
    spec = importlib.util.spec_from_file_location("test_unique_coverage", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


tuc = _load()

T1 = "tests/t1.py::test_a"
T2 = "tests/t2.py::test_b"
T3 = "tests/t3.py::test_c"
T4 = "tests/t4.py::test_d"

# (context, file, lines) as recorded by pytest-cov with --cov-context=test.
# Per source line, the covering tests are:
#   a.py:1 {t1}    a.py:2 {t1, t2}    a.py:3 {t2}    a.py:4 {t1, t4}
#   b.py:1 {t3}    b.py:2 {t1, t3}
_FIXTURE = [
    (f"{T1}|run", "/src/a.py", [1, 2, 4]),
    (f"{T1}|run", "/src/b.py", [2]),
    (f"{T2}|setup", "/src/a.py", [3]),
    (f"{T2}|run", "/src/a.py", [2, 3]),
    (f"{T2}|teardown", "/src/a.py", [3]),
    (f"{T3}|run", "/src/b.py", [1, 2]),
    (f"{T4}|run", "/src/a.py", [4]),
]


def _oracle_rows():
    """Hand-computed from the per-line table above."""
    return {
        # covers a1, a2, a4, b2; a1 is its only sole line
        T1: tuc.Contribution(T1, covered=4, unique=1),
        # covers a2, a3; a3 is its only sole line
        T2: tuc.Contribution(T2, covered=2, unique=1),
        # covers b1, b2; b1 is its only sole line
        T3: tuc.Contribution(T3, covered=2, unique=1),
        # covers a4 with t1; shadowed entirely
        T4: tuc.Contribution(T4, covered=1, unique=0),
    }


def _tally_fixture():
    lines = [(file, line, [ctx]) for ctx, file, nums in _FIXTURE for line in nums]
    # Merge the per-context rows into one contexts list per (file, line).
    merged: dict[tuple[str, int], list[str]] = collections.defaultdict(list)
    for file, line, ctxs in lines:
        merged[(file, line)].extend(ctxs)
    return tuc.tally(
        (file, line, ctxs) for (file, line), ctxs in sorted(merged.items())
    )


# -- context_test ---------------------------------------------------------


@pytest.mark.parametrize(
    "context,expected",
    [
        (f"{T1}|setup", T1),
        (f"{T1}|run", T1),
        (f"{T1}|teardown", T1),
        ("tests/x.py::T::t[p]|run", "tests/x.py::T::t[p]"),
        # no phase suffix: left as is
        ("tests/x.py::test_plain", "tests/x.py::test_plain"),
        # a bare phase is not a test
        ("|run", None),
        ("", None),
    ],
)
def test_context_test_strips_phase_suffix(context, expected):
    assert tuc.context_test(context) == expected


def test_context_test_empty_is_none():
    assert tuc.context_test("") is None


# -- tally ----------------------------------------------------------------


def test_tally_counts_match_hand_computed_oracle():
    rows, _single = _tally_fixture()
    assert rows == _oracle_rows()


def test_tally_single_test_lines_per_file():
    _rows, single = _tally_fixture()
    # a.py: lines 1 and 3 are covered by one test each; b.py: line 1.
    assert single == collections.Counter({"/src/a.py": 2, "/src/b.py": 1})


def test_tally_phases_of_one_test_count_as_one_test():
    # Line 3 is covered by t2 in setup, run and teardown: still one test,
    # so it is unique to t2 (not a multi-test line).
    rows, _ = tuc.tally(
        [("/src/a.py", 3, [f"{T2}|setup", f"{T2}|run", f"{T2}|teardown"])]
    )
    assert rows == {T2: tuc.Contribution(T2, covered=1, unique=1)}


def test_tally_ignores_lines_with_no_test_context():
    rows, single = tuc.tally([("/src/a.py", 9, [""]), ("/src/a.py", 10, [])])
    assert rows == {}
    assert single == collections.Counter()


# -- junit durations ------------------------------------------------------

_JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="4">
    <testcase classname="tests.t1" name="test_a" time="0.5"/>
    <testcase classname="tests.t1.TestB" name="test_y[1-2]" time="1.25"/>
    <testcase classname="tests.t1" name="test_a" time="0.25"/>
    <testcase classname="tests.t4" name="test_d" time="oops"/>
  </testsuite>
</testsuites>
"""


def test_junit_durations_sums_reruns_and_zeroes_bad_time(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text(_JUNIT, encoding="utf-8")
    assert tuc.junit_durations(xml) == {
        "tests.t1.test_a": 0.75,
        "tests.t1.TestB.test_y[1-2]": 1.25,
        "tests.t4.test_d": 0.0,
    }


@pytest.mark.parametrize(
    "nodeid,dotted",
    [
        ("tests/test_a.py::test_x", "tests.test_a.test_x"),
        ("tests/test_a.py::TestB::test_y[1-2]", "tests.test_a.TestB.test_y[1-2]"),
        ("tests/test_a.py::test_p[a.b]", "tests.test_a.test_p[a.b]"),
        # a parameter containing "::" is one name in junit, not a class path
        (
            "tests/test_a.py::test_p[tests/t.py::x|run-tests/t.py::x]",
            "tests.test_a.test_p[tests/t.py::x|run-tests/t.py::x]",
        ),
        ("tests/test_a.py", "tests.test_a"),
    ],
)
def test_dotted_id_matches_junit_spelling(nodeid, dotted):
    assert tuc.dotted_id(nodeid) == dotted


def test_timings_for_joins_coverage_ids_to_junit_keys(tmp_path):
    xml = tmp_path / "junit.xml"
    xml.write_text(_JUNIT, encoding="utf-8")
    junit = tuc.junit_durations(xml)
    got = tuc.timings_for(
        [
            "tests/t1.py::test_a",
            "tests/t1.py::TestB::test_y[1-2]",
            "tests/t9.py::test_none",
        ],
        junit,
    )
    assert got == {"tests/t1.py::test_a": 0.75, "tests/t1.py::TestB::test_y[1-2]": 1.25}


# -- summary --------------------------------------------------------------


def _summary(seconds=None):
    rows, single = _tally_fixture()
    return tuc.summary(rows, single, Path("/repo"), top=10, seconds=seconds)


def test_summary_headline_and_zero_unique_sections():
    text = _summary()
    assert "tests with coverage: 4" in text
    assert "zero-unique tests: 1 (25.0%)" in text
    assert "lines covered by exactly one test: 3" in text
    assert (
        f"largest zero-unique tests (lines covered) -- top 10:\n       1  {T4}" in text
    )


def test_summary_source_file_section_lists_single_test_lines():
    text = _summary()
    assert "source files most dependent on single tests -- top 10:" in text
    assert "       2  /src/a.py" in text
    assert "       1  /src/b.py" in text


def test_summary_runtime_section_without_durations_says_so():
    text = _summary()
    assert "slowest zero-unique tests (seconds) -- top 10:" in text
    assert "(no --durations report given)" in text


def test_summary_runtime_section_ranks_zero_unique_by_seconds():
    text = _summary(seconds={T4: 2.5, T1: 99.0})
    # T1 is slow but not zero-unique, so it must not be ranked here.
    section = text.split("slowest zero-unique tests (seconds) -- top 10:\n", 1)[1]
    section = section.split("\n\n", 1)[0]
    assert section.splitlines() == [f"       2.50  {T4}"]


def test_summary_runtime_section_reports_untimed_zero_unique_tests():
    # Only the zero-unique test T4 is in the rows; no timing for it.
    text = _summary(seconds={T1: 1.0})
    assert "(no zero-unique test appears in the durations report)" in text


# -- end to end (real coverage data file, real junit xml) ------------------


def _write_coverage(path: Path) -> None:
    from coverage import CoverageData

    data = CoverageData(basename=str(path))
    for ctx, file, nums in _FIXTURE:
        data.set_context(ctx)
        data.add_lines({file: nums})
    data.write()


def test_main_end_to_end_writes_rows_with_seconds(tmp_path, capsys):
    data = tmp_path / ".coverage"
    _write_coverage(data)
    xml = tmp_path / "junit.xml"
    xml.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase classname="tests.t4" name="test_d" time="2.5"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    out = tmp_path / "unique.json"

    rc = tuc.main(["--data", str(data), "--durations", str(xml), "--json", str(out)])

    assert rc == 0
    printed = capsys.readouterr().out
    assert "     2.50  " + T4 in printed
    doc = json.loads(out.read_text(encoding="utf-8"))
    by_test = {row["test"]: row for row in doc}
    assert by_test[T4] == {"test": T4, "covered": 1, "unique": 0, "seconds": 2.5}
    assert by_test[T1] == {"test": T1, "covered": 4, "unique": 1, "seconds": None}


def test_main_exits_zero_on_missing_inputs(tmp_path, capsys):
    assert tuc.main(["--data", str(tmp_path / "absent")]) == 0
    assert "no coverage data" in capsys.readouterr().out


def test_main_exits_zero_on_unreadable_durations(tmp_path, capsys):
    data = tmp_path / ".coverage"
    _write_coverage(data)
    bad = tmp_path / "junit.xml"
    bad.write_text("<testsuites", encoding="utf-8")
    assert tuc.main(["--data", str(data), "--durations", str(bad)]) == 0
    assert "cannot read durations" in capsys.readouterr().out
