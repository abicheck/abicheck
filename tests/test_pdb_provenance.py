# SPDX-License-Identifier: Apache-2.0
# Copyright The abicheck Authors.
"""PDB declaration-provenance extraction (ADR-024 Phase 1).

Exercises the source-file (``decl_file``) plumbing end to end at the layers
that are testable on Linux without MSVC: the IPI ``LF_UDT_SRC_LINE`` parser,
the ``DwarfMetadata`` ``decl_file`` field, and the ``DwarfMetadata`` → model
type bridge that lets PDB-derived types reach public-surface resolution.
"""
from __future__ import annotations

import struct
from pathlib import Path

from abicheck.dwarf_metadata import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from abicheck.model import ScopeOrigin
from abicheck.pdb_metadata import parse_pdb_debug_info
from abicheck.pdb_model import model_types_from_dwarf_metadata
from abicheck.pdb_parser import (
    LF_ENUM,
    LF_FIELDLIST,
    LF_STRING_ID,
    LF_STRUCTURE,
    LF_UDT_MOD_SRC_LINE,
    LF_UDT_SRC_LINE,
    TypeDatabase,
    _resolve_udt_source_files,
    extract_udt_source_files,
    parse_tpi_stream,
)
from abicheck.provenance import apply_provenance
from tests.test_pdb_parser import (
    _build_minimal_pdb,
    _build_tpi_stream,
    _make_lf_enum,
    _make_lf_enumerate,
    _make_lf_fieldlist,
    _make_lf_member,
    _make_lf_structure,
)


def _build_ipi_stream(records: list[tuple[int, bytes]]) -> bytes:
    """Build an IPI stream (identical record layout to TPI)."""
    ti_begin = 0x1000
    ti_end = ti_begin + len(records)
    rec_data = b""
    for leaf, payload in records:
        rec_len = 2 + len(payload)
        rec_bytes = struct.pack("<HH", rec_len, leaf) + payload
        pad = (4 - (len(rec_bytes) % 4)) % 4
        rec_bytes += b"\x00" * pad
        rec_data += rec_bytes
    header = struct.pack(
        "<IIIII", 20040203, 56, ti_begin, ti_end, len(rec_data)
    )
    header += b"\x00" * (56 - len(header))
    return header + rec_data


def _string_id(name: str) -> bytes:
    # { substr_list_id: u32, name: char[] }
    return struct.pack("<I", 0) + name.encode() + b"\x00"


def _udt_src_line(udt_ti: int, src_id: int, line: int) -> bytes:
    return struct.pack("<III", udt_ti, src_id, line)


def _udt_mod_src_line(udt_ti: int, src_id: int, line: int, mod: int) -> bytes:
    return struct.pack("<IIIH", udt_ti, src_id, line, mod)


