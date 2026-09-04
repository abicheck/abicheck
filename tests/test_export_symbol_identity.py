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

"""ADR-063 Phase 2: ``entity_id`` population for the export-table-only
(no-headers) fallback paths -- ELF (already covered in
``test_dumper_elf_fallback_entity_id.py``), Mach-O and PE
(``dumper.py``'s own ``_dump_macho``/``_dump_pe``), all built on the
shared ``extract/export_symbol_identity.py``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from abicheck.extract.export_symbol_identity import (
    itanium_export_function,
    itanium_export_variable,
    msvc_export_function,
)
from abicheck.model.identity import EntityKind


class TestExportSymbolIdentityHelpers:
    """Direct unit coverage of the shared builders, no binary needed."""

    def test_itanium_export_function_mangled(self) -> None:
        fn = itanium_export_function("_Z3addii")
        assert fn.entity_id is not None
        assert fn.entity_id.kind == EntityKind.FUNCTION
        assert fn.entity_id.extra == ("mangled", "_Z3addii")

    def test_itanium_export_function_plain_c(self) -> None:
        fn = itanium_export_function("plain")
        assert fn.entity_id is not None
        assert fn.entity_id.extra == ("extern_c",)
        assert fn.entity_id.leaf_name == "plain"

    def test_itanium_export_variable(self) -> None:
        var = itanium_export_variable("_ZN2ns1xE")
        assert var.entity_id is not None
        assert var.entity_id.kind == EntityKind.VARIABLE
        assert var.entity_id.extra == ("mangled", "_ZN2ns1xE")

    def test_msvc_export_function_mangled(self) -> None:
        fn = msvc_export_function("?add@@YAHHH@Z")
        assert fn.entity_id is not None
        assert fn.entity_id.extra == ("mangled", "?add@@YAHHH@Z")

    def test_msvc_export_function_plain(self) -> None:
        fn = msvc_export_function("PlainExport")
        assert fn.entity_id is not None
        assert fn.entity_id.extra == ("extern_c",)

    def test_msvc_export_function_itanium_mangled_mingw(self) -> None:
        # A MinGW/GCC-built PE DLL's C++ exports are Itanium-mangled, not
        # MSVC-mangled -- a real, supported PE lane distinct from MSVC's own.
        fn = msvc_export_function("_Z3addii")
        assert fn.entity_id is not None
        assert fn.entity_id.extra == ("mangled", "_Z3addii")

    def test_msvc_export_function_stdcall_decoration_stripped_on_x86_32(self) -> None:
        # extern "C" __stdcall int f(int) exports as "_f@4" on 32-bit x86 --
        # the identity's leaf must be the undecorated "f" to agree with the
        # header-AST producer's own EntityId for the identical declaration.
        # Only meaningful when the caller identifies the binary as x86-32
        # (see the x64 test below for why this must NOT be the default).
        fn = msvc_export_function("_f@4", is_x86_32=True)
        assert fn.entity_id is not None
        assert fn.entity_id.extra == ("extern_c",)
        assert fn.entity_id.leaf_name == "f"
        # The raw, decorated evidence is preserved on the Function itself.
        assert fn.name == "_f@4"

    def test_msvc_export_function_fastcall_decoration_stripped_on_x86_32(self) -> None:
        fn = msvc_export_function("@f@4", is_x86_32=True)
        assert fn.entity_id is not None
        assert fn.entity_id.leaf_name == "f"

    def test_msvc_export_function_cdecl_underscore_stripped_on_x86_32(self) -> None:
        # extern "C" int f(int) (default __cdecl) exports as "_f" on
        # 32-bit x86 -- leading underscore only, no "@N" suffix.
        fn = msvc_export_function("_f", is_x86_32=True)
        assert fn.entity_id is not None
        assert fn.entity_id.leaf_name == "f"

    def test_msvc_export_function_x64_no_decoration_unaffected(self) -> None:
        # x64 PE has no calling-convention decoration at all -- the default
        # (is_x86_32=False) must NOT strip a leading underscore, since a
        # real x64 export literally named "_secret" is a distinct symbol
        # from "secret", not a decorated spelling of it (Codex review,
        # PR #1015).
        fn = msvc_export_function("_secret")
        assert fn.entity_id is not None
        assert fn.entity_id.leaf_name == "_secret"

    def test_msvc_export_function_x86_32_plain_undecorated_export(self) -> None:
        # Even on x86-32, an export with no leading underscore at all (e.g.
        # __fastcall/__stdcall's own decoration didn't apply, or a plain
        # already-undecorated name) is left as-is by the fallback branch.
        fn = msvc_export_function("f", is_x86_32=True)
        assert fn.entity_id is not None
        assert fn.entity_id.leaf_name == "f"

    def test_msvc_export_function_i686_mingw_double_underscore_itanium(self) -> None:
        # i686 MinGW prepends its own leading underscore on top of Itanium
        # mangling too -- a real "_Z3addii" export appears as "__Z3addii"
        # in the PE export table, exclusively on 32-bit x86.
        fn = msvc_export_function("__Z3addii", is_x86_32=True)
        assert fn.entity_id is not None
        assert fn.entity_id.extra == ("mangled", "_Z3addii")
        # Raw, observed evidence is preserved on the Function itself.
        assert fn.name == "__Z3addii"

    def test_msvc_export_function_x64_double_underscore_not_normalized(self) -> None:
        # Without is_x86_32, "__Z..." is never assumed to be a decorated
        # Itanium name -- an x64 export can't carry this convention at all,
        # so a literal "__Z..." export there is treated as extern-C, same
        # as any other unrecognized-prefix name.
        fn = msvc_export_function("__Z3addii")
        assert fn.entity_id is not None
        assert fn.entity_id.extra == ("extern_c",)

    def test_msvc_export_function_vectorcall_x64(self) -> None:
        # __vectorcall's own "name@@N" decoration applies on x64 too, unlike
        # __stdcall/__fastcall/__cdecl -- so this must strip even without
        # is_x86_32.
        fn = msvc_export_function("f@@8")
        assert fn.entity_id is not None
        assert fn.entity_id.extra == ("extern_c",)
        assert fn.entity_id.leaf_name == "f"
        assert fn.name == "f@@8"  # raw evidence preserved

    def test_msvc_export_function_vectorcall_x86_32_with_underscore(self) -> None:
        fn = msvc_export_function("_f@@8", is_x86_32=True)
        assert fn.entity_id is not None
        assert fn.entity_id.leaf_name == "f"

    def test_two_distinct_exports_never_collide(self) -> None:
        ids = {
            itanium_export_function(n).entity_id
            for n in ("_Z3addii", "plain_a", "plain_b")
        }
        assert len(ids) == 3


class TestMachoExportOnlyEntityId:
    def test_functions_and_variables_carry_entity_id(self, tmp_path: Path) -> None:
        import abicheck.macho_metadata as _macho
        from abicheck import dumper
        from abicheck.model.macho_facts import MachoExport, MachoMetadata

        dylib = tmp_path / "foo.dylib"
        dylib.write_bytes(b"\xcf\xfa\xed\xfe")
        meta = MachoMetadata(
            exports=[
                # macho_metadata.py already strips Mach-O's own
                # leading-underscore ABI prefix while parsing the real
                # export trie/symtab (see its own "Strip leading
                # underscore" step) -- so a real MachoExport.name for a
                # C++ export is already bare Itanium-mangled ("_Z..."),
                # not double-underscored, and a plain C export has no
                # leading underscore at all. This fixture must match that
                # already-normalized spelling; dumper._dump_macho's
                # no-headers branch must not strip again.
                MachoExport(name="_Z3addii", is_data=False),
                MachoExport(name="plain_c_fn", is_data=False),
                MachoExport(name="plain_c_var", is_data=True),
            ]
        )
        with patch.object(_macho, "parse_macho_metadata", return_value=meta):
            snap = dumper._dump_macho(dylib, [], [], "1.0", "c++")

        assert snap.from_headers is False
        mangled_fn = next(f for f in snap.functions if f.mangled == "_Z3addii")
        assert mangled_fn.entity_id is not None
        assert mangled_fn.entity_id.extra == ("mangled", "_Z3addii")

        plain_fn = next(f for f in snap.functions if f.name == "plain_c_fn")
        assert plain_fn.entity_id is not None
        assert plain_fn.entity_id.extra == ("extern_c",)

        var = next(v for v in snap.variables if v.name == "plain_c_var")
        assert var.entity_id is not None
        assert var.entity_id.kind == EntityKind.VARIABLE
        assert var.entity_id.extra == ("extern_c",)


class TestPeExportOnlyEntityId:
    def test_functions_carry_entity_id(self, tmp_path: Path) -> None:
        import abicheck.pe_metadata as _pe
        from abicheck import dumper
        from abicheck.model.pe_facts import PeExport, PeMetadata

        dll = tmp_path / "foo.dll"
        dll.write_bytes(b"MZ\x90\x00")
        meta = PeMetadata(
            exports=[
                PeExport(name="?add@@YAHHH@Z", ordinal=1),
                PeExport(name="PlainExport", ordinal=2),
                # A MinGW/GCC-built PE DLL's own C++ export mangling --
                # Itanium, not MSVC -- a real, distinct supported PE lane.
                PeExport(name="_Z3subii", ordinal=3),
            ]
        )
        with patch.object(_pe, "parse_pe_metadata", return_value=meta):
            snap = dumper._dump_pe(dll, [], [], "1.0", "c++")

        assert snap.from_headers is False
        mangled_fn = next(f for f in snap.functions if f.name == "?add@@YAHHH@Z")
        assert mangled_fn.entity_id is not None
        assert mangled_fn.entity_id.extra == ("mangled", "?add@@YAHHH@Z")

        plain_fn = next(f for f in snap.functions if f.name == "PlainExport")
        assert plain_fn.entity_id is not None
        assert plain_fn.entity_id.extra == ("extern_c",)

        mingw_fn = next(f for f in snap.functions if f.name == "_Z3subii")
        assert mingw_fn.entity_id is not None
        assert mingw_fn.entity_id.extra == ("mangled", "_Z3subii")

    def test_x86_32_stdcall_export_decoration_stripped(self, tmp_path: Path) -> None:
        import abicheck.pe_metadata as _pe
        from abicheck import dumper
        from abicheck.model.pe_facts import PeExport, PeMetadata

        dll = tmp_path / "foo32.dll"
        dll.write_bytes(b"MZ\x90\x00")
        meta = PeMetadata(
            machine="IMAGE_FILE_MACHINE_I386",
            exports=[PeExport(name="_f@4", ordinal=1)],
        )
        with patch.object(_pe, "parse_pe_metadata", return_value=meta):
            snap = dumper._dump_pe(dll, [], [], "1.0", "c")

        fn = next(f for f in snap.functions if f.name == "_f@4")
        assert fn.entity_id is not None
        assert fn.entity_id.leaf_name == "f"

    def test_x64_underscore_prefixed_export_not_stripped(self, tmp_path: Path) -> None:
        # x64 PE has no calling-convention decoration -- a real export
        # literally named "_secret" must keep its leading underscore as
        # part of its identity (Codex review, PR #1015).
        import abicheck.pe_metadata as _pe
        from abicheck import dumper
        from abicheck.model.pe_facts import PeExport, PeMetadata

        dll = tmp_path / "foo64.dll"
        dll.write_bytes(b"MZ\x90\x00")
        meta = PeMetadata(
            machine="IMAGE_FILE_MACHINE_AMD64",
            exports=[PeExport(name="_secret", ordinal=1)],
        )
        with patch.object(_pe, "parse_pe_metadata", return_value=meta):
            snap = dumper._dump_pe(dll, [], [], "1.0", "c")

        fn = next(f for f in snap.functions if f.name == "_secret")
        assert fn.entity_id is not None
        assert fn.entity_id.leaf_name == "_secret"
