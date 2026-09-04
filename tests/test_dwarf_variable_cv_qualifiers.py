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

"""Two related but independent Codex-review fixes for DWARF variable
cv-qualifier handling (PR #1021, fresh evidence each round):

1. ``TestDwarfVariableIsConstCvQualifierOrder`` -- ``Variable.is_const``
   derivation from a DWARF variable's own outermost type
   (``extract.dwarf_records.variable_is_const`` and its caller,
   ``dwarf_snapshot._DwarfSnapshotBuilder._process_variable``).

   **Bug class, not a single reported input.** The original finding was one
   input (``const volatile int``); this file states the general invariant --
   "a variable's own outermost declared type is const-qualified if and only
   if a ``DW_TAG_const_type`` DIE appears anywhere in the LEADING run of pure
   cv-qualifier wrapper DIEs its ``DW_AT_type`` chain starts with, regardless
   of what other cv-qualifiers wrap it or in what order" -- against every
   independently-chosen sibling shape that distinguishes "checks only the
   immediate wrapper" (the reverted bug) from "walks the whole leading
   cv-qualifier run" (the fix): plain, const-only, volatile-only, both orders
   of const+volatile, a const pointer (unaffected by the first round's fix,
   since a pointer's own const-ness was never behind a volatile wrapper) as
   a negative control, and -- the second round's finding -- a const pointer
   wrapped in ``restrict`` (``int * const restrict``, where GCC nests
   ``DW_TAG_restrict_type`` outside ``DW_TAG_const_type`` the same way it
   nests ``DW_TAG_volatile_type``) plus a restrict-only pointer as that
   round's own negative control.

2. ``TestDwarfConstPointerSpellingPlacement`` -- ``Variable.type``'s own
   reconstructed spelling (``dwarf_snapshot._compute_type_name`` /
   ``extract.dwarf_records.format_qualified_type_name``): a cv-qualifier
   wrapping a pointer/reference DIE directly must be spelled AFTER the
   sigil (``int * const``), never as an indistinguishable prefix shared
   with a qualifier on the pointee (``const int *``).
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
            "extern int* const __restrict__ g_const_restrict = nullptr;\n"
            "extern int* __restrict__ g_restrict_only = nullptr;\n"
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

    def test_const_restrict_pointer_is_const(self, variables_by_name) -> None:
        """Second-round Codex finding, fresh evidence: GCC nests
        ``DW_TAG_restrict_type`` (outer) around ``DW_TAG_const_type``
        (inner) for ``int * const restrict`` -- the identical shape of bug
        as ``volatile``'s own wrapper, just with a different transparent
        qualifier tag."""
        assert variables_by_name["g_const_restrict"].is_const is True

    def test_restrict_only_pointer_is_not_const(self, variables_by_name) -> None:
        """Negative control: ``restrict`` alone (no ``const`` anywhere in
        the chain) must not be reported as const merely because
        ``DW_TAG_restrict_type`` is now transparent to the walk."""
        assert variables_by_name["g_restrict_only"].is_const is False


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
class TestDwarfConstPointerSpellingPlacement:
    """``dwarf_snapshot``'s own type-name reconstruction (Codex review on PR
    #1021, fresh evidence): a cv-qualifier wrapping a pointer/reference
    DIE directly must be spelled AFTER the sigil (``int * const``, the
    POINTER is const), never confused with a qualifier wrapping only the
    pointee (``const int *``, the pointee is const) -- both used to render
    as the identical ``"const int *"`` text, which silently hid the
    distinction from every consumer of ``Variable.type`` (not just the
    separate structural ``is_const``/``cv_qualification`` facts).

    **Bug class, not a single reported input.** Every independently-chosen
    sibling shape that distinguishes "qualifier always prints as a prefix"
    (the reverted bug) from "qualifier prints on the correct side of the
    declarator" (the fix): a plain pointer (no qualifier at all, negative
    control), a const pointer, a pointer to const data, and a const pointer
    to const data (both qualifiers present at once, on opposite sides).
    """

    @pytest.fixture(scope="class")
    def variables_by_name(self, tmp_path_factory: pytest.TempPathFactory):
        tmp_path = tmp_path_factory.mktemp("dwarf_const_pointer_spelling")
        cpp = tmp_path / "ptrspelling.cpp"
        cpp.write_text(
            'extern "C" {\n'
            "extern int* g_plain_ptr = nullptr;\n"
            "extern int* const g_const_ptr = nullptr;\n"
            "extern const int* g_ptr_to_const = nullptr;\n"
            "extern const int* const g_const_ptr_to_const = nullptr;\n"
            "}\n"
        )
        so_path = tmp_path / "libptrspelling.so"
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

    def test_plain_pointer_has_no_qualifier_text(self, variables_by_name) -> None:
        assert variables_by_name["g_plain_ptr"].type == "int *"

    def test_const_pointer_places_qualifier_after_sigil(
        self, variables_by_name
    ) -> None:
        assert variables_by_name["g_const_ptr"].type == "int * const"

    def test_pointer_to_const_places_qualifier_before_type(
        self, variables_by_name
    ) -> None:
        assert variables_by_name["g_ptr_to_const"].type == "const int *"

    def test_const_pointer_and_pointer_to_const_spell_differently(
        self, variables_by_name
    ) -> None:
        """The discriminating assertion: these two used to collapse to the
        identical spelling. is_const distinguishes them structurally too,
        independent of the text fix."""
        const_ptr = variables_by_name["g_const_ptr"]
        ptr_to_const = variables_by_name["g_ptr_to_const"]
        assert const_ptr.type != ptr_to_const.type
        assert const_ptr.is_const is True
        assert ptr_to_const.is_const is False

    def test_both_qualifiers_at_once_place_each_correctly(
        self, variables_by_name
    ) -> None:
        """``const int* const`` -- pointee const (prefix) AND pointer const
        (suffix) simultaneously; neither placement rule may suppress the
        other."""
        var = variables_by_name["g_const_ptr_to_const"]
        assert var.type == "const int * const"
        assert var.is_const is True
