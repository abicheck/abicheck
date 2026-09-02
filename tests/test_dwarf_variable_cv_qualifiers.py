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

"""``Variable.is_const`` derivation from a DWARF variable's own outermost
type -- ``extract.dwarf_records.variable_is_const`` and its caller,
``dwarf_snapshot._DwarfSnapshotBuilder._process_variable`` (Codex review on
PR #1021, fresh evidence).

**Bug class, not a single reported input.** The original finding was one
input (``const volatile int``); this file states the general invariant --
"a variable's own outermost declared type is const-qualified if and only if
a ``DW_TAG_const_type`` DIE appears anywhere in the LEADING run of pure
cv-qualifier wrapper DIEs its ``DW_AT_type`` chain starts with, regardless
of what other cv-qualifiers wrap it or in what order" -- against every
independently-chosen sibling shape that distinguishes "checks only the
immediate wrapper" (the reverted bug) from "walks the whole leading
cv-qualifier run" (the fix): plain, const-only, volatile-only, both orders
of const+volatile, and a const pointer (unaffected by this fix, since a
pointer's own const-ness was never behind a volatile wrapper) as a negative
control proving the fix didn't overreach into declarator territory it
shouldn't touch.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from abicheck.dwarf_snapshot import build_snapshot_from_dwarf
from abicheck.dwarf_unified import parse_dwarf
from abicheck.elf_metadata import parse_elf_metadata

_GPP = "g++"


def _has_gpp() -> bool:
    """Mirrors ``test_dwarf_entity_id.py``'s own ``_has_gpp``."""
    if sys.platform != "linux":
        return False
    try:
        result = subprocess.run(
            [_GPP, "--version"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


_HAS_GPP = _has_gpp()


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
class TestDwarfVariableIsConstCvQualifierOrder:
    """One real compiled fixture covering every sibling shape, not just the
    one reported input."""

    @pytest.fixture(scope="class")
    def variables_by_name(self, tmp_path_factory: pytest.TempPathFactory):
        tmp_path = tmp_path_factory.mktemp("dwarf_cv_qualifiers")
        cpp = tmp_path / "cv.cpp"
        cpp.write_text(
            'extern "C" {\n'
            "extern int g_plain = 0;\n"
            "extern const int g_const = 1;\n"
            "extern volatile int g_volatile = 2;\n"
            "extern const volatile int g_const_volatile = 3;\n"
            "extern volatile const int g_volatile_const = 4;\n"
            "extern int* const g_const_ptr = nullptr;\n"
            "}\n"
        )
        so_path = tmp_path / "libcv.so"
        result = subprocess.run(
            [_GPP, "-g", "-shared", "-fPIC", "-o", str(so_path), str(cpp)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"

        elf_meta = parse_elf_metadata(so_path)
        session_out: list = []
        dwarf_meta, dwarf_adv = parse_dwarf(so_path, _session_out=session_out)
        session = session_out[0] if session_out else None
        snap = build_snapshot_from_dwarf(
            so_path, elf_meta, dwarf_meta, dwarf_adv, session=session
        )
        return {v.name: v for v in snap.variables}

    def test_plain_variable_is_not_const(self, variables_by_name) -> None:
        assert variables_by_name["g_plain"].is_const is False

    def test_const_variable_is_const(self, variables_by_name) -> None:
        assert variables_by_name["g_const"].is_const is True

    def test_volatile_only_variable_is_not_const(self, variables_by_name) -> None:
        assert variables_by_name["g_volatile"].is_const is False

    def test_const_volatile_variable_is_const(self, variables_by_name) -> None:
        """The originally reported case: GCC nests ``DW_TAG_volatile_type``
        (outer) around ``DW_TAG_const_type`` (inner) for ``const volatile
        int`` -- checking only the immediate wrapper die missed the const
        qualifier entirely."""
        assert variables_by_name["g_const_volatile"].is_const is True

    def test_volatile_const_variable_is_const(self, variables_by_name) -> None:
        """The other source-order spelling of the identical type --
        ``const``/``volatile`` order is not semantically meaningful in C++,
        so this must resolve identically to the previous case regardless of
        which order GCC's own DWARF encoding happens to nest them in."""
        assert variables_by_name["g_volatile_const"].is_const is True

    def test_const_pointer_is_still_const(self, variables_by_name) -> None:
        """Negative control: a plain const pointer (``int* const``, no
        volatile involved at all) was never affected by the immediate-
        wrapper-only bug -- confirms the fix didn't regress the case that
        already worked."""
        assert variables_by_name["g_const_ptr"].is_const is True