class TestExtractUdtSourceFiles:
    def test_resolves_src_line_via_string_id(self) -> None:
        # IPI: ti 0x1000 = LF_STRING_ID("api.h"); ti 0x1001 ties UDT 0x2000 → it.
        ipi = parse_tpi_stream(
            _build_ipi_stream(
                [
                    (LF_STRING_ID, _string_id("api.h")),
                    (LF_UDT_SRC_LINE, _udt_src_line(0x2000, 0x1000, 42)),
                ]
            )
        )
        assert extract_udt_source_files(ipi) == {0x2000: "api.h"}

    def test_mod_src_line_also_resolved(self) -> None:
        ipi = parse_tpi_stream(
            _build_ipi_stream(
                [
                    (LF_STRING_ID, _string_id("detail/private.h")),
                    (LF_UDT_MOD_SRC_LINE, _udt_mod_src_line(0x2001, 0x1000, 7, 3)),
                ]
            )
        )
        assert extract_udt_source_files(ipi) == {0x2001: "detail/private.h"}

    def test_unresolvable_string_id_skipped(self) -> None:
        # src id 0x1999 has no LF_STRING_ID record → entry dropped, not crash.
        ipi = parse_tpi_stream(
            _build_ipi_stream(
                [(LF_UDT_SRC_LINE, _udt_src_line(0x2000, 0x1999, 1))]
            )
        )
        assert extract_udt_source_files(ipi) == {}

    def test_resolve_to_udt_name(self) -> None:
        # _resolve_udt_source_files maps the IPI ti→file map onto UDT *names*
        # via the TPI TypeDatabase (struct Vec3 at ti 0x1001).
        tpi = parse_tpi_stream(
            _build_tpi_stream(
                [
                    (LF_FIELDLIST, _make_lf_fieldlist([_make_lf_member(0, 0x74, 0, "x")])),
                    (LF_STRUCTURE, _make_lf_structure(1, 0, 0x1000, 4, "Vec3")),
                ]
            )
        )
        types = TypeDatabase(tpi)
        types.parse_all()
        ipi = parse_tpi_stream(
            _build_ipi_stream(
                [
                    (LF_STRING_ID, _string_id("include/vec.h")),
                    (LF_UDT_SRC_LINE, _udt_src_line(0x1001, 0x1000, 5)),
                ]
            )
        )
        assert _resolve_udt_source_files(ipi, types) == {"Vec3": "include/vec.h"}

    def test_resolve_enum_udt_name(self) -> None:
        # The enum branch of _resolve_udt_source_files: an LF_ENUM at ti 0x1001.
        tpi = parse_tpi_stream(
            _build_tpi_stream(
                [
                    (LF_FIELDLIST, _make_lf_fieldlist([_make_lf_enumerate(0, 0, "A")])),
                    (LF_ENUM, _make_lf_enum(1, 0, 0x74, 0x1000, "Color")),
                ]
            )
        )
        types = TypeDatabase(tpi)
        types.parse_all()
        ipi = parse_tpi_stream(
            _build_ipi_stream(
                [
                    (LF_STRING_ID, _string_id("include/color.h")),
                    (LF_UDT_SRC_LINE, _udt_src_line(0x1001, 0x1000, 3)),
                ]
            )
        )
        assert _resolve_udt_source_files(ipi, types) == {"Color": "include/color.h"}

    def test_resolve_empty_when_no_src_lines(self) -> None:
        # No UDT_SRC_LINE records → empty map (early-return branch).
        tpi = parse_tpi_stream(_build_tpi_stream([]))
        types = TypeDatabase(tpi)
        types.parse_all()
        ipi = parse_tpi_stream(_build_ipi_stream([(LF_STRING_ID, _string_id("x.h"))]))
        assert _resolve_udt_source_files(ipi, types) == {}

    def test_first_definition_wins(self) -> None:
        ipi = parse_tpi_stream(
            _build_ipi_stream(
                [
                    (LF_STRING_ID, _string_id("first.h")),
                    (LF_STRING_ID, _string_id("second.h")),
                    (LF_UDT_SRC_LINE, _udt_src_line(0x2000, 0x1000, 1)),
                    (LF_UDT_SRC_LINE, _udt_src_line(0x2000, 0x1001, 2)),
                ]
            )
        )
        assert extract_udt_source_files(ipi) == {0x2000: "first.h"}


class TestDwarfMetadataDeclFile:
    def test_struct_layout_carries_decl_file(self) -> None:
        layout = StructLayout(name="Vec3", byte_size=12, decl_file="include/api.h")
        assert layout.decl_file == "include/api.h"

    def test_decl_file_defaults_none(self) -> None:
        assert StructLayout(name="X", byte_size=4).decl_file is None
        assert EnumInfo(name="E", underlying_byte_size=4).decl_file is None


class TestModelBridge:
    def test_record_source_location_from_decl_file(self) -> None:
        meta = DwarfMetadata(has_dwarf=True)
        meta.structs["Vec3"] = StructLayout(
            name="Vec3",
            byte_size=12,
            fields=[FieldInfo(name="x", type_name="float", byte_offset=0, byte_size=4)],
            decl_file="include/api.h",
        )
        meta.enums["Color"] = EnumInfo(
            name="Color",
            underlying_byte_size=4,
            members={"RED": 0, "GREEN": 1},
            decl_file="include/api.h",
        )
        records, enums = model_types_from_dwarf_metadata(meta)
        assert len(records) == 1 and len(enums) == 1
        assert records[0].name == "Vec3"
        assert records[0].source_location == "include/api.h"
        assert records[0].size_bits == 96
        assert records[0].fields[0].offset_bits == 0
        assert enums[0].source_location == "include/api.h"
        assert enums[0].underlying_type == "int"

    def test_empty_metadata_yields_nothing(self) -> None:
        assert model_types_from_dwarf_metadata(None) == ([], [])
        assert model_types_from_dwarf_metadata(DwarfMetadata()) == ([], [])

    def test_bitfield_and_union_branches(self) -> None:
        meta = DwarfMetadata(has_dwarf=True)
        meta.structs["Flags"] = StructLayout(
            name="Flags",
            byte_size=4,
            alignment=4,
            is_union=False,
            fields=[
                FieldInfo(name="a", type_name="unsigned int", byte_offset=0,
                          byte_size=4, bit_offset=0, bit_size=1),
            ],
        )
        meta.structs["U"] = StructLayout(name="U", byte_size=8, is_union=True)
        records, _ = model_types_from_dwarf_metadata(meta)
        by_name = {r.name: r for r in records}
        assert by_name["Flags"].alignment_bits == 32
        assert by_name["Flags"].fields[0].is_bitfield is True
        assert by_name["Flags"].fields[0].bitfield_bits == 1
        assert by_name["U"].kind == "union" and by_name["U"].is_union is True


