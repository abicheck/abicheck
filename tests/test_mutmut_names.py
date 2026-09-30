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

"""The whole-tree harness inventories read a mutmut-rewritten tree as its source.

Regression for the mutation lane aborting on PR #1433: under mutmut,
``_cached_bare_re`` became ``x__cached_bare_re__mutmut_1..N`` and the F4 site
scan reported every copy as a new, unregistered heuristic.
"""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path

import pytest
from _family_f4_inventory import _Visitor
from _family_f5_support import scan_optimization_sites
from _mutmut_names import canonical_def_name, is_mutmut_artifact


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("f", "f"),
        ("_cached_bare_re", "_cached_bare_re"),
        ("x__cached_bare_re__mutmut_orig", "_cached_bare_re"),
        ("x__cached_bare_re__mutmut_1", None),
        ("x__cached_bare_re__mutmut_42", None),
        ("x__cached_bare_re__mutmut_mutants", None),
        ("xǁCacheǁget__mutmut_orig", "get"),
        ("xǁCacheǁget__mutmut_7", None),
        ("x_value", "x_value"),
        ("xyz__mutmut", "xyz__mutmut"),
    ],
)
def test_canonical_def_name(name: str, expected: str | None) -> None:
    assert canonical_def_name(name) == expected
    assert is_mutmut_artifact(name) is (expected is None)


_SOURCE = """
    import functools, re
    _cache = {}

    @functools.lru_cache
    def _cached_bare_re(name):
        return re.compile(r"\\b" + re.escape(name) + r"\\b")
"""

# What mutmut 3 writes for the same module (trampoline + orig + mutants).
_MUTATED = """
    import functools, re
    _cache = {}
    x__cached_bare_re__mutmut_mutants = {}

    @functools.lru_cache
    def _cached_bare_re(name):
        return _mutmut_trampoline(x__cached_bare_re__mutmut_orig, x__cached_bare_re__mutmut_mutants, name)

    def x__cached_bare_re__mutmut_orig(name):
        return re.compile(r"\\b" + re.escape(name) + r"\\b")

    def x__cached_bare_re__mutmut_1(name):
        return re.compile(r"XX\\bXX" + re.escape(name) + r"\\b")

    def x__cached_bare_re__mutmut_2(name):
        return re.compile(None)
"""


def _f4_sites(src: str) -> set[str]:
    v = _Visitor("m")
    v.visit(ast.parse(textwrap.dedent(src)))
    return v.sites


def test_f4_inventory_is_invariant_under_mutmut_rewrite() -> None:
    pristine = _f4_sites(_SOURCE)
    assert pristine, "vacuity guard: the fixture must contain a heuristic site"
    assert _f4_sites(_MUTATED) == pristine


def _write_pkg(root: Path, src: str) -> Path:
    pkg = root / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "m.py").write_text(textwrap.dedent(src), encoding="utf-8")
    return pkg


def test_f5_inventory_is_invariant_under_mutmut_rewrite(tmp_path: Path) -> None:
    pristine = scan_optimization_sites(_write_pkg(tmp_path / "a", _SOURCE))
    mutated = scan_optimization_sites(_write_pkg(tmp_path / "b", _MUTATED))
    assert pristine, "vacuity guard: the fixture must contain an optimization site"
    assert mutated == pristine
