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

"""Build a real PE DLL from C source on any host that has a PE toolchain.

MinGW ``gcc`` on Windows; elsewhere ``clang`` targeting windows-gnu with
``ld.lld`` (no C runtime, so a stub ``DllMainCRTStartup`` stands in for the
entry point). Follows the suite's three-state rule: no toolchain is a skip
(``False``), a toolchain that runs and fails is a test failure.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def build_pe_dll(out: Path, source: Path) -> bool:
    """Build *source* into a PE DLL at *out*; ``False`` when no PE toolchain
    is present on this host."""
    if sys.platform == "win32" and shutil.which("gcc"):
        cmd = ["gcc", "-shared", "-o", str(out), str(source)]
    elif shutil.which("clang") and shutil.which("ld.lld"):
        entry = out.with_suffix(".entry.c")
        entry.write_text(
            "int DllMainCRTStartup(void *a, unsigned b, void *c) { return 1; }\n",
            encoding="utf-8",
        )
        cmd = [
            "clang",
            "--target=x86_64-windows-gnu",
            "-shared",
            "-nostdlib",
            "-fuse-ld=lld",
            "-Wl,--export-all-symbols",
            "-o",
            str(out),
            str(source),
            str(entry),
        ]
    else:
        return False
    proc = subprocess.run(cmd, capture_output=True, text=True)
    assert proc.returncode == 0, (cmd, proc.stderr)
    return True
