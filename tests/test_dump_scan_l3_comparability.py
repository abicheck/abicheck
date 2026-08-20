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

"""End-to-end regression coverage for the "dump-vs-scan compile-context
divergence" bug report (a real ``abicheck dump`` baseline compared against
an unchanged codebase via ``abicheck scan --against`` reported
``NOT_COMPARABLE``/``profile_fingerprint`` mismatch, because ``dump``'s own
CLI path did not fold real L3 build evidence into its L2 header-AST parse
the way ``compare``'s implicit-dump operand and ``scan``'s candidate
resolution already did).

AGENTS.md's "Known gaps" section documents this class of bug at length (the
"The native ELF `abicheck dump` path never applies L3 build context to its
own L2 header parse" entry) and records it closed via the P0.3 L3->L2 fold
(``buildsource/l2_seed.py``'s ``seed_includes_and_fold_compile_context``),
wired into both ``perform_elf_dump`` and ``handle_non_elf_dump``. The
existing regression coverage for that fold
(``tests/test_cli_dump_helpers_coverage.py::
test_perform_elf_dump_folds_l3_compile_context_into_header_parse`` and
siblings) verifies the mechanism with a stubbed ``dump()`` call, at the unit
level. Per this file's own "Third-party-boundary tests must exercise the
real public API at realistic scale" convention, this module adds the
missing piece: a real ``abicheck dump`` CLI invocation, over a real
g++-compiled library and a real ``compile_commands.json``, whose JSON
output is then fed to a real ``abicheck scan --against`` CLI invocation on
the same (unchanged) sources -- confirming end to end that the reported
symptom (``ast_resolved_standard``/``ast_compile_args`` empty on the
``dump`` side, ``NOT_COMPARABLE`` on the ``scan`` side) does not reproduce
on the current fold, rather than trusting the mocked unit coverage alone.

Deliberately *not* marked ``integration`` (that marker's Linux gate
requires castxml) -- ``--ast-frontend clang`` is used explicitly, mirroring
``test_clang_header_backend_integration.py``'s own self-skipping
convention, since the point is a real clang + g++ toolchain, not castxml
specifically.
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

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith("linux"),
    reason="ELF/Linux-scoped repro (real g++-compiled .so + compile_commands.json)",
)

_HAVE_GXX = shutil.which("g++") is not None
_HAVE_CLANG = shutil.which("clang") is not None


def _build_library(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Compile a tiny real C++17 library + a matching compile_commands.json.

    Mirrors the bug report's repro shape: a library genuinely built with an
    explicit ``-std=`` the header itself depends on (so a dump that failed
    to fold the real standard would produce headers parsed under the wrong
    dialect, not merely empty metadata).
    """
    header = tmp_path / "widget.h"
    header.write_text(
        "#pragma once\n"
        "#if __cplusplus < 201703L\n"
        '#error "needs c++17"\n'
        "#endif\n"
        "struct Widget {\n"
        "    int x;\n"
        "    int y;\n"
        "    int sum() const { return x + y; }\n"
        "};\n",
        encoding="utf-8",
    )
    src = tmp_path / "widget.cpp"
    src.write_text(
        '#include "widget.h"\nint compute(const Widget& w) { return w.sum(); }\n',
        encoding="utf-8",
    )
    so_path = tmp_path / "libwidget.so"
    subprocess.run(
        ["g++", "-std=c++17", "-shared", "-fPIC", "-o", str(so_path), str(src)],
        check=True,
        capture_output=True,
    )
    compile_db = tmp_path / "compile_commands.json"
    compile_db.write_text(
        json.dumps(
            [
                {
                    "directory": str(tmp_path),
                    "arguments": [
                        "g++",
                        "-std=c++17",
                        "-shared",
                        "-fPIC",
                        "-c",
                        str(src),
                        "-o",
                        "widget.o",
                    ],
                    "file": str(src),
                }
            ]
        ),
        encoding="utf-8",
    )
    return so_path, header, compile_db


@pytest.mark.skipif(
    not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
)
def test_dump_folds_real_l3_evidence_into_ast_compile_context(tmp_path: Path) -> None:
    """A real ``dump --build-info`` baseline carries the real ``-std=``.

    Direct assertion on the reported symptom: ``ast_resolved_standard``/
    ``ast_compile_args`` must not be empty when real L3 build evidence
    (a compile database recording ``-std=c++17``) was supplied.
    """
    so_path, header, compile_db = _build_library(tmp_path)
    baseline = tmp_path / "baseline.json"

    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            "clang",
            "-o",
            str(baseline),
        ],
    )
    assert result.exit_code == 0, result.output

    snap = json.loads(baseline.read_text(encoding="utf-8"))
    assert snap.get("ast_resolved_standard") == "c++17", snap.get(
        "ast_resolved_standard"
    )
    assert snap.get("ast_compile_args"), "ast_compile_args must not be empty"
    assert snap.get("parsed_with_build_context") is True


@pytest.mark.skipif(
    not (_HAVE_GXX and _HAVE_CLANG), reason="needs a real g++ and clang toolchain"
)
def test_scan_against_real_dump_baseline_is_comparable_on_unchanged_source(
    tmp_path: Path,
) -> None:
    """The full repro from the bug report: dump a baseline, then ``scan
    --against`` it on the identical, unchanged codebase. Must resolve as
    comparable (``NO_CHANGE``/exit 0), never ``NOT_COMPARABLE`` (exit 6)."""
    so_path, header, compile_db = _build_library(tmp_path)
    baseline = tmp_path / "baseline.json"

    dump_result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            "clang",
            "-o",
            str(baseline),
        ],
    )
    assert dump_result.exit_code == 0, dump_result.output

    scan_result = CliRunner().invoke(
        main,
        [
            "scan",
            str(so_path),
            "-H",
            str(header),
            "--sources",
            str(tmp_path),
            "--build-info",
            str(compile_db),
            "--depth",
            "source",
            "--ast-frontend",
            "clang",
            "--against",
            str(baseline),
        ],
    )
    assert "NOT_COMPARABLE" not in scan_result.output, scan_result.output
    assert "profile_fingerprint mismatch" not in scan_result.output, scan_result.output
    assert scan_result.exit_code == 0, scan_result.output
    assert "Verdict: NO_CHANGE" in scan_result.output, scan_result.output
