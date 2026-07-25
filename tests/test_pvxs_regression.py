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

"""End-to-end regression for the PVXS-style C++20 false-positive dialect bug.

PVXS's ``json.h``-style headers guard a required base type with a plain
preprocessor diagnostic, e.g.::

    #ifndef HAVE_BASE
    #error Foo requires Base
    #endif

abicheck's C++20 ``requires``/``concept`` auto-detection heuristic used to
scan raw header text for the word "requires" without skipping preprocessor
directive lines, so this ``#error`` line alone was enough to make it believe
the header needed C++20, silently forcing ``-std=gnu++20`` onto a project
that only ever declared C++11 support. That is now fixed at two levels
(``dumper_ast_config_cpp20._detect_cpp20_headers`` skips directive lines
entirely; ``cli_helpers_compare._pair_wide_dialect_override`` resolves the
dialect once over the union of both sides so old/new can never disagree) —
both already have direct unit coverage. This test is the missing outer
layer: a real compile + a real ``abicheck compare`` invocation against actual
binaries and headers containing exactly this pattern, on an explicit C++11
profile, proving the whole pipeline stays inert end-to-end -- specifically,
that a forced C++20 dialect never manifests as a false BREAKING/API_BREAK
verdict or a MAJOR/SONAME-bump release advisory for this pair.

Marked ``integration`` (needs a real compiler); uses ``--ast-frontend clang``
so it also runs on hosts with clang but no castxml (unlike most other
integration tests here, which default to castxml).
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

#: The exact PVXS json.h pattern: a plain "requires" inside a preprocessor
#: diagnostic, not a real C++20 requires-clause. HAVE_BASE is always defined
#: by the (real) build below, so the #error never actually fires -- exactly
#: like a real project's config header satisfying its own guard -- but the
#: raw source text still contains the string that used to trip the heuristic.
_GUARDED_HEADER = """\
#ifndef HAVE_BASE
#error Foo requires Base
#endif
int pvxs_op(int x);
"""

_SOURCE = """\
#include "pvxs.h"
int pvxs_op(int x) { return x; }
"""


def _build_lib(src_dir: Path, out_so: Path) -> None:
    header = src_dir / "pvxs.h"
    header.write_text(_GUARDED_HEADER, encoding="utf-8")
    source = src_dir / "pvxs.cpp"
    source.write_text(_SOURCE, encoding="utf-8")
    subprocess.run(
        [
            "g++",
            "-DHAVE_BASE=1",
            "-std=gnu++11",
            "-fPIC",
            "-shared",
            "-o",
            str(out_so),
            str(source),
        ],
        cwd=src_dir,
        check=True,
        capture_output=True,
        text=True,
    )


def test_pvxs_error_requires_guard_does_not_force_cxx20(tmp_path: Path) -> None:
    old_dir = tmp_path / "old"
    new_dir = tmp_path / "new"
    old_dir.mkdir()
    new_dir.mkdir()

    old_so = old_dir / "libpvxs.so"
    new_so = new_dir / "libpvxs.so"
    _build_lib(old_dir, old_so)
    _build_lib(new_dir, new_so)

    out_json = tmp_path / "report.json"
    result = CliRunner().invoke(
        main,
        [
            "compare",
            str(old_so),
            str(new_so),
            "--header",
            f"old={old_dir / 'pvxs.h'}",
            "--header",
            f"new={new_dir / 'pvxs.h'}",
            "--ast-frontend",
            "clang",
            "--gcc-options",
            "-DHAVE_BASE=1",
            "--format",
            "json",
            "-o",
            str(out_json),
        ],
    )
    assert result.exit_code == 0, result.output

    report = json.loads(out_json.read_text(encoding="utf-8"))
    # NO_CHANGE/COMPATIBLE is the common case. On a platform where header <->
    # export-table symbol matching itself degrades for an unrelated reason
    # (observed on macOS Mach-O: --scope-public-headers falls back to the
    # full export table, reducing confidence and tripping an unrelated
    # runtime/toolchain-floor RISK finding) the verdict can widen to
    # COMPATIBLE_WITH_RISK, which correctly earns its own PATCH/MINOR release
    # recommendation -- that degradation is orthogonal to the C++20 dialect
    # bug this test guards against, so it isn't asserted away. What a forced
    # C++20 dialect misclassification on this pair *would* look like is a
    # false BREAKING/API_BREAK verdict, a MAJOR version bump, or a SONAME
    # bump advisory -- those are the real regression guards below.
    assert report["verdict"] not in ("BREAKING", "API_BREAK")
    assert report["summary"]["breaking"] == 0
    assert report["summary"]["source_breaks"] == 0
    kinds = {c.get("kind") for c in report.get("changes", [])}
    assert "soname_bump_recommended" not in kinds
    release = report.get("release_recommendation") or {}
    assert release.get("soname_action") not in ("bump_required", "bump_missing")
    assert release.get("version_bump") != "major"
