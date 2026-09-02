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

"""ADR-063 Phase 2 (DWARF slice): ``EntityId``/``ScopePath`` population from
real DWARF debug info, via ``dwarf_snapshot.py``'s DIE walk.

Split out from ``test_dwarf_snapshot.py`` (itself at its
``architecture/debt.yaml`` no-growth baseline) rather than grown in place --
this file compiles its own small fixtures instead of sharing that module's
class-scoped ones.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from abicheck.dwarf_snapshot import _DwarfSnapshotBuilder, build_snapshot_from_dwarf
from abicheck.dwarf_unified import parse_dwarf
from abicheck.elf_metadata import parse_elf_metadata
from abicheck.model.identity import EntityKind, Namespace

_GCC = "gcc"


def _can_compile() -> bool:
    try:
        result = subprocess.run(
            [_GCC, "--version"], capture_output=True, text=True, timeout=10
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


_HAS_GCC = _can_compile()


@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestDwarfEntityIdCLib:
    """A plain C library: no real Itanium mangling anywhere, so every
    ``entity_id`` takes the scope-free extern-"C" branch."""

    @pytest.fixture()
    def c_lib(self, tmp_path: Path) -> Path:
        c_src = tmp_path / "lib.c"
        c_src.write_text(
            "typedef struct { int x; int y; } Point;\n"
            "int global_var = 42;\n"
            "int add(int a, int b) { return a + b; }\n"
            "Point make_point(int x, int y) { Point p; p.x = x; p.y = y; return p; }\n"
        )
        so_path = tmp_path / "libc_entity.so"
        result = subprocess.run(
            [_GCC, "-shared", "-fPIC", "-g", "-o", str(so_path), str(c_src)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"Compilation failed: {result.stderr}"
        return so_path

    def test_function_entity_id_populated(self, c_lib: Path) -> None:
        elf_meta = parse_elf_metadata(c_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(c_lib)
        snap = build_snapshot_from_dwarf(c_lib, elf_meta, dwarf_meta, dwarf_adv)

        add_func = next((f for f in snap.functions if f.name == "add"), None)
        assert add_func is not None
        assert add_func.entity_id is not None
        assert add_func.entity_id.kind == EntityKind.FUNCTION
        assert add_func.entity_id.leaf_name == "add"
        assert add_func.entity_id.extra == ("extern_c",)

    def test_variable_entity_id_populated(self, c_lib: Path) -> None:
        elf_meta = parse_elf_metadata(c_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(c_lib)
        snap = build_snapshot_from_dwarf(c_lib, elf_meta, dwarf_meta, dwarf_adv)

        gv = next((v for v in snap.variables if v.name == "global_var"), None)
        assert gv is not None
        assert gv.entity_id is not None
        assert gv.entity_id.kind == EntityKind.VARIABLE
        assert gv.entity_id.leaf_name == "global_var"
        assert gv.entity_id.extra == ("extern_c",)

    def test_typedef_entity_ids_sidecar_populated(self, c_lib: Path) -> None:
        """``AbiSnapshot.typedef_entity_ids`` is keyed identically to
        ``typedefs`` -- mirrors ``dumper_castxml.py``'s/``dumper_clang.py``'s
        own sidecar."""
        elf_meta = parse_elf_metadata(c_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(c_lib)
        snap = build_snapshot_from_dwarf(c_lib, elf_meta, dwarf_meta, dwarf_adv)

        assert set(snap.typedef_entity_ids) == set(snap.typedefs)
        point_id = snap.typedef_entity_ids["Point"]
        assert point_id.kind == EntityKind.TYPEDEF
        assert point_id.leaf_name == "Point"


@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestDwarfEntityIdCppNamespacedRecords:
    """A namespaced C++ record's ``entity_id`` carries a real typed
    ``ScopePath``, and two distinct sibling records in the same namespace
    resolve to distinct ``EntityId``s -- the exact collision class ADR-063
    Phase 2 exists to close, now for DWARF too."""

    @pytest.fixture()
    def nested_cpp_lib(self, tmp_path: Path) -> Path:
        cpp = tmp_path / "lib.cpp"
        cpp.write_text(
            "namespace pkg {\n"
            "struct Base { virtual ~Base(); virtual int f(int); int b0; };\n"
            "struct Derived : Base { int a0; int f(int) override; };\n"
            "Base::~Base() {}\n"
            "int Base::f(int z) { return z; }\n"
            "int Derived::f(int z) { return z + a0; }\n"
            "}\n"
        )
        so_path = tmp_path / "libnested_entity.so"
        result = subprocess.run(
            [
                _GCC.replace("gcc", "g++"),
                "-shared",
                "-fPIC",
                "-g",
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

    def test_record_entity_id_carries_namespace_scope(
        self, nested_cpp_lib: Path
    ) -> None:
        elf_meta = parse_elf_metadata(nested_cpp_lib)
        builder = _DwarfSnapshotBuilder(nested_cpp_lib, elf_meta)
        builder.extract()

        base = next(t for t in builder.types if t.name == "pkg::Base")
        derived = next(t for t in builder.types if t.name == "pkg::Derived")
        assert base.entity_id is not None
        assert derived.entity_id is not None
        assert base.entity_id.scope == (Namespace("pkg"),)
        assert base.entity_id.kind == EntityKind.TYPE
        assert base.entity_id.leaf_name == "Base"
        assert derived.entity_id.leaf_name == "Derived"
        assert base.entity_id != derived.entity_id


@pytest.mark.skipif(not _HAS_GCC, reason="GCC not available")
class TestDwarfEntityIdNonItaniumLinkageName:
    """A real, explicit linkage name that doesn't start with ``_Z`` (an
    ``asm("...")`` label on an ordinary C++ function) must still take the
    mangled-identity branch, not the scope-free extern-"C" one --
    ``Function.is_extern_c``'s own ``not mangled.startswith("_Z")`` heuristic
    is not a trustworthy is-genuinely-extern-"C" signal the way the two
    header-AST backends' real language-linkage read is (Codex review, PR
    #1015)."""

    @pytest.fixture()
    def asm_label_lib(self, tmp_path: Path) -> Path:
        cpp = tmp_path / "lib.cpp"
        cpp.write_text(
            'int cppfunc(int x) asm("custom_cpp_name");\n'
            "int cppfunc(int x) { return x + 1; }\n"
        )
        so_path = tmp_path / "libasmlabel_entity.so"
        result = subprocess.run(
            [
                _GCC.replace("gcc", "g++"),
                "-shared",
                "-fPIC",
                "-g",
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

    def test_real_non_itanium_linkage_name_takes_mangled_branch(
        self, asm_label_lib: Path
    ) -> None:
        elf_meta = parse_elf_metadata(asm_label_lib)
        dwarf_meta, dwarf_adv = parse_dwarf(asm_label_lib)
        snap = build_snapshot_from_dwarf(asm_label_lib, elf_meta, dwarf_meta, dwarf_adv)

        func = next((f for f in snap.functions if f.mangled == "custom_cpp_name"), None)
        assert func is not None
        # Pre-existing, unrelated heuristic: still (wrongly) reads this as
        # extern-"C" -- not this PR's to fix, only entity_id's own handling
        # of it.
        assert func.is_extern_c is True
        assert func.entity_id is not None
        assert func.entity_id.kind == EntityKind.FUNCTION
        assert func.entity_id.extra == ("mangled", "custom_cpp_name")
