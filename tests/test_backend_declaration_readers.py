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
"""ADR-063 6B closure: the checker reads no backend-specific declaration
representation beyond a shrink-only baseline.

The scanner is tested on synthetic sources for every read shape it must see
(attribute, ``getattr`` through a plain name, a local alias and an aliased
``builtins`` module) and every shape it must not (a local variable, a string
elsewhere), with the enclosing qualname as the oracle. The baseline
comparison is tested on hand-built dicts, independent of the live tree.
"""

from __future__ import annotations

import ast
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from semantic_ir_cutover import (  # noqa: E402
    KNOWN_BACKEND_DECLARATION_READERS,
    backend_declaration_problems,
    backend_declaration_reads,
    current_backend_declaration_readers,
)


def _reads(src: str) -> dict[tuple[str, str], int]:
    return backend_declaration_reads(ast.parse(textwrap.dedent(src)))


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("def f(s):\n    return s.dwarf\n", {("f", "dwarf"): 1}),
        ("def f(s):\n    return s.dwarf_advanced.x\n", {("f", "dwarf_advanced"): 1}),
        ("def f(s):\n    return getattr(s, 'dwarf', None)\n", {("f", "dwarf"): 1}),
        (
            "g = getattr\ndef f(s):\n    return g(s, 'dwarf_advanced')\n",
            {("f", "dwarf_advanced"): 1},
        ),
        (
            "import builtins as b\ndef f(s):\n    return b.getattr(s, 'dwarf')\n",
            {("f", "dwarf"): 1},
        ),
        (
            "class C:\n    def m(self, s):\n        return s.dwarf, s.dwarf\n",
            {("C.m", "dwarf"): 2},
        ),
        ("x = snap.dwarf\n", {("<module>", "dwarf"): 1}),
        ("def f(dwarf):\n    return dwarf.structs\n", {}),
        ("def f(s):\n    s.dwarf = None\n", {}),
        ("def f(s):\n    del s.dwarf\n", {}),
        ("def f(s, v):\n    s.dwarf += v\n", {("f", "dwarf"): 1}),
        ("def f(s):\n    return s.declarations.types, 'dwarf'\n", {}),
        ("def f(s):\n    return getattr(s, 'elf')\n", {}),
    ],
)
def test_scanner_sees_every_read_shape_and_only_those(
    src: str, expected: dict[tuple[str, str], int]
) -> None:
    assert _reads(src) == expected


_KEY = ("abicheck/diff_x.py", "f", "dwarf")


@pytest.mark.parametrize(
    ("current", "baseline", "needles"),
    [
        ({_KEY: 1}, {_KEY: 1}, []),
        ({}, {}, []),
        ({_KEY: 2}, {_KEY: 1}, ["reads `AbiSnapshot.dwarf` 2 time(s) (baseline 1)"]),
        ({_KEY: 1}, {}, ["(baseline 0)"]),
        ({}, {_KEY: 1}, ["lower the baseline"]),
        ({_KEY: 1}, {_KEY: 3}, ["lower the baseline"]),
    ],
)
def test_baseline_only_shrinks(
    current: dict[tuple[str, str, str], int],
    baseline: dict[tuple[str, str, str], int],
    needles: list[str],
) -> None:
    problems = backend_declaration_problems(current, baseline)
    assert len(problems) == len(needles)
    for problem, needle in zip(problems, needles):
        assert needle in problem


@pytest.mark.repo_scan
def test_live_checker_matches_the_baseline() -> None:
    current = current_backend_declaration_readers()
    assert (
        backend_declaration_problems(current, KNOWN_BACKEND_DECLARATION_READERS) == []
    )
    # Vacuity guard: the scan reached the DWARF-layout detector it must see.
    assert any(rel == "abicheck/diff_platform.py" for rel, _, _ in current)


@pytest.mark.parametrize(
    ("qualname", "expected"),
    [
        ("f", "f"),
        ("C.m", "C.m"),
        ("x__has_type_evidence__mutmut_orig", "_has_type_evidence"),
        ("x_public__mutmut_orig", "public"),
        ("x__has_type_evidence__mutmut_7", None),
        ("C.xǁCǁm__mutmut_orig", "C.m"),
        ("C.xǁCǁ_m__mutmut_12", None),
        ("x_a__b__mutmut_orig", "a__b"),
    ],
)
def test_a_mutmut_tree_reports_the_source_reader_not_its_copies(
    qualname: str, expected: str | None
) -> None:
    """Run from mutmut's `mutants/` tree, a function's body exists as its
    `_mutmut_orig` copy plus one numbered copy per mutant; the gate must see
    only the original, under its source name, so every cutover test that runs
    the whole gate keeps passing there."""
    from semantic_ir_cutover import _unmangled_qualname

    assert _unmangled_qualname(qualname) == expected


def test_copies_of_one_source_function_count_as_one_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A mutmut tree can hold a function both under its own name and as its
    `_mutmut_orig` copy; the gate must report the source count, not a sum."""
    import semantic_ir_cutover as gate

    mod = tmp_path / "abicheck" / "diff_x.py"
    mod.parent.mkdir()
    body = "    return snap.dwarf\n"
    mod.write_text(
        "def _f(snap):\n"
        + body
        + "def x__f__mutmut_orig(snap):\n"
        + body
        + "def x__f__mutmut_1(snap):\n"
        + body,
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gate, "_checker_modules", lambda: [mod])
    assert gate.current_backend_declaration_readers() == {
        ("abicheck/diff_x.py", "_f", "dwarf"): 1
    }
