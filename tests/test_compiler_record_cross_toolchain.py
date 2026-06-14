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

"""E3 — cross-compiler toolchain capture (field-eval P07), live.

Builds the *same* tiny shared library twice — once with gcc, once with clang —
and confirms abicheck recovers the compiler identity from each shipped artifact
(via ``DW_AT_producer`` / ``.GCC.command.line``) and surfaces the gcc↔clang swap
as ``TOOLCHAIN_VERSION_CHANGED`` drift. Needs both compilers, so it is marked
``integration`` (skipped when either is absent). The pure parsers are unit-tested
in ``test_compiler_record_unit.py``; this is the end-to-end validation the eval
follow-up asks for.
"""

from __future__ import annotations

import shutil
import subprocess
import sys

import pytest

from abicheck.buildsource.build_diff import diff_build_evidence
from abicheck.buildsource.compiler_record import extract_compiler_record
from abicheck.checker_policy import ChangeKind

pytestmark = pytest.mark.integration

_GCC = shutil.which("gcc")
_CLANG = shutil.which("clang")


def _is_real_gnu_gcc(cc: str | None) -> bool:
    """True only for a genuine GNU gcc (not the Apple-clang `/usr/bin/gcc` shim).

    `extract_compiler_record` reads ELF + `.GCC.command.line`; on macOS `gcc` is a
    clang driver emitting Mach-O, and on Windows it would emit PE — neither is
    ELF. Gate on a real GNU gcc so the integration lane skips cleanly off-Linux
    rather than failing on an empty/non-ELF artifact.
    """
    if not cc:
        return False
    try:
        out = subprocess.run([cc, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "Free Software Foundation" in out or "(GCC)" in out


def _build(cc: str, src, out, record_flag: str) -> None:
    subprocess.run(
        [cc, "-shared", "-fPIC", "-g", record_flag, str(src), "-o", str(out)],
        check=True, capture_output=True, text=True, timeout=120,
    )


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") and _CLANG and _is_real_gnu_gcc(_GCC)),
    reason="needs ELF host with a real GNU gcc + clang (compiler-record is ELF-only)",
)
def test_gcc_vs_clang_toolchain_drift_is_captured_and_surfaced(tmp_path):
    src = tmp_path / "foo.c"
    src.write_text("int abicheck_add(int a, int b) { return a + b; }\n")
    gcc_so = tmp_path / "libfoo_gcc.so"
    clang_so = tmp_path / "libfoo_clang.so"
    _build(_GCC, src, gcc_so, "-grecord-gcc-switches")
    _build(_CLANG, src, clang_so, "-grecord-command-line")

    gcc_ev = extract_compiler_record(gcc_so)
    clang_ev = extract_compiler_record(clang_so)

    # Producer/toolchain identity recovered from each shipped artifact.
    assert any(t.compiler_id == "GNU" for t in gcc_ev.toolchains), gcc_ev.toolchains
    assert any(t.compiler_id == "Clang" for t in clang_ev.toolchains), clang_ev.toolchains

    # The gcc↔clang swap surfaces as toolchain drift even though clang's
    # DW_AT_producer carries no language token (the asymmetry that previously
    # made _diff_toolchains miss the change — see test_compiler_record_unit.py).
    changes = diff_build_evidence(gcc_ev, clang_ev)
    drift = [c for c in changes if c.kind is ChangeKind.TOOLCHAIN_VERSION_CHANGED]
    assert drift, f"expected toolchain drift, got {[c.kind for c in changes]}"
