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

"""Tests for scripts/known_gap_canaries.py — the `known-gap-canaries` gate.

The rules are exercised on synthetic pages against a synthetic `tests/`
tree written per test (one page, one tree, one expected problem set each);
the real page is checked at the end, and pytest is asked to collect every
canary it names.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_spec = importlib.util.spec_from_file_location(
    "known_gap_canaries_under_test", _SCRIPTS / "known_gap_canaries.py"
)
assert _spec is not None and _spec.loader is not None
kgc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = kgc
_spec.loader.exec_module(kgc)

REPO = Path(__file__).resolve().parent.parent

_CANARY_FILE = """import pytest

pytestmark = pytest.mark.integration


@pytest.mark.known_gap_canary
def test_marked():
    assert 1 + 1 == 2


def test_unmarked():
    assert 1 + 1 == 2


class TestGroup:
    @pytest.mark.known_gap_canary
    def test_in_class(self):
        assert True


@pytest.mark.known_gap_canary
@pytest.mark.xfail(reason="gap", strict=False)
def test_lax_xfail():
    assert False


@pytest.mark.known_gap_canary
@pytest.mark.skip(reason="later")
def test_skipped():
    assert False


@pytest.mark.known_gap_canary
@pytest.mark.xfail(reason="gap", strict=True)
def test_strict_xfail():
    assert False
"""

_ALL_MARKED = {
    "tests/test_c.py::test_marked",
    "tests/test_c.py::TestGroup::test_in_class",
    "tests/test_c.py::test_lax_xfail",
    "tests/test_c.py::test_skipped",
    "tests/test_c.py::test_strict_xfail",
}


@pytest.fixture
def root(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_c.py").write_text(_CANARY_FILE, encoding="utf-8")
    (tmp_path / "tests" / "helper.py").write_text(
        "def test_x():\n    pass\n", encoding="utf-8"
    )
    return tmp_path


def _entry(status: str, *markers: str, heading: str = "### Gap") -> str:
    body = "\n".join(markers)
    return f"{heading}\n\n**Status (2026-11-05): {status}.** Prose.\n{body}\n"


def _page(*entries: str, link_all: bool = True) -> str:
    """A page; with *link_all*, a trailing POLICY entry is not added but every
    marked test in the synthetic tree is named by a final OPEN entry, so a
    case's only problem is the one it sets up."""
    parts = ["# Known gaps\n", *entries]
    if link_all:
        parts.append(
            _entry(
                "OPEN",
                *(
                    f"<!-- canary: {n} -->"
                    for n in sorted(
                        _ALL_MARKED
                        - {
                            "tests/test_c.py::test_lax_xfail",
                            "tests/test_c.py::test_skipped",
                        }
                    )
                ),
                heading="### Holder",
            )
        )
    return "\n".join(parts)


def _problems(text: str, root: Path) -> list[str]:
    # The holder links everything except the two broken canaries; drop the
    # orphan lines for those unless a case names them.
    return [
        p
        for p in kgc.find_problems(text, root)
        if not (
            p.startswith(
                ("tests/test_c.py::test_lax_xfail:", "tests/test_c.py::test_skipped:")
            )
        )
    ]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def test_parse_reads_every_status_shape_and_its_markers() -> None:
    text = "\n".join(
        [
            "# Page",
            "### A",
            "**Status (2026-10-01): OPEN.** x",
            "<!-- canary: tests/test_a.py::test_one -->",
            "<!-- canary: tests/test_a.py::test_two -->",
            "### B",
            "- [Linked entry](x.md)",
            "  **Status (re-verified 2026-10-09 at `abc`): PARTIAL.** y",
            "  <!-- no-canary: needs a Windows host to observe this -->",
            "## C",
            "**Status: NARROWED.** z",
            "<!-- no-canary: spans",
            "     two lines of reason -->",
        ]
    )
    entries, stray = kgc.parse_entries(text)
    assert stray == []
    assert [(e.line, e.title, e.status, e.canaries, e.no_canary) for e in entries] == [
        (
            3,
            "A",
            "OPEN",
            ["tests/test_a.py::test_one", "tests/test_a.py::test_two"],
            [],
        ),
        (8, "Linked entry", "PARTIAL", [], ["needs a Windows host to observe this"]),
        (11, "C", "NARROWED", [], ["spans two lines of reason"]),
    ]


def test_a_marker_after_a_heading_with_no_status_is_stray() -> None:
    text = "### A\n**Status: OPEN.** x\n### Section intro\n<!-- canary: tests/test_a.py::test_x -->\n"
    entries, stray = kgc.parse_entries(text)
    assert stray == [4]
    assert entries[0].canaries == []


def test_a_marker_quoted_in_prose_is_not_a_marker() -> None:
    text = (
        "Write `<!-- canary: tests/test_a.py::test_x -->` under the entry.\n"
        "### A\n**Status: OPEN.** see `<!-- no-canary: quoted reason here -->`\n"
    )
    entries, stray = kgc.parse_entries(text)
    assert stray == []
    assert (entries[0].canaries, entries[0].no_canary) == ([], [])


# ---------------------------------------------------------------------------
# Status rules
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["OPEN", "PARTIAL", "NARROWED"])
def test_an_open_entry_without_any_marker_fails(status: str, root: Path) -> None:
    problems = _problems(_page(_entry(status)), root)
    assert len(problems) == 1 and "has neither" in problems[0], problems


