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

"""Every header-AST caller hands the frontend the same ``-I`` spelling.

The AST cache/acquisition key folds each include root's spelling in. The
binary dumps absolutized relative roots before this choke point, while the
header-only parse behind the release public surface did not, so the same
headers under the same (relative) root keyed apart: an MKL release ran a
second full castxml parse per side and kept a second DOM resident.

Invariant, over several spellings of one directory and both caller shapes:
the roots ``resolve_header_ast_result`` hands the parser equal
``absolutize_include_roots`` of what the caller passed -- so any two
relative spellings of one directory agree, and an absolute root is passed
through unchanged. The oracle is ``Path.resolve`` on the directory itself,
not the helper under test.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from abicheck.extract.headers.manifest import resolve_header_ast_result


class _Captured(Exception):
    pass


def _captured_includes(headers: list[Path], includes: list[Path]) -> list[Path]:
    seen: list[list[Path]] = []

    def parser(forced: list[Path], inc: list[Path], **_kw: object) -> object:
        seen.append(list(inc))
        raise _Captured

    with pytest.raises(_Captured):
        resolve_header_ast_result(
            dump_manifest=None,
            headers=headers,
            extra_includes=includes,
            header_ast_parser=parser,  # type: ignore[arg-type]
            backend="castxml",
            compiler="c++",
            gcc_path=None,
            gcc_prefix=None,
            gcc_options=None,
            gcc_option_tokens=(),
            sysroot=None,
            nostdinc=False,
            lang=None,
            exported_dynamic=set(),
            exported_static=set(),
            public_headers=None,
            public_header_dirs=None,
        )
    return seen[0]


@pytest.fixture
def tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    real = tmp_path / "opt" / "pkg" / "include"
    real.mkdir(parents=True)
    (real / "api.h").write_text("int f(void);\n")
    work = tmp_path / "work"
    work.mkdir()
    (work / "sub").mkdir()
    os.symlink(real, work / "include")  # the MKL layout: a symlinked root
    monkeypatch.chdir(work)
    return real


@pytest.mark.parametrize(
    "spelling",
    ["include", "./include", "sub/../include", "include/", "./sub/../include/."],
)
def test_relative_spellings_reach_the_parser_as_one_absolute_root(
    tree: Path, spelling: str
) -> None:
    got = _captured_includes([Path("include/api.h")], [Path(spelling)])
    assert got == [tree.resolve()]


def test_absolute_root_is_passed_through_unchanged(tree: Path) -> None:
    link = Path.cwd() / "include"  # absolute, but through the symlink
    assert _captured_includes([link / "api.h"], [link]) == [link]


def test_mixed_list_keeps_order_and_only_rewrites_relative(tree: Path) -> None:
    absolute = Path.cwd() / "sub"
    got = _captured_includes(
        [Path("include/api.h")], [Path("include"), absolute, Path("./sub")]
    )
    assert got == [tree.resolve(), absolute, absolute.resolve()]
