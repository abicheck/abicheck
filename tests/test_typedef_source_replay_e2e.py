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

"""A removed or retargeted typedef is reported once, not once per tier.

Through the real CLI on compiled libraries. The header tier (``compare.typedefs``)
and the L4 source replay both observe the same typedef changes, with different
kinds (``typedef_removed`` vs ``public_typedef_removed``). Oracle, per cell (C
and C++), written out literally: the header-tier findings survive, each replay
twin is dropped and recorded as ``deduplicated``, the verdict and exit code stay
``BREAKING``/4, and the raw total still counts every observation (ADR-067). The
unchanged typedef ``u_t`` must produce no finding on either tier.
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
    "c": "#pragma once\ntypedef int old_t;\ntypedef int count_t;\n"
    "typedef unsigned u_t;\nint keep(int x);\n",
    "c++": "#pragma once\nnamespace demo {\ntypedef int old_t;\ntypedef int count_t;\n"
    "namespace inner {\ntypedef int deep_t;\n}\ntypedef unsigned u_t;\n}\n"
    "int keep(int x);\n",
}
_HEADER_V2 = {
    "c": "#pragma once\ntypedef long count_t;\ntypedef unsigned u_t;\n"
    "int keep(int x);\n",
    "c++": "#pragma once\nnamespace demo {\ntypedef long count_t;\n"
    "namespace inner {\n}\ntypedef unsigned u_t;\n}\nint keep(int x);\n",
}
# (header-tier kind, symbol) and (replay kind, symbol) that name one change.
_PAIRS = {
    "c": [
        ("typedef_removed", "old_t", "public_typedef_removed", "old_t"),
        ("typedef_base_changed", "count_t", "public_typedef_target_changed", "count_t"),
    ],
    "c++": [
        ("typedef_removed", "old_t", "public_typedef_removed", "demo::old_t"),
        ("typedef_removed", "deep_t", "public_typedef_removed", "demo::inner::deep_t"),
        (
            "typedef_base_changed",
            "count_t",
            "public_typedef_target_changed",
            "demo::count_t",
        ),
    ],
}


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
def test_typedef_change_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, language: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    old = _side(tmp_path, "v1", language, _HEADER_V1[language])
    new = _side(tmp_path, "v2", language, _HEADER_V2[language])
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
        "--contract",
        "all",
        "-o",
        "json=report.json",
    ]

    result = CliRunner().invoke(main, args)

    assert result.exit_code == 4, result.output
    report = json.loads(Path("report.json").read_text(encoding="utf-8"))
    assert report["verdict"] == "BREAKING"
    pairs = _PAIRS[language]
    kept = sorted((c["kind"], c["symbol"]) for c in report["changes"])
    # One finding per pair: the header-tier copy survives, the replay copy goes.
    assert kept == sorted((h, hs) for h, hs, _, _ in pairs), kept
    audit = report["disposition_audit"]
    assert audit["counts"]["deduplicated"] == len(pairs), audit["counts"]
    assert audit["detected_total"] == len(report["changes"]) + len(pairs)
    assert not any("u_t" in c["symbol"] for c in report["changes"])
