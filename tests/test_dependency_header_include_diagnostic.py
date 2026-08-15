# Copyright 2026 Nikolay Petrov
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

"""End-to-end regression for a real-world class of scan/Action failure found
during a PVXS Action acceptance run: a library's public header transitively
``#include``s a *sibling dependency's* header (e.g. PVXS's own
``pvxs/version.h`` pulling in EPICS Base's ``epicsVersion.h``/
``epicsTime.h``) whose directory was never passed to abicheck.

Reproduced end-to-end against real EPICS Base 7.0 + PVXS 1.5.2 sources during
triage: pointing ``-H``/``--header`` only at the library's own public
``include/`` directory (the natural, and in PVXS's case build-produced,
choice) makes ``abicheck scan``/``compare``/``dump`` fail to parse the very
first header that reaches into the dependency, with the parse aborting deep
inside a transitively-``#include``d file the caller never named directly.

This is NOT a code defect in the header-parse pipeline: both header AST
backends (castxml's ``_validate_castxml_output`` /
``dumper_castxml_probe.py`` and clang's ``diagnose_header_compile_failure``
in ``dumper_clang_errors.py``) already turn this into an actionable
``SnapshotError``/``HeaderToolchainError`` naming the missing include and
pointing at ``--include-dir``/``-I`` -- verified directly against the real
PVXS failure. What was missing was end-to-end coverage proving three things
together: (1) the actionable hint, not an opaque subprocess-exit-code
message, is what a real CLI invocation surfaces; (2) once the dependency's
include directory is supplied, the exact same library parses and a
self-comparison reports no ABI change; (3) the fixture shape matches the
real-world failure (a multi-header public surface where only the header
actually reached by ``#include`` first fails, not necessarily the one named
via ``-H``), not merely a single hand-rolled header the string-only unit
tests in ``test_header_failure_hints.py`` already cover.

Marked ``integration`` (needs a real compiler); uses ``--ast-frontend
clang`` like its ``test_pvxs_regression.py`` sibling, so it also runs on
hosts with clang but no castxml.
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
    pytest.mark.skipif(
        sys.platform == "win32", reason="builds an ELF .so pair, linux/macos only"
    ),
    pytest.mark.skipif(
        shutil.which("g++") is None, reason="g++ required to build fixtures"
    ),
    pytest.mark.skipif(
        shutil.which("clang") is None, reason="clang required for --ast-frontend clang"
    ),
]

#: A minimal stand-in for EPICS Base's ``epicsVersion.h`` -- a dependency
#: header living under its own, separate include root.
_DEP_HEADER = """\
#ifndef DEP_VERSION_H
#define DEP_VERSION_H
#define DEP_MAJOR_VERSION 1
#endif
"""

#: The exact PVXS ``version.h`` shape: a public umbrella header that
#: ``#include``s a *sibling dependency's* header via angle brackets (so it
#: is resolved as a system/library include, not relative to itself), before
#: declaring any of the library's own public API.
_MAIN_HEADER = """\
#include <dep/version.h>
#ifndef MAIN_API
#  define MAIN_API
#endif
MAIN_API int main_op(int x);
"""

_SOURCE = """\
#include "main.h"
int main_op(int x) { return x; }
"""


def _build_lib(src_dir: Path, dep_include_dir: Path, out_so: Path) -> None:
    (dep_include_dir / "dep").mkdir(parents=True, exist_ok=True)
    (dep_include_dir / "dep" / "version.h").write_text(_DEP_HEADER, encoding="utf-8")

    header = src_dir / "main.h"
    header.write_text(_MAIN_HEADER, encoding="utf-8")
    source = src_dir / "main.cpp"
    source.write_text(_SOURCE, encoding="utf-8")
    subprocess.run(
        [
            "g++",
            "-std=gnu++11",
            "-fPIC",
            "-shared",
            "-I",
            str(dep_include_dir),
            "-o",
            str(out_so),
            str(source),
        ],
        cwd=src_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def test_missing_dependency_include_dir_gives_actionable_hint(
    tmp_path: Path,
) -> None:
    """Without the dependency's include dir, the CLI must surface the real
    'missing include' hint (--include-dir / -I) -- not an opaque subprocess
    exit-code message with no remediation, which is what the PVXS Action
    acceptance run reported seeing (the actual root cause, traced end-to-end
    against real EPICS Base + PVXS sources, turned out to be exactly this:
    the dependency's -I was never passed)."""
    src_dir = tmp_path / "lib"
    src_dir.mkdir()
    dep_dir = tmp_path / "dep-include"
    so_path = src_dir / "libmain.so"
    _build_lib(src_dir, dep_dir, so_path)

    out_json = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "dump",
            str(so_path),
            "-H",
            str(src_dir / "main.h"),
            "--ast-frontend",
            "clang",
            "-o",
            str(out_json),
        ],
    )
    assert result.exit_code != 0
    # The actionable hint, not a bare "returned non-zero exit status" dump.
    assert "dep/version.h" in result.output
    assert "--include-dir" in result.output or "-I" in result.output
    assert "was not found" in result.output


def test_dependency_include_dir_supplied_scan_and_self_compare_succeed(
    tmp_path: Path,
) -> None:
    """With the dependency's include dir passed via -I, the exact same
    library parses cleanly and a self-comparison (the Action's own
    self-compare smoke check) reports no ABI change."""
    src_dir = tmp_path / "lib"
    src_dir.mkdir()
    dep_dir = tmp_path / "dep-include"
    so_path = src_dir / "libmain.so"
    _build_lib(src_dir, dep_dir, so_path)

    out_json = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(so_path),
            str(so_path),
            "-H",
            str(src_dir / "main.h"),
            "-I",
            str(dep_dir),
            "--ast-frontend",
            "clang",
            "--format",
            "json",
            "-o",
            str(out_json),
        ],
    )
    assert result.exit_code == 0, result.output

    report = json.loads(out_json.read_text(encoding="utf-8"))
    assert report["verdict"] in ("COMPATIBLE", "NO_CHANGE")
    assert report["summary"]["breaking"] == 0
    assert report["summary"]["source_breaks"] == 0
