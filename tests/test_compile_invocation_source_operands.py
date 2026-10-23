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
"""Multi-source corpus for the compile-invocation model (plan l4-l2 Phase 3 slice 4).

A compile-database entry can name several sources, switch ``-x`` between them,
link objects and archives, or stop after ``-E``/``-S``/``-c``. The corpus in
``multi_source_compile_commands.json`` covers those shapes.

Two pins live here:

* **Consumers are unchanged.** The clang argv golden was recorded from the
  code before :class:`~abicheck.buildsource.source_extractors.compile_invocation.CompilerInvocation`
  gained its operand fields. The renderers never read those fields, so every
  rendered argv must stay byte-identical.
* **The operands match a hand-written oracle.** The expectations below are
  written from the gcc driver's documented behaviour, not copied from the
  implementation, so a parser bug shows up as a disagreement.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from test_compile_invocation_differential import _render_unit

from abicheck.buildsource.adapters.compile_db import CompileDbAdapter
from abicheck.buildsource.source_extractors.compile_action import (
    CompileAction,
    SourceOperand,
    parse_gnu_operands,
)
from abicheck.buildsource.source_extractors.compile_invocation import (
    parse_compile_invocation,
)

_DATA = Path(__file__).parent / "data" / "compile_invocation"
_CORPUS = _DATA / "multi_source_compile_commands.json"
_GOLDEN = _DATA / "multi_source_clang_argv_golden.json"

C = CompileAction


def _render_corpus() -> dict[str, dict[str, Any]]:
    evidence = CompileDbAdapter(_CORPUS).collect()
    return {cu.source: _render_unit(cu) for cu in evidence.compile_units}


def test_consumer_argv_matches_pre_change_golden() -> None:
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    assert _render_corpus() == golden


# (entry source, action, [(operand, language, effective output)], link inputs)
_EXPECTED: list[tuple[str, CompileAction, list[tuple[str, str, str]], list[str]]] = [
    (
        "/work/a.c",
        C.COMPILE,
        [("a.c", "c", "a.o"), ("b.c", "c", "b.o")],
        [],
    ),
    (
        # ``-x c++`` applies to a.h, ``-x none`` restores extension lookup for b.c;
        # the link output is shared by both operands.
        "/work/a.h",
        C.LINK,
        [("a.h", "c++", "prog"), ("b.c", "c", "prog")],
        [],
    ),
    ("/work/c.c", C.COMPILE, [("c.c", "c", "c.o")], []),
    (
        "/work/d.c",
        C.LINK,
        [("d.c", "c", "d.so")],
        ["c.o", "lib.a"],
    ),
    ("/work/e.cpp", C.COMPILE, [("e.cpp", "c", "e.o")], []),
    ("/work/f.c", C.PREPROCESS, [("f.c", "c", "f.i")], []),
    ("/work/g.c", C.ASSEMBLE, [("g.c", "c", "g.s")], []),
    (
        "/work/h1.cpp",
        C.COMPILE,
        [("h1.cpp", "c++", "h1.o"), ("h2.cc", "c++", "h2.o")],
        [],
    ),
    ("/work/main.c", C.LINK, [("main.c", "c", "app")], ["util.o", "libfoo.a"]),
    ("/work/k.S", C.COMPILE, [("k.S", "assembler-with-cpp", "k.o")], []),
    (
        "/work/stdio_like.h",
        C.COMPILE,
        [("stdio_like.h", "c-header", "stdio_like.h.gch")],
        [],
    ),
    ("/work/script_src", C.COMPILE, [("script_src", "c", "script.o")], []),
    ("/work/m.mm", C.COMPILE, [("m.mm", "objective-c++", "m.o")], []),
    # ``-include pre.h`` consumes its operand: pre.h is not a source or input.
    ("/work/n.cpp", C.COMPILE, [("n.cpp", "c++", "n.o")], []),
    # ``-MF o.d`` likewise consumes its operand.
    ("/work/o.c", C.COMPILE, [("o.c", "c", "o.o")], []),
    (
        "/work/p.cpp",
        C.COMPILE,
        [("p.cpp", "c++", "p.o"), ("p2.cpp", "c++", "p2.o")],
        [],
    ),
]


@pytest.mark.parametrize(
    ("source", "action", "operands", "link_inputs"),
    _EXPECTED,
    ids=[row[0].rsplit("/", 1)[-1] for row in _EXPECTED],
)
def test_source_operands_match_hand_written_oracle(
    source: str,
    action: CompileAction,
    operands: list[tuple[str, str, str]],
    link_inputs: list[str],
) -> None:
    evidence = CompileDbAdapter(_CORPUS).collect()
    cu = next(unit for unit in evidence.compile_units if unit.source == source)
    inv = parse_compile_invocation(cu)
    assert inv.action is action
    assert [
        (op.source, op.language, op.effective_output) for op in inv.operands
    ] == operands
    assert list(inv.link_inputs) == link_inputs
    assert all(op.action is action for op in inv.operands)


def test_every_corpus_unit_has_an_action_and_sources_track_it() -> None:
    evidence = CompileDbAdapter(_CORPUS).collect()
    for cu in evidence.compile_units:
        inv = parse_compile_invocation(cu)
        assert inv.action is not None
        assert inv.operands, cu.source
        # Link inputs only exist under a link, and never repeat a source.
        if inv.action is not C.LINK:
            assert inv.link_inputs == ()
        assert not {op.source for op in inv.operands} & set(inv.link_inputs)


def test_msvc_driver_reports_no_operand_parse() -> None:
    from abicheck.buildsource.build_evidence import CompileUnit

    cu = CompileUnit(
        id="cu://a.cpp",
        source="/work/a.cpp",
        directory="/work",
        argv=["clang-cl", "/c", "a.cpp", "/Fo:a.obj"],
        language="CXX",
    )
    inv = parse_compile_invocation(cu)
    assert inv.action is None
    assert inv.operands == ()
    assert inv.link_inputs == ()


@pytest.mark.parametrize(
    ("tail", "expected"),
    [
        pytest.param(
            ["-c", "a.c", "-x", "none", "-x", "c++", "b.txt"],
            [("a.c", "c", "a.o"), ("b.txt", "c++", "b.o")],
            id="forced-language-applies-to-extensionless-and-unknown",
        ),
        pytest.param(
            ["-c", "x.tar.c"],
            [("x.tar.c", "c", "x.tar.o")],
            id="default-output-keeps-inner-dots",
        ),
        pytest.param(
            ["-c", "a.c", "b.c", "-o", "ab.o"],
            [("a.c", "c", "a.o"), ("b.c", "c", "b.o")],
            id="o-ignored-when-several-compile-sources",
        ),
        pytest.param(
            ["-E", "a.c", "-o", "a.i", "-Iinc"],
            [("a.c", "c", "a.i")],
            id="preprocess-with-o-single-source",
        ),
        pytest.param(
            ["-E", "a.c", "b.c"],
            [("a.c", "c", ""), ("b.c", "c", "")],
            id="preprocess-to-stdout-has-no-output",
        ),
        pytest.param(
            ["a.c", "-o", "app", "-lm", "-Wl,--as-needed", "libx.so"],
            [("a.c", "c", "app")],
            id="link-ignores-l-and-wl-and-keeps-shared-library-input",
        ),
    ],
)
def test_parse_gnu_operands_leaf_rules(
    tail: list[str], expected: list[tuple[str, str, str]]
) -> None:
    parsed = parse_gnu_operands(tail)
    assert [(o.source, o.language, o.effective_output) for o in parsed.operands] == (
        expected
    )


def test_link_inputs_keep_response_file_as_visible_gap() -> None:
    # Response files are not expanded: the token is reported, not dropped.
    parsed = parse_gnu_operands(["a.c", "@objs.rsp", "-o", "app"])
    assert parsed.action is C.LINK
    assert parsed.link_inputs == ("@objs.rsp",)


def test_source_operand_is_a_frozen_value() -> None:
    op = SourceOperand(
        source="a.c", language="c", action=C.COMPILE, effective_output="a.o"
    )
    with pytest.raises(AttributeError):
        op.source = "b.c"  # type: ignore[misc]