class TestHeaderScopeFallback:
    """The structured ``scope_fallback`` signal (ADR-024 §D5.3) returned by
    service._try_header_scoped_dump when PE/Mach-O header scoping cannot apply.
    """

    def test_castxml_unavailable_fallback(self, tmp_path, monkeypatch):
        import warnings as _w

        import abicheck.dumper as dumper
        from abicheck import service

        hdr = tmp_path / "api.h"
        hdr.write_text("int f(void);\n")

        def _boom(*a, **k):
            raise RuntimeError("castxml not found")

        monkeypatch.setattr(dumper, "_dump_pe", _boom)
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            snap, reason = service._try_header_scoped_dump(
                "pe", tmp_path / "lib.dll", [hdr], [], "1", "c++"
            )
        assert snap is None
        assert reason == "castxml-unavailable"

    def test_mangling_fallback(self, tmp_path, monkeypatch):
        import warnings as _w

        import abicheck.dumper as dumper
        from abicheck import service
        from abicheck.model import AbiSnapshot, Function, Visibility

        hdr = tmp_path / "api.h"
        hdr.write_text("int f(void);\n")

        # A snapshot whose declared symbols never matched the export table:
        # no PUBLIC-visibility symbols → _has_matched_public_surface is False.
        def _unmatched(*a, **k):
            return AbiSnapshot(
                library="lib.dll", version="1",
                functions=[Function(name="f", mangled="f", return_type="int",
                                    visibility=Visibility.HIDDEN)],
            )

        monkeypatch.setattr(dumper, "_dump_pe", _unmatched)
        with _w.catch_warnings():
            _w.simplefilter("ignore")
            snap, reason = service._try_header_scoped_dump(
                "pe", tmp_path / "lib.dll", [hdr], [], "1", "c++"
            )
        assert snap is None
        assert reason == "mangling-fallback"

    def test_scoped_success_returns_no_fallback(self, tmp_path, monkeypatch):
        import abicheck.dumper as dumper
        from abicheck import service
        from abicheck.model import AbiSnapshot, Function, Visibility

        hdr = tmp_path / "api.h"
        hdr.write_text("int f(void);\n")

        def _matched(*a, **k):
            return AbiSnapshot(
                library="lib.dll", version="1",
                functions=[Function(name="f", mangled="f", return_type="int",
                                    visibility=Visibility.PUBLIC)],
            )

        monkeypatch.setattr(dumper, "_dump_pe", _matched)
        snap, reason = service._try_header_scoped_dump(
            "pe", tmp_path / "lib.dll", [hdr], [], "1", "c++"
        )
        assert snap is not None
        assert reason is None


