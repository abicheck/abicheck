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

"""``AbiSnapshot.semantic_ir`` for a real DWARF-only dump (ADR-063 Phase 6,
fifth slice) -- end-to-end, against real compiled fixtures, exercising the
actual production wiring (``dumper_elf_fallback._try_dwarf_snapshot`` ->
``dwarf_snapshot.build_snapshot_from_dwarf`` ->
``extract.semantic_normalizer.normalize_header_ast``), not the normalizer in
isolation (``test_semantic_normalizer.py``'s own ``producer="dwarf"``
section covers that).

Compiles real ``.so`` fixtures with gcc/g++, the same lightweight
``skipif``-gated pattern ``test_dwarf_entity_id.py`` uses (this needs no
castxml/clang, so no ``integration`` marker -- matching that module's own
choice, not ``test_semantic_ir_end_to_end.py``'s, which does need both
header-AST toolchains).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from abicheck.dumper_elf_fallback import _try_dwarf_snapshot
from abicheck.dwarf_unified import parse_dwarf
from abicheck.elf_metadata import parse_elf_metadata
from abicheck.model.fact import FactStatus
from abicheck.model.identity import EntityKind

_GPP = "g++"


def _has_gpp() -> bool:
    """Mirrors ``test_dwarf_entity_id.py``'s own ``_has_gpp`` -- a real GCC
    install does not guarantee a matching g++, and ``gcc`` on macOS is a
    clang symlink producing Mach-O, not ELF."""
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


def _one_entity(snapshot, entity_id):
    """The single ``CanonicalEntity`` for *entity_id* in *snapshot*'s
    ``semantic_ir`` -- fails loudly if there is zero or more than one, since
    every fixture in this file is built to have exactly one occurrence per
    entity (a real multi-occurrence case is ``test_semantic_ir_merge.py``'s
    concern, not this module's)."""
    (occ_id,) = snapshot.semantic_ir.occurrences_for(entity_id)
    return snapshot.semantic_ir.occurrences[occ_id]


def _build_snapshot(so_path: Path):
    """Runs the real production call chain
    ``dumper_elf_fallback._try_dwarf_snapshot`` (no headers, not
    ``--dwarf-only``) and returns the resulting ``AbiSnapshot``."""
    elf_meta = parse_elf_metadata(so_path)
    session_out: list = []
    dwarf_meta, dwarf_adv = parse_dwarf(so_path, _session_out=session_out)
    session = session_out[0] if session_out else None
    snap, _dwarf_only_types = _try_dwarf_snapshot(
        so_path,
        elf_meta,
        dwarf_meta,
        dwarf_adv,
        version="1.0",
        profile_hint="cpp",
        headers=[],
        dwarf_only=False,
        session=session,
    )
    assert snap is not None
    return snap


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
class TestDwarfSemanticIrCvQualification:
    """The money case: a DWARF-only dump's ``cv_qualification`` must
    correctly distinguish a const POINTER from a pointer to const DATA, even
    though ``dwarf_snapshot``'s own type-name reconstruction renders both
    with the IDENTICAL text (``"const int *"``) -- only the structural
    ``Variable.is_const`` field (read directly for ``producer="dwarf"``, see
    ``extract/semantic_normalizer.py``'s own docstring) can tell them apart.
    """

    @pytest.fixture()
    def cv_lib(self, tmp_path: Path) -> Path:
        cpp = tmp_path / "cv.cpp"
        cpp.write_text(
            'extern "C" {\n'
            "extern const int g_const_int = 42;\n"
            "extern int* g_plain_ptr = nullptr;\n"
            "extern int* const g_const_ptr = nullptr;\n"
            "extern const int* g_ptr_to_const = nullptr;\n"
            "extern volatile int g_volatile_int = 7;\n"
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
        return so_path

    @pytest.fixture()
    def snapshot(self, cv_lib: Path):
        return _build_snapshot(cv_lib)

    def _cv_qualification_for(self, snapshot, var_name: str) -> tuple[str, ...]:
        var = next(v for v in snapshot.variables if v.name == var_name)
        assert snapshot.semantic_ir is not None
        entity = _one_entity(snapshot, var.entity_id)
        assert entity.cv_qualification.status is FactStatus.PRESENT
        return entity.cv_qualification.value

    def test_semantic_ir_is_populated(self, snapshot) -> None:
        assert snapshot.semantic_ir is not None
        assert len(snapshot.semantic_ir.occurrences) > 0

    def test_by_value_const_is_top_level(self, snapshot) -> None:
        assert self._cv_qualification_for(snapshot, "g_const_int") == ("const",)

    def test_plain_pointer_has_no_qualification(self, snapshot) -> None:
        assert self._cv_qualification_for(snapshot, "g_plain_ptr") == ()

    def test_const_pointer_is_top_level(self, snapshot) -> None:
        """``int* const`` -- the pointer itself is const."""
        assert self._cv_qualification_for(snapshot, "g_const_ptr") == ("const",)

    def test_pointer_to_const_is_not_top_level(self, snapshot) -> None:
        """``const int*`` -- the pointee is const, the pointer is not. This
        is the discriminating case: ``dwarf_snapshot``'s own type-name
        reconstruction renders this variable's ``.type`` with the exact same
        text as ``g_const_ptr`` above (``"const int *"``) -- only the
        structural ``is_const`` field distinguishes them."""
        const_ptr = next(v for v in snapshot.variables if v.name == "g_const_ptr")
        ptr_to_const = next(v for v in snapshot.variables if v.name == "g_ptr_to_const")
        assert const_ptr.type == ptr_to_const.type
        assert self._cv_qualification_for(snapshot, "g_ptr_to_const") == ()

    def test_volatile_is_a_documented_gap_not_a_false_absence(self, snapshot) -> None:
        """DWARF extracts no structural volatile fact for a variable at all
        -- a genuinely volatile, non-const variable reports the same empty
        ``cv_qualification`` a plain variable would (a documented gap, see
        ``extract/semantic_normalizer.py``'s own docstring), so this asserts
        the known behaviour rather than a claim that it is correct."""
        assert self._cv_qualification_for(snapshot, "g_volatile_int") == ()


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
class TestDwarfSemanticIrFunctionsAndTypes:
    """Functions and record types populate ``semantic_ir`` too, and a
    function's ``cv_qualification`` is honestly ``NOT_COLLECTED`` -- DWARF's
    own DIE walk never reads a method's const/volatile qualifier, so
    reporting a confirmed empty tuple would misrepresent "never looked" as
    "confirmed not const"."""

    @pytest.fixture()
    def fn_lib(self, tmp_path: Path) -> Path:
        cpp = tmp_path / "fn.cpp"
        cpp.write_text(
            "struct Widget {\n"
            "  int getValue() const { return value_; }\n"
            "  int value_ = 0;\n"
            "};\n"
            "Widget* make_widget() { return new Widget(); }\n"
            "int compute(const int* p, int n) { return p ? *p + n : n; }\n"
        )
        so_path = tmp_path / "libfn.so"
        result = subprocess.run(
            [
                _GPP,
                "-g",
                "-shared",
                "-fPIC",
                "-std=c++17",
                "-o",
                str(so_path),
                str(cpp),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        return so_path

    @pytest.fixture()
    def snapshot(self, fn_lib: Path):
        return _build_snapshot(fn_lib)

    def test_function_occurrences_populated(self, snapshot) -> None:
        assert snapshot.functions, "fixture should export at least one function"
        assert snapshot.semantic_ir is not None
        for fn in snapshot.functions:
            assert fn.entity_id is not None
            entity = _one_entity(snapshot, fn.entity_id)
            assert entity.producer == "dwarf"
            assert entity.canonical_spelling.status is FactStatus.PRESENT

    def test_function_cv_qualification_not_collected(self, snapshot) -> None:
        compute = next(f for f in snapshot.functions if f.name == "compute")
        entity = _one_entity(snapshot, compute.entity_id)
        assert entity.cv_qualification.status is FactStatus.NOT_COLLECTED

    def test_record_type_occurrence_populated(self, snapshot) -> None:
        widget = next((t for t in snapshot.types if t.name == "Widget"), None)
        assert widget is not None
        assert widget.entity_id is not None
        entity = _one_entity(snapshot, widget.entity_id)
        assert entity.canonical_spelling.value == "Widget"
        assert entity.producer == "dwarf"


@pytest.mark.skipif(not _HAS_GPP, reason="g++ not available")
def test_dwarf_semantic_ir_has_no_constant_occurrences(
    tmp_path: Path,
) -> None:
    """DWARF carries no constexpr-initializer evidence at all (see
    ``AbiSnapshot.constant_entity_ids``'s own docstring) -- a DWARF-only
    dump's ``semantic_ir`` must never contain a ``CONSTANT``-kind
    occurrence, even for a source-level ``constexpr`` the compiler keeps a
    debuggable copy of."""
    cpp = tmp_path / "const.cpp"
    cpp.write_text("constexpr int kAnswer = 42;\nint use_it() { return kAnswer; }\n")
    so_path = tmp_path / "libconst.so"
    result = subprocess.run(
        [_GPP, "-g", "-shared", "-fPIC", "-o", str(so_path), str(cpp)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Compilation failed: {result.stderr}"
    snapshot = _build_snapshot(so_path)
    assert snapshot.semantic_ir is not None
    assert all(
        occ_id.entity_id.kind is not EntityKind.CONSTANT
        for occ_id in snapshot.semantic_ir.occurrences
    )