@pytest.mark.parametrize("status", ["CLOSED", "FIXED", "MOOT", "RESOLVED"])
def test_a_closed_entry_on_the_main_page_fails(status: str, root: Path) -> None:
    problems = _problems(
        _page(_entry(status, "<!-- no-canary: whatever reason is long -->")), root
    )
    assert len(problems) == 1 and "move the entry to archive" in problems[0], problems


@pytest.mark.parametrize("status", ["NEGATIVE_RESULT", "POLICY_NOTE"])
def test_negative_results_and_policy_notes_need_no_marker(
    status: str, root: Path
) -> None:
    assert _problems(_page(_entry(status)), root) == []


def test_an_unknown_status_keyword_fails(root: Path) -> None:
    problems = _problems(_page(_entry("PARTLY")), root)
    assert len(problems) == 1 and "unknown status 'PARTLY'" in problems[0], problems


# ---------------------------------------------------------------------------
# no-canary rules
# ---------------------------------------------------------------------------


def test_a_reasoned_exemption_passes(root: Path) -> None:
    page = _page(_entry("OPEN", "<!-- no-canary: needs MSVC cl.exe and a PDB -->"))
    assert _problems(page, root) == []


@pytest.mark.parametrize("reason", ["TBD", "todo", "needs work", ""])
def test_a_placeholder_exemption_fails(reason: str, root: Path) -> None:
    problems = _problems(_page(_entry("OPEN", f"<!-- no-canary: {reason} -->")), root)
    assert len(problems) == 1 and "placeholder" in problems[0], problems


def test_canary_and_exemption_together_fail(root: Path) -> None:
    page = _page(
        _entry(
            "OPEN",
            "<!-- canary: tests/test_c.py::test_marked -->",
            "<!-- no-canary: needs MSVC cl.exe and a PDB -->",
        )
    )
    problems = _problems(page, root)
    assert len(problems) == 1 and "both a canary and a no-canary" in problems[0], (
        problems
    )


# ---------------------------------------------------------------------------
# canary resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("node", "fragment"),
    [
        ("tests/test_c.py", "is not a"),
        ("tests/helper.py::test_x", "is not a"),
        ("tests/test_missing.py::test_x", "does not exist"),
        ("tests/test_c.py::test_nope", "does not define"),
        ("tests/test_c.py::TestNope::test_in_class", "class TestNope"),
        ("tests/test_c.py::test_unmarked", "lacks @pytest.mark.known_gap_canary"),
        ("tests/test_c.py::test_lax_xfail", "non-strict xfail"),
        ("tests/test_c.py::test_skipped", "unconditionally skipped"),
    ],
)
def test_a_bad_canary_reference_fails(node: str, fragment: str, root: Path) -> None:
    problems = _problems(_page(_entry("OPEN", f"<!-- canary: {node} -->")), root)
    assert len(problems) == 1 and fragment in problems[0], problems


@pytest.mark.parametrize(
    "node",
    [
        "tests/test_c.py::test_marked",
        "tests/test_c.py::TestGroup::test_in_class",
        "tests/test_c.py::test_strict_xfail",
    ],
)
def test_a_good_canary_reference_passes(node: str, root: Path) -> None:
    assert _problems(_page(_entry("OPEN", f"<!-- canary: {node} -->")), root) == []


def test_a_module_pytestmark_marks_every_test(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text(
        "import pytest\npytestmark = [pytest.mark.integration, pytest.mark.known_gap_canary]\n"
        "def test_a():\n    assert True\nclass TestB:\n    def test_b(self):\n        assert True\n",
        encoding="utf-8",
    )
    assert kgc.marked_tests(tmp_path) == {
        "tests/test_m.py::test_a",
        "tests/test_m.py::TestB::test_b",
    }
    test, error = kgc.resolve_canary("tests/test_m.py::TestB::test_b", tmp_path)
    assert error is None and test is not None and test.marked


def test_an_unnamed_marked_test_is_an_orphan(root: Path) -> None:
    page = _page(
        _entry("OPEN", "<!-- canary: tests/test_c.py::test_marked -->"), link_all=False
    )
    problems = _problems(page, root)
    assert all("no known-gaps.md entry names it" in p for p in problems), problems
    assert sorted(p.split(": carries")[0] for p in problems) == [
        "tests/test_c.py::TestGroup::test_in_class",
        "tests/test_c.py::test_strict_xfail",
    ]


def test_marked_tests_finds_functions_and_methods(root: Path) -> None:
    assert kgc.marked_tests(root) == _ALL_MARKED


# ---------------------------------------------------------------------------
# The real repository
# ---------------------------------------------------------------------------


def _real_page() -> str:
    return kgc.KNOWN_GAPS.read_text(encoding="utf-8")


@pytest.mark.repo_scan
def test_the_real_page_passes_the_gate() -> None:
    assert kgc.find_problems(_real_page(), REPO) == []


@pytest.mark.repo_scan
def test_the_real_page_links_every_open_entry() -> None:
    c = kgc.coverage(_real_page())
    assert c["with_canary"] + c["exempted"] == c["needs_link"]
    assert c["with_canary"] >= 19  # coverage may grow, never silently shrink


@pytest.mark.repo_scan
def test_every_named_canary_is_collected_by_pytest() -> None:
    entries, _ = kgc.parse_entries(_real_page())
    nodes = sorted({n for e in entries for n in e.canaries})
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "-p",
            "no:cacheprovider",
            *nodes,
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
    collected = {
        line.split("[")[0] for line in result.stdout.splitlines() if "::" in line
    }
    assert set(nodes) <= collected, sorted(set(nodes) - collected)
