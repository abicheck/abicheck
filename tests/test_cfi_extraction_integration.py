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

"""Integration: real CFI (.eh_frame/.debug_frame) extraction from a
genuinely compiled, real-DWARF ELF shared library (P1 review, fresh
evidence).

This is the exact "third-party-boundary" case this repo's own test-quality
guidance calls out: a mock-only unit test using a bare, unspec'd
``MagicMock()`` for pyelftools' ``DWARFInfo`` cannot catch a method-name
mismatch against the real library, because the mock happily answers
whatever attribute name the code under test asks for. That is precisely
how ``_get_cfi_source()`` shipped calling nonexistent
``get_EH_CFI_entries()``/``get_CFI_entries()`` (pyelftools' real API is
``EH_CFI_entries()``/``CFI_entries()``, no ``get_`` prefix) for an unknown
period without any test noticing: every unit test using an unspec'd mock
passed identically before and after the bug, and CFI extraction silently
never ran against any real binary, always taking the "no CFI section at
all" early-return path while still reporting the advanced channel
``parsed``.

Drives ``_get_cfi_source``/``_parse_frame_registers`` through their real
public entry points against an actually-compiled ``.so``, at the toolchain
boundary these mock-based unit tests cannot exercise -- following the
pattern ``test_btf_integration.py`` already established for the analogous
BTF-toolchain-boundary case.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from elftools.elf.elffile import ELFFile

from abicheck.dwarf_advanced import (
    AdvancedDwarfMetadata,
    _get_cfi_source,
    _parse_frame_registers,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="gcc-produced .eh_frame CFI extraction is exercised on Linux",
)

_LIB_SRC = """
int add(int a, int b) { return a + b; }
int sub(int a, int b) { return a - b; }
"""


def _compile_lib(tmp: Path, name: str = "liba.so") -> Path:
    out = tmp / name
    cmd = ["gcc", "-shared", "-fPIC", "-g", "-O0", "-o", str(out), "-x", "c", "-"]
    result = subprocess.run(cmd, input=_LIB_SRC.encode(), capture_output=True)
    if result.returncode != 0:
        pytest.skip(f"gcc unavailable/failed: {result.stderr.decode()[:200]}")
    return out


@pytest.mark.integration
def test_real_binary_cfi_source_is_not_none() -> None:
    """``_get_cfi_source`` must actually find the real ``.eh_frame`` CFI
    pyelftools exposes for any normally-compiled binary -- the bug this
    guards against made this unconditionally ``None``."""
    with tempfile.TemporaryDirectory() as tmp:
        so = _compile_lib(Path(tmp))
        with open(so, "rb") as f:
            elf = ELFFile(f)
            dwarf = elf.get_dwarf_info()
            src = _get_cfi_source(dwarf)
            assert src is not None
            assert len(list(src)) > 0


@pytest.mark.integration
def test_real_binary_frame_registers_are_populated() -> None:
    """End-to-end: ``_parse_frame_registers`` against a real compiled
    binary must populate real frame-register/callee-saved-register facts
    for its exported functions, not silently no-op."""
    with tempfile.TemporaryDirectory() as tmp:
        so = _compile_lib(Path(tmp))
        with open(so, "rb") as f:
            elf = ELFFile(f)
            dwarf = elf.get_dwarf_info()
            meta = AdvancedDwarfMetadata(has_dwarf=True)
            complete = _parse_frame_registers(elf, dwarf, meta)

        assert complete is True
        # Both exported functions must have real CFI-derived facts -- the
        # bug this guards against left both dicts permanently empty.
        assert "add" in meta.frame_registers
        assert "sub" in meta.frame_registers
        assert meta.frame_registers["add"]  # a real register name, not falsy
        assert "add" in meta.callee_saved_regs
        assert isinstance(meta.callee_saved_regs["add"], frozenset)