class TestParsePdbEndToEnd:
    def test_decl_file_flows_through_parse_pdb(self, tmp_path: Path) -> None:
        # Full path: parse_pdb parses the IPI stream, resolves UDT→source, and
        # pdb_metadata tags StructLayout.decl_file (ADR-024 Phase 1, PE path).
        tpi = [
            (LF_FIELDLIST, _make_lf_fieldlist([_make_lf_member(0, 0x74, 0, "x")])),
            (LF_STRUCTURE, _make_lf_structure(1, 0, 0x1000, 4, "Vec3")),  # ti 0x1001
        ]
        ipi = [
            (LF_STRING_ID, _string_id("include/vec.h")),                 # ti 0x1000
            (LF_UDT_SRC_LINE, _udt_src_line(0x1001, 0x1000, 9)),
        ]
        pdb = tmp_path / "e2e.pdb"
        pdb.write_bytes(_build_minimal_pdb(tpi_records=tpi, ipi_records=ipi))
        meta, _adv = parse_pdb_debug_info(pdb)
        assert "Vec3" in meta.structs
        assert meta.structs["Vec3"].decl_file == "include/vec.h"

    def test_no_ipi_leaves_decl_file_none(self, tmp_path: Path) -> None:
        tpi = [
            (LF_FIELDLIST, _make_lf_fieldlist([_make_lf_member(0, 0x74, 0, "x")])),
            (LF_STRUCTURE, _make_lf_structure(1, 0, 0x1000, 4, "Vec3")),
        ]
        pdb = tmp_path / "noipi.pdb"
        pdb.write_bytes(_build_minimal_pdb(tpi_records=tpi))  # no IPI stream
        meta, _adv = parse_pdb_debug_info(pdb)
        assert meta.structs["Vec3"].decl_file is None

    def test_cli_apply_native_provenance(self) -> None:
        # The CLI wrapper threads a PE/Mach-O snapshot through apply_provenance
        # (lifting the old ELF-only restriction).
        from abicheck.cli import _apply_native_provenance
        from abicheck.model import AbiSnapshot

        meta = DwarfMetadata(has_dwarf=True)
        meta.structs["PublicType"] = StructLayout(
            name="PublicType", byte_size=8, decl_file="include/api.h"
        )
        records, enums = model_types_from_dwarf_metadata(meta)
        snap = AbiSnapshot(library="lib.dll", version="1", types=records, enums=enums)
        out = _apply_native_provenance(snap, [Path("include/api.h")], None)
        assert out.types[0].origin == ScopeOrigin.PUBLIC_HEADER
        # No public set → no-op (origin stays UNKNOWN).
        snap2 = AbiSnapshot(
            library="lib.dll", version="1",
            types=model_types_from_dwarf_metadata(meta)[0],
        )
        out2 = _apply_native_provenance(snap2, None, None)
        assert out2.types[0].origin == ScopeOrigin.UNKNOWN

    def test_bridge_feeds_provenance_classification(self) -> None:
        # The decl_file → source_location bridge lets apply_provenance classify
        # a PDB-derived type's ScopeOrigin against a public-header set — the
        # whole point of PDB provenance (ADR-024 Phase 1) on the PE path.
        from abicheck.model import AbiSnapshot

        meta = DwarfMetadata(has_dwarf=True)
        meta.structs["PublicType"] = StructLayout(
            name="PublicType", byte_size=8, decl_file="include/api.h"
        )
        meta.structs["PrivateType"] = StructLayout(
            name="PrivateType", byte_size=8, decl_file="src/internal.h"
        )
        records, enums = model_types_from_dwarf_metadata(meta)
        snap = AbiSnapshot(library="lib.dll", version="1", types=records, enums=enums)
        apply_provenance(snap, public_headers=["include/api.h"], public_header_dirs=None)
        by_name = {t.name: t for t in snap.types}
        assert by_name["PublicType"].origin == ScopeOrigin.PUBLIC_HEADER
        assert by_name["PrivateType"].origin == ScopeOrigin.PRIVATE_HEADER


class TestDumpPeFallbackBuildsPdbTypes:
    """service._dump_pe recovers PDB-derived model types (with provenance) on
    the header-scoping fallback branch (ADR-024 Phase 1, PE path)."""

    def test_pdb_types_built_on_mangling_fallback(self, tmp_path, monkeypatch):
        import types as _t
        import warnings as _w

        from abicheck import service
        from abicheck.model import ScopeOrigin as _SO

        hdr = tmp_path / "api.h"
        hdr.write_text("struct Widget { int x; };\n")

        # Fake PE metadata: one named export, valid machine.
        fake_pe = _t.SimpleNamespace(
            machine="AMD64",
            exports=[_t.SimpleNamespace(name="Api", ordinal=1)],
        )
        monkeypatch.setattr(
            "abicheck.pe_metadata.parse_pe_metadata", lambda p: fake_pe
        )
        # PDB debug info with a public-header type.
        meta = DwarfMetadata(has_dwarf=True)
        meta.structs["Widget"] = StructLayout(
            name="Widget", byte_size=4, decl_file="api.h"
        )
        monkeypatch.setattr(service, "_extract_pdb_debug", lambda p, pp: (meta, None))
        # Header scoping falls back (castxml/mangling gap).
        monkeypatch.setattr(
            service, "_try_header_scoped_dump", lambda *a, **k: (None, "mangling-fallback")
        )

        with _w.catch_warnings():
            _w.simplefilter("ignore")
            snap = service._dump_pe(
                tmp_path / "lib.dll", "1", headers=[hdr], includes=[], lang="c++"
            )
        # PDB type recovered into the model with its source header, and the
        # structured fallback signal recorded.
        assert snap.scope_fallback == "mangling-fallback"
        names = {t.name: t for t in snap.types}
        assert "Widget" in names
        assert names["Widget"].source_location == "api.h"
        # apply_provenance (run by the CLI wrapper) would then classify it; here
        # we confirm the type reached the model so provenance is possible.
        apply_provenance(snap, public_headers=[hdr], public_header_dirs=None)
        assert names["Widget"].origin == _SO.PUBLIC_HEADER
