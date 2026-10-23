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

"""A header-only inline removed from the public headers is reported once.

Through the real CLI on compiled libraries: ``clamp_it`` is a header-only
inline in v1 and gone in v2. Both the header tier and the L4 source replay see
the removal. Oracle, per cell (C and C++): exactly one ``inline_function_removed``
finding, the verdict stays ``API_BREAK``, and the disposition ledger records the
replay copy as ``deduplicated`` while its raw total still counts both
(ADR-067, "record before disposing").
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

_HEADER_V1 = {
    "c": "#pragma once\ninline int clamp_it(int x) { return x < 0 ? 0 : x; }\n"
    "int keep(int x);\n",
    "c++": "#pragma once\nnamespace demo {\n"
    "inline int clamp_it(int x) { return x < 0 ? 0 : x; }\n}\nint keep(int x);\n",
}
_HEADER_V2 = "#pragma once\nint keep(int x);\n"


def _side(root: Path, version: str, language: str, header: str) -> Path:
    side = root / version
    side.mkdir()
    (side / "widget.h").write_text(header, encoding="utf-8")
    ext = "c" if language == "c" else "cpp"
    source = side / f"widget.{ext}"
    source.write_text(
        '#include "widget.h"\nint keep(int x) { return x + 1; }\n', encoding="utf-8"
    )
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
def test_header_only_inline_removal_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    old = _side(tmp_path, "v1", language, _HEADER_V1[language])
    new = _side(tmp_path, "v2", language, _HEADER_V2)
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

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 2, result.output
    report = json.loads(Path("report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "API_BREAK"
    removed = [c for c in report["changes"] if c["kind"] == "inline_function_removed"]
    assert len(removed) == 1, [c["symbol"] for c in removed]
    # The ledger keeps the replay copy: one deduplicated, and the raw total
    # still counts both observations.
    audit = report["disposition_audit"]
    assert audit["counts"]["deduplicated"] == 1, audit["counts"]
    assert audit["detected_total"] == len(report["changes"]) + 1
