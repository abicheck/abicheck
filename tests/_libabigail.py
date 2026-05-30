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

"""Shared helpers for the libabigail parity test lanes.

`abidiff`, `abicompat`, and `abipkgdiff` share one exit-code bit field and the
same gcc-compile / tool-presence plumbing. Keeping a single copy here avoids the
three parity test files drifting out of sync (e.g. a decode fix landing in two
of three lanes). Not a pytest plugin — plain importable helpers.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

#: libabigail exit-code bit field (shared by abidiff / abicompat / abipkgdiff):
#:   bit 0 (1) = error
#:   bit 2 (4) = compatible changes present
#:   bit 3 (8) = incompatible (breaking) changes present
_BIT_ERROR = 1
_BIT_COMPATIBLE = 4
_BIT_INCOMPATIBLE = 8


def decode_exit_code(code: int, *, zero_verdict: str = "COMPATIBLE") -> str:
    """Decode a libabigail tool exit code into a verdict string.

    ``zero_verdict`` is what a clean exit (0) means for the specific tool:
    ``abidiff`` reports ``"NO_CHANGE"``; ``abicompat`` / ``abipkgdiff`` report
    ``"COMPATIBLE"``.
    """
    if code == 0:
        return zero_verdict
    if code & _BIT_ERROR:
        return "ERROR"
    if code & _BIT_INCOMPATIBLE:
        return "BREAKING"
    if code & _BIT_COMPATIBLE:
        return "COMPATIBLE"
    return zero_verdict


def require_tool(name: str) -> None:
    """Skip the current test if *name* is not on PATH."""
    if shutil.which(name) is None:
        pytest.skip(f"{name} not found in PATH")


def compile_shared_lib(
    src: str,
    out: Path,
    *,
    lang: str = "c",
    soname: str | None = None,
) -> None:
    """Compile *src* into a shared library at *out* (skips on failure).

    ``lang`` is ``"c"`` or ``"cpp"`` (selects gcc/g++). ``soname`` sets an
    explicit ``-Wl,-soname`` when provided.
    """
    ext = ".c" if lang == "c" else ".cpp"
    src_file = out.with_suffix(ext)
    src_file.write_text(src.strip() + "\n", encoding="utf-8")
    compiler = "gcc" if lang == "c" else "g++"
    cmd = [
        compiler, "-shared", "-fPIC", "-g", "-fvisibility=default",
        "-o", str(out), str(src_file),
    ]
    if soname is not None:
        cmd.insert(1, f"-Wl,-soname,{soname}")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        pytest.skip(f"library compile failed: {r.stderr[:200]}")
