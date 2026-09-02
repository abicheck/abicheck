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
                # Mach-O's own leading-underscore ABI prefix, on top of the
                # Itanium mangling's own "_Z" -- _normalize_macho_sym strips
                # exactly one, per this file's own docstring reference.
                MachoExport(name="__Z3addii", is_data=False),
                MachoExport(name="_plain_c_fn", is_data=False),
                MachoExport(name="_plain_c_var", is_data=True),
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
