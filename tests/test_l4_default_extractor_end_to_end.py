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

"""``compare --sources … --depth source`` sees a public macro change on C and C++.

Through the real CLI, on real compiled libraries: the ``evidence-depth``
workflow example's ``BUFFER_LIMIT`` change (16 → 32), as a C tree and as a
C++ tree, under the default frontend and under an explicit castxml.

Oracle, written out per cell: L4 under the default frontend replays with
clang, which records macros, so ``public_macro_value_changed`` is reported
on both languages; an explicit castxml frontend stays castxml, which records
no macros, so the change is not reported -- but its C unit now parses
(castxml's C emulation) instead of failing the replay. Before the fix the
default frontend replayed with castxml: the C++ tree missed the macro and
the C tree's replay failed outright.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from abicheck.cli import main

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform != "linux", reason="builds an ELF .so"),
    pytest.mark.skipif(
        any(shutil.which(t) is None for t in ("gcc", "g++", "castxml", "clang")),
        reason="needs gcc, g++, castxml and clang",
    ),
]

_EXAMPLE = Path(__file__).resolve().parents[1] / "examples/workflows/evidence-depth"


def _side(root: Path, version: str, language: str) -> Path:
    side = root / version
    side.mkdir()
    shutil.copy(_EXAMPLE / version / "widget.h", side / "widget.h")
    source = side / ("widget.c" if language == "c" else "widget.cpp")
    shutil.copy(_EXAMPLE / version / "widget.c", source)
    driver = "gcc" if language == "c" else "g++"
    subprocess.run(
        [
            driver,
            "-shared",
            "-fPIC",
            f"-I{side}",
            str(source),
            "-o",
            str(side / "libwidget.so"),
        ],
        check=True,
    )
    (side / "compile_commands.json").write_text(
        json.dumps(
            [
                {
                    "directory": str(side),
                    "arguments": [
                        driver,
                        f"-I{side}",
                        "-c",
                        source.name,
                        "-o",
                        "widget.o",
                    ],
                    "file": str(source),
                }
            ]
        ),
        encoding="utf-8",
    )
    return side


@pytest.mark.parametrize("language", ["c", "c++"])
@pytest.mark.parametrize("frontend", [None, "castxml"])
def test_depth_source_reports_the_macro_change_iff_l4_runs_clang(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    language: str,
    frontend: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    old = _side(tmp_path, "v1", language)
    new = _side(tmp_path, "v2", language)
    if frontend is not None:
        (tmp_path / "project.yml").write_text(
            f"version: 3\ncompile:\n  frontend: {frontend}\n", encoding="utf-8"
        )
    args = [
        "compare",
        str(old / "libwidget.so"),
        str(new / "libwidget.so"),
        "--header",
        f"old={old / 'widget.h'}",
        "--header",
        f"new={new / 'widget.h'}",
        "--sources",
        f"old={old}",
        "--sources",
        f"new={new}",
        "--build-info",
        f"old={old / 'compile_commands.json'}",
        "--build-info",
        f"new={new / 'compile_commands.json'}",
        "--depth",
        "source",
        "-o",
        "json=report.json",
    ]
    if frontend is not None:
        args += ["--config", str(tmp_path / "project.yml")]

    result = CliRunner().invoke(main, args)

    out = result.output + (result.stderr or "")
    # The layout break gates on every cell, whatever L4 sees.
    assert result.exit_code == 4, out
    assert "L4 source ABI replay       present" in out, out
    assert "extractor failures" not in out, out
    kinds = {c["kind"] for c in json.loads(Path("report.json").read_text())["changes"]}
    assert ("public_macro_value_changed" in kinds) is (frontend is None), kinds
