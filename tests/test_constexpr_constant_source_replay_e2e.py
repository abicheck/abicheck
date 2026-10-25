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

"""A namespace-scope constant whose value changed is reported once.

Through the real CLI on compiled libraries. The header tier reports the value
change as ``CONSTANT_CHANGED``; the L4 source replay also reports a C++
``constexpr`` variable as ``CONSTEXPR_VALUE_CHANGED``. Oracle, per cell: the
header-tier finding survives, the replay copy is recorded as ``deduplicated``,
the raw total still counts both observations, and the verdict and exit code do
not move (ADR-067, "record before disposing"). Plain ``const`` and C never
reach the replay kind, so they stay single findings.
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

# Each form declares one constant and a public inline that reads it, so the
# constant is reachable from the public surface on both tiers.
_FORMS: dict[str, tuple[str, str, str]] = {
    # name: (language, declaration template, qualified symbol)
    "c-static-const": (
        "c",
        "static const int kLimit = {v};\n"
        "static inline int cap(int x) {{ return x < kLimit ? x : kLimit; }}\n",
        "kLimit",
    ),
    "cxx-constexpr-namespace": (
        "c++",
        "namespace demo {{\n"
        "constexpr int kLimit = {v};\n"
        "inline int cap(int x) {{ return x < kLimit ? x : kLimit; }}\n}}\n",
        "demo::kLimit",
    ),
    "cxx-constexpr-nested-namespace": (
        "c++",
        "namespace outer {{ namespace demo {{\n"
        "constexpr int kLimit = {v};\n"
        "inline int cap(int x) {{ return x < kLimit ? x : kLimit; }}\n"
        "}} }}\n",
        "outer::demo::kLimit",
    ),
    "cxx-const-namespace": (
        "c++",
        "namespace demo {{\n"
        "const int kLimit = {v};\n"
        "inline int cap(int x) {{ return x < kLimit ? x : kLimit; }}\n}}\n",
        "demo::kLimit",
    ),
}


def _side(root: Path, version: str, language: str, body: str) -> Path:
    side = root / version
    side.mkdir()
    (side / "widget.h").write_text(
        "#pragma once\n" + body + "int keep(int x);\n", encoding="utf-8"
    )
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


def _compare(tmp_path: Path, form: str, old_value: int, new_value: int) -> dict:
    language, template, _ = _FORMS[form]
    old = _side(tmp_path, "v1", language, template.format(v=old_value))
    new = _side(tmp_path, "v2", language, template.format(v=new_value))
    report = tmp_path / "report.json"
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
        f"json={report}",
    ]
    result = CliRunner().invoke(main, args)
    assert result.exit_code in (0, 2), result.output
    data = json.loads(report.read_text(encoding="utf-8"))
    data["_exit_code"] = result.exit_code
    return data


def _kinds(report: dict, kind: str) -> list[str]:
    return [c["symbol"] for c in report["changes"] if c["kind"] == kind]


@pytest.mark.parametrize(
    "form",
    ["c-static-const", "cxx-constexpr-namespace", "cxx-constexpr-nested-namespace"],
)
def test_constant_value_change_is_reported_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, form: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    report = _compare(tmp_path, form, 10, 20)
    qualified = _FORMS[form][2]

    assert report["verdict"] == "API_BREAK"
    assert report["_exit_code"] == 2
    assert _kinds(report, "constant_changed") == [qualified]
    # Only C++ constexpr reaches the replay kind; for the other forms there is
    # nothing to drop, so the ledger counts no deduplication.
    replay_copies = _kinds(report, "constexpr_value_changed")
    assert replay_copies == []
    audit = report["disposition_audit"]
    expected_dedup = 1 if form.startswith("cxx-constexpr") else 0
    assert audit["counts"]["deduplicated"] == expected_dedup, audit["counts"]


def test_plain_const_is_reported_once_and_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A plain ``const`` is not a C++ ``constexpr`` variable, so the L4 replay
    # never emits CONSTEXPR_VALUE_CHANGED for it. The header tier's finding is
    # the only one, and nothing is deduplicated.
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    report = _compare(tmp_path, "cxx-const-namespace", 10, 20)

    assert _kinds(report, "constant_changed") == ["demo::kLimit"]
    assert _kinds(report, "constexpr_value_changed") == []
    assert report["disposition_audit"]["counts"]["deduplicated"] == 0


def test_unchanged_constexpr_value_reports_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ABICHECK_AST_FRONTEND", raising=False)
    report = _compare(tmp_path, "cxx-constexpr-namespace", 10, 10)

    assert report["changes"] == []
    assert report["_exit_code"] == 0
    assert report["disposition_audit"]["counts"]["deduplicated"] == 0
