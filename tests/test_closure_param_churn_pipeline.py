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

"""Closure-parameterized non-constructor functions must not churn on a lambda
line shift (known-gaps: closure-parameterized-non-ctor-functions-still-churn-func).

Each case builds a real shared library with g++ from a header that declares a
template instantiated over a local lambda closure, dumps it through the CLI
and compares the two snapshots through the same loader users reach
(``serialization.load_snapshot``), so the castxml spelling, the ordinal
renumbering on dump/load and the function-level detectors are all exercised
together -- not a hand-built ``AbiSnapshot`` with hand-written spellings.

Oracle: the ``ChangeKind`` set itself (the change's own kind), not the
implementation's helpers. The same lambda body at shifted line numbers must
produce no ``FUNC_PARAMS_CHANGED``/``TEMPLATE_PARAM_TYPE_CHANGED``; a real
parameter type change on the same extern-"C" symbol must still produce
``FUNC_PARAMS_CHANGED``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.checker import compare
from abicheck.checker_policy import ChangeKind
from abicheck.cli import main
from abicheck.serialization import load_snapshot

pytestmark = [pytest.mark.integration]

_PARAM_KINDS = {
    ChangeKind.FUNC_PARAMS_CHANGED,
    ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
}

# A lambda-parameterized instantiation, reached the way oneTBB's
# ``raii_guard<(lambda:task_group.h:...)>`` is: the closure is spelled inside a
# function template body, and a non-template public function takes the
# instantiation by value. ``extern "C"`` keeps the exported symbol name fixed
# across both sides, so only the parameter type can differ.
_GUARD_HEADER = """\
#pragma once
template <class F> struct raii_guard {{ F f; }};
namespace tbb_like {{
template <class T> struct arena_fn {{
  static auto make() {{ return raii_guard<decltype([] {{}})>{{}}; }}
}};
}}
inline auto make_guard() {{ return tbb_like::arena_fn<int>::make(); }}
{decl}
"""

_LAMBDA_DECL = 'extern "C" void run_task(decltype(make_guard()) g);'
_INT_DECL = 'extern "C" void run_task(int g);'


def _need_toolchain() -> None:
    if shutil.which("g++") is None:
        pytest.skip("needs g++")
    if shutil.which("castxml") is None:
        pytest.skip("needs castxml")


_LAMBDA_IMPL = 'extern "C" void run_task(decltype(make_guard()) g) {}'
_INT_IMPL = 'extern "C" void run_task(int g) {}'


def _build_and_dump(root: Path, name: str, header: str, impl: str, out: Path) -> Path:
    """Write *header* and its *impl* source, build a shared library in its
    own directory, dump it through ``abicheck dump`` and return the snapshot
    path."""
    case = root / name
    case.mkdir()
    (case / "lib.h").write_text(header)
    (case / "lib.cpp").write_text(f'#include "lib.h"\n{impl}\n')
    so = case / "libt.so"
    subprocess.run(
        [
            "g++",
            "-std=c++20",
            "-shared",
            "-fPIC",
            "-I",
            str(case),
            "-o",
            str(so),
            str(case / "lib.cpp"),
        ],
        check=True,
    )
    result = CliRunner().invoke(
        main,
        ["dump", str(so), "-H", str(case / "lib.h"), "--label", name, "-o", str(out)],
    )
    assert result.exit_code == 0, result.output
    return out


def _param_kinds(old: Path, new: Path) -> set[ChangeKind]:
    result = compare(load_snapshot(old), load_snapshot(new))
    return {c.kind for c in result.changes}


@pytest.fixture(scope="module")
def guard_dumps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    _need_toolchain()
    root = tmp_path_factory.mktemp("closure_param_churn")
    base = _GUARD_HEADER.format(decl=_LAMBDA_DECL)
    shapes = {
        "at_line_6": (base, _LAMBDA_IMPL),
        "shifted_by_2": ("// unrelated edit\n// more\n" + base, _LAMBDA_IMPL),
        "shifted_by_40": ("\n" * 40 + base, _LAMBDA_IMPL),
        "int_param": (_GUARD_HEADER.format(decl=_INT_DECL), _INT_IMPL),
    }
    return {
        name: _build_and_dump(root, name, header, impl, root / f"{name}.json")
        for name, (header, impl) in shapes.items()
    }


class TestLambdaLineShiftIsNoFinding:
    @pytest.mark.parametrize("shifted", ["shifted_by_2", "shifted_by_40"])
    def test_same_lambda_body_at_shifted_lines_has_no_param_finding(
        self, guard_dumps: dict[str, Path], shifted: str
    ) -> None:
        kinds = _param_kinds(guard_dumps["at_line_6"], guard_dumps[shifted])
        assert not (kinds & _PARAM_KINDS), kinds


class TestRealParamTypeChangeIsStillReported:
    def test_lambda_parameter_replaced_by_int_is_reported(
        self, guard_dumps: dict[str, Path]
    ) -> None:
        kinds = _param_kinds(guard_dumps["at_line_6"], guard_dumps["int_param"])
        assert ChangeKind.FUNC_PARAMS_CHANGED in kinds, kinds


# ---------------------------------------------------------------------------
# Insertion case: an unrelated closure added to the SAME header, outside the
# function that spells the guarded closure. Ordinals are per header and per
# marker kind, so the guarded closure's ordinal moves from #1 to #2 on the new
# side -- the spelling changes, but the linker symbol does not (the closure's
# Itanium encoding is scoped to ``arena_fn<int>::make()``, not the header).
# ---------------------------------------------------------------------------

_CPP_HEADER = """\
#pragma once
template <class F> struct raii_guard {{ F f; }};
{pre}
namespace tbb_like {{
template <class T> struct arena_fn {{
  static auto make() {{ {make_body} }}
}};
}}
inline auto make_guard() {{ return tbb_like::arena_fn<int>::make(); }}
void run_task(decltype(make_guard()) g);
"""

_PLAIN_MAKE = "return raii_guard<decltype([] {})>{};"
_EXTRA_BEFORE_MAKE = (
    "\ninline auto unrelated_guard() {{ return raii_guard<decltype([] {{}})>{{}}; }}\n"
)
_TWO_EXTRA_BEFORE_MAKE = _EXTRA_BEFORE_MAKE + (
    "inline auto unrelated_guard2() {{ return raii_guard<decltype([] {{}})>{{}}; }}\n"
)
_CPP_IMPL = "void run_task(decltype(make_guard()) g) {}"

_CPP_PARAM_KINDS = {
    ChangeKind.FUNC_PARAMS_CHANGED,
    ChangeKind.TEMPLATE_PARAM_TYPE_CHANGED,
}


def _cpp_header(pre: str = "", make_body: str = _PLAIN_MAKE) -> str:
    return _CPP_HEADER.format(pre=pre, make_body=make_body)


@pytest.fixture(scope="module")
def insertion_dumps(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    _need_toolchain()
    root = tmp_path_factory.mktemp("closure_insertion")
    shapes = {
        "base": (_cpp_header(), _CPP_IMPL),
        "one_unrelated_above": (_cpp_header(pre=_EXTRA_BEFORE_MAKE), _CPP_IMPL),
        "two_unrelated_above": (_cpp_header(pre=_TWO_EXTRA_BEFORE_MAKE), _CPP_IMPL),
        # A lambda added INSIDE make(), before the guarded one, changes the
        # closure's own Itanium encoding (Ul...E_ -> Ul...E0_): a real linker
        # symbol change, which must stay a removal/addition.
        "extra_inside_make": (
            _cpp_header(
                make_body="auto unused = [] {}; " + _PLAIN_MAKE,
            ),
            _CPP_IMPL,
        ),
    }
    return {
        name: _build_and_dump(root, name, header, impl, root / f"{name}.json")
        for name, (header, impl) in shapes.items()
    }


class TestUnrelatedClosureInsertedAboveIsNoFinding:
    @pytest.mark.parametrize("shifted", ["one_unrelated_above", "two_unrelated_above"])
    def test_inserted_closure_above_does_not_churn_the_exported_function(
        self, insertion_dumps: dict[str, Path], shifted: str
    ) -> None:
        result = compare(
            load_snapshot(insertion_dumps["base"]),
            load_snapshot(insertion_dumps[shifted]),
        )
        churned = [
            (c.kind, c.symbol)
            for c in result.changes
            if c.kind in _CPP_PARAM_KINDS and c.symbol and "run_task" in c.symbol
        ]
        assert not churned, churned


class TestClosureInsideMakeIsStillAReportedBreak:
    def test_removed_exported_key_is_reported_when_the_closure_encoding_changes(
        self, insertion_dumps: dict[str, Path]
    ) -> None:
        old = load_snapshot(insertion_dumps["base"])
        new = load_snapshot(insertion_dumps["extra_inside_make"])
        old_key = next(
            f.mangled for f in old.declarations.functions if f.name == "run_task"
        )
        result = compare(old, new)
        removed = [c for c in result.changes if c.kind == ChangeKind.FUNC_REMOVED]
        assert any(c.symbol == old_key for c in removed), [c.symbol for c in removed]
