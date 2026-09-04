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

"""Tests for PDB metadata extraction (pdb_metadata.py).

Validates that PDB-derived metadata produces the same DwarfMetadata and
AdvancedDwarfMetadata dataclasses as the DWARF pipeline, ensuring unified
data model consistency for the checker layer.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from abicheck.dwarf_advanced import AdvancedDwarfMetadata, ToolchainInfo
from abicheck.dwarf_metadata import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from abicheck.pdb_metadata import _is_user_visible, parse_pdb_debug_info

# Import test helpers from test_pdb_parser
from tests.test_pdb_parser import (
    LF_BITFIELD,
    LF_ENUM,
    LF_FIELDLIST,
    LF_MFUNCTION,
    LF_PROCEDURE,
    LF_STRUCTURE,
    LF_UNION,
    _build_dbi_stream,
    _build_minimal_pdb,
    _make_lf_bitfield,
    _make_lf_enum,
    _make_lf_enumerate,
    _make_lf_fieldlist,
    _make_lf_member,
    _make_lf_mfunction,
    _make_lf_procedure,
    _make_lf_structure,
    _make_lf_union,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def pdb_with_struct_and_enum(tmp_path: Path) -> Path:
    """Create a PDB file with a struct, union, enum, and procedure."""
    fl_struct = _make_lf_fieldlist(
        [
            _make_lf_member(0, 0x74, 0, "x"),  # int x at offset 0
            _make_lf_member(0, 0x74, 4, "y"),  # int y at offset 4
            _make_lf_member(0, 0x41, 8, "z"),  # double z at offset 8
        ]
    )
    fl_union = _make_lf_fieldlist(
        [
            _make_lf_member(0, 0x74, 0, "i"),  # int i
            _make_lf_member(0, 0x40, 0, "f"),  # float f
        ]
    )
    fl_enum = _make_lf_fieldlist(
        [
            _make_lf_enumerate(0, 0, "NONE"),
            _make_lf_enumerate(0, 1, "READ"),
            _make_lf_enumerate(0, 2, "WRITE"),
            _make_lf_enumerate(0, 3, "READWRITE"),
        ]
    )

    records = [
        (LF_FIELDLIST, fl_struct),  # 0x1000
        (LF_STRUCTURE, _make_lf_structure(3, 0, 0x1000, 16, "Vec3")),  # 0x1001
        (LF_FIELDLIST, fl_union),  # 0x1002
        (LF_UNION, _make_lf_union(2, 0, 0x1002, 4, "Data")),  # 0x1003
        (LF_FIELDLIST, fl_enum),  # 0x1004
        (LF_ENUM, _make_lf_enum(4, 0, 0x74, 0x1004, "Access")),  # 0x1005
        # Procedures with different calling conventions
        (LF_PROCEDURE, _make_lf_procedure(0x74, 0x00, 2, 0)),  # 0x1006 cdecl
        (LF_PROCEDURE, _make_lf_procedure(0x74, 0x07, 1, 0)),  # 0x1007 stdcall
        (LF_PROCEDURE, _make_lf_procedure(0x74, 0x04, 2, 0)),  # 0x1008 fastcall
        (
            LF_MFUNCTION,
            _make_lf_mfunction(0x74, 0x1001, 0, 0x0B, 0, 0),
        ),  # 0x1009 thiscall
        (LF_PROCEDURE, _make_lf_procedure(0x74, 0x18, 3, 0)),  # 0x100A vectorcall
    ]

    dbi = _build_dbi_stream(
        machine=0x8664,
        build_major=14,
        build_minor=36,
        modules=[("foo.obj", "C:\\src\\foo.cpp")],
    )

    pdb_bytes = _build_minimal_pdb(tpi_records=records, dbi_data=dbi)
    pdb_file = tmp_path / "test.pdb"
    pdb_file.write_bytes(pdb_bytes)
    return pdb_file


@pytest.fixture()
def pdb_packed_struct(tmp_path: Path) -> Path:
    """Create a PDB with a packed struct."""
    fl = _make_lf_fieldlist(
        [
            _make_lf_member(0, 0x10, 0, "a"),  # signed char at 0
            _make_lf_member(0, 0x74, 1, "b"),  # int at 1 (packed — no padding)
        ]
    )
    records = [
        (LF_FIELDLIST, fl),
        (
            LF_STRUCTURE,
            _make_lf_structure(2, 0x0800, 0x1000, 5, "Packed"),
        ),  # packed flag
    ]
    pdb_bytes = _build_minimal_pdb(tpi_records=records)
    pdb_file = tmp_path / "packed.pdb"
    pdb_file.write_bytes(pdb_bytes)
    return pdb_file


@pytest.fixture()
def pdb_with_bitfield(tmp_path: Path) -> Path:
    """Create a PDB with a struct containing bitfields."""
    records = [
        (LF_BITFIELD, _make_lf_bitfield(0x74, 3, 0)),  # 0x1000: int:3 at bit 0
        (LF_BITFIELD, _make_lf_bitfield(0x74, 5, 3)),  # 0x1001: int:5 at bit 3
        (
            LF_FIELDLIST,
            _make_lf_fieldlist(
                [
                    _make_lf_member(0, 0x1000, 0, "flags"),
                    _make_lf_member(0, 0x1001, 0, "mode"),
                ]
            ),
        ),  # 0x1002
        (LF_STRUCTURE, _make_lf_structure(2, 0, 0x1002, 4, "BitStruct")),  # 0x1003
    ]
    pdb_bytes = _build_minimal_pdb(tpi_records=records)
    pdb_file = tmp_path / "bitfield.pdb"
    pdb_file.write_bytes(pdb_bytes)
    return pdb_file


# ---------------------------------------------------------------------------
# Data model consistency: DwarfMetadata from PDB
# ---------------------------------------------------------------------------


class TestPdbToDwarfMetadata:
    """Verify that PDB-derived DwarfMetadata matches DWARF pipeline's model."""

    def test_struct_layout(self, pdb_with_struct_and_enum: Path) -> None:
        meta, _ = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert isinstance(meta, DwarfMetadata)
        assert meta.has_dwarf is True
        assert "Vec3" in meta.structs

        vec3 = meta.structs["Vec3"]
        assert isinstance(vec3, StructLayout)
        assert vec3.name == "Vec3"
        assert vec3.byte_size == 16
        assert vec3.is_union is False
        assert len(vec3.fields) == 3

        # Field 0: int x at offset 0
        f0 = vec3.fields[0]
        assert isinstance(f0, FieldInfo)
        assert f0.name == "x"
        assert f0.byte_offset == 0
        assert f0.byte_size == 4
        assert "int" in f0.type_name

        # Field 1: int y at offset 4
        f1 = vec3.fields[1]
        assert f1.name == "y"
        assert f1.byte_offset == 4

        # Field 2: double z at offset 8
        f2 = vec3.fields[2]
        assert f2.name == "z"
        assert f2.byte_offset == 8
        assert f2.byte_size == 8

    def test_union_layout(self, pdb_with_struct_and_enum: Path) -> None:
        meta, _ = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert "Data" in meta.structs
        data = meta.structs["Data"]
        assert data.is_union is True
        assert data.byte_size == 4
        assert len(data.fields) == 2
        assert data.fields[0].name == "i"
        assert data.fields[1].name == "f"

    def test_enum_extraction(self, pdb_with_struct_and_enum: Path) -> None:
        meta, _ = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert "Access" in meta.enums
        access_enum = meta.enums["Access"]
        assert isinstance(access_enum, EnumInfo)
        assert access_enum.name == "Access"
        assert access_enum.underlying_byte_size == 4  # int
        assert len(access_enum.members) == 4
        assert access_enum.members["NONE"] == 0
        assert access_enum.members["READ"] == 1
        assert access_enum.members["WRITE"] == 2
        assert access_enum.members["READWRITE"] == 3

    def test_packed_struct(self, pdb_packed_struct: Path) -> None:
        meta, adv = parse_pdb_debug_info(pdb_packed_struct)

        assert "Packed" in meta.structs
        assert meta.structs["Packed"].byte_size == 5
        assert "Packed" in adv.packed_structs

    def test_bitfield_extraction(self, pdb_with_bitfield: Path) -> None:
        meta, _ = parse_pdb_debug_info(pdb_with_bitfield)

        assert "BitStruct" in meta.structs
        bs = meta.structs["BitStruct"]
        assert bs.byte_size == 4
        assert len(bs.fields) == 2

        f0 = bs.fields[0]
        assert f0.name == "flags"
        assert f0.bit_size == 3
        assert f0.bit_offset == 0

        f1 = bs.fields[1]
        assert f1.name == "mode"
        assert f1.bit_size == 5
        assert f1.bit_offset == 3

    def test_missing_pdb(self, tmp_path: Path) -> None:
        meta, adv = parse_pdb_debug_info(tmp_path / "nonexistent.pdb")
        assert meta.has_dwarf is False
        assert adv.has_dwarf is False
        assert meta.evidence_source == "pdb"
        assert meta.evidence_state == adv.evidence_state == "failed"

    def test_invalid_pdb(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.pdb"
        bad_file.write_bytes(b"not a pdb file" + b"\x00" * 100)
        meta, adv = parse_pdb_debug_info(bad_file)
        assert meta.has_dwarf is False
        assert adv.has_dwarf is False
        assert meta.evidence_source == "pdb"
        assert meta.evidence_state == adv.evidence_state == "failed"

    def test_one_pdb_extraction_phase_marks_its_channel_partial(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        with patch(
            "abicheck.pdb_metadata._extract_enums", side_effect=RuntimeError("bad enum")
        ):
            meta, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert meta.evidence_source == "pdb"
        assert meta.evidence_state == "partial"
        # The advanced channel is unconditionally "partial" (never "parsed")
        # regardless of what failed above it: PDB never populates
        # value_abi_traits/return_value_sizes/frame_registers/
        # callee_saved_regs/target_arch at all, and calling_conventions
        # covers methods only, never free functions (P1 review).
        assert adv.evidence_state == "partial"

    def test_advanced_channel_never_reports_parsed_on_clean_success(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """Even a fully clean PDB parse (no extraction errors at all) must not
        claim "parsed" for the advanced channel -- it structurally never
        collects several of that channel's detector inputs."""
        meta, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)
        assert meta.evidence_state == "parsed"
        assert adv.evidence_state == "partial"

    def test_skipped_struct_record_marks_both_debug_channels_partial(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """Per-record recovery must not claim complete PDB evidence."""
        with patch(
            "abicheck.pdb_metadata._extract_fields",
            side_effect=RuntimeError("bad record"),
        ):
            meta, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert meta.has_dwarf and adv.has_dwarf
        assert meta.evidence_state == adv.evidence_state == "partial"

    def test_truncated_tpi_stream_marks_basic_channel_partial(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """P2 review, fresh evidence: ``parse_tpi_stream()`` can stop before
        every promised type index was consumed (``TpiStream.truncated``)
        while ``pdb.types`` is still non-``None`` -- the basic channel must
        not read ``parsed`` for that shape."""
        from abicheck import pdb_metadata as pdb_metadata_mod

        real_parse_pdb = pdb_metadata_mod.parse_pdb

        def _with_truncated_tpi(path):  # noqa: ANN001, ANN202
            pdb = real_parse_pdb(path)
            assert pdb.tpi is not None
            pdb.tpi.truncated = True
            return pdb

        with patch.object(
            pdb_metadata_mod, "parse_pdb", side_effect=_with_truncated_tpi
        ):
            meta, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert meta.has_dwarf is True
        assert meta.evidence_state == "partial"

    def test_failed_tpi_records_mark_basic_channel_partial(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """Sibling shape: the stream parsed in full (not truncated), but
        one or more individual records failed to decode
        (``TypeDatabase.failed_record_count``) -- same required outcome."""
        from abicheck import pdb_metadata as pdb_metadata_mod

        real_parse_pdb = pdb_metadata_mod.parse_pdb

        def _with_failed_records(path):  # noqa: ANN001, ANN202
            pdb = real_parse_pdb(path)
            assert pdb.types is not None
            pdb.types.failed_record_count = 1
            return pdb

        with patch.object(
            pdb_metadata_mod, "parse_pdb", side_effect=_with_failed_records
        ):
            meta, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert meta.has_dwarf is True
        assert meta.evidence_state == "partial"

    def test_clean_tpi_stream_reports_parsed(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """Positive control: neither a truncated stream nor a failed record
        -- the basic channel legitimately reads ``parsed`` (mirrors
        ``test_advanced_channel_never_reports_parsed_on_clean_success``,
        stated explicitly here as this fix's own positive control)."""
        meta, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)
        assert meta.evidence_state == "parsed"

    def test_struct_with_missing_fieldlist_marks_partial_and_is_dropped(
        self, tmp_path: Path
    ) -> None:
        """P2 review, fresh evidence (Codex): a fully-framed LF_STRUCTURE
        whose field_list_ti names a type index the TPI stream never
        defined previously fell through TypeDatabase.get_fieldlist()'s
        empty-list default -- indistinguishable from a struct that
        legitimately has zero fields -- and _extract_struct_layouts()
        recorded it with a fabricated empty layout while evidence_state
        stayed "parsed". The struct must now be dropped (same per-record
        skip-and-continue as any other malformed-field extraction failure)
        and the channel marked partial, exercised through the public
        parser rather than only at the TypeDatabase/helper level."""
        records = [
            (LF_STRUCTURE, _make_lf_structure(2, 0, 0x9999, 8, "Bad")),  # 0x1000
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "missing_fieldlist.pdb"
        pdb_file.write_bytes(pdb_bytes)

        meta, adv = parse_pdb_debug_info(pdb_file)

        assert meta.evidence_state == "partial"
        assert adv.evidence_state == "partial"
        assert "Bad" not in meta.structs

    def test_struct_with_wrong_kind_fieldlist_marks_partial_and_is_dropped(
        self, tmp_path: Path
    ) -> None:
        """Sibling shape: field_list_ti resolves to a real record, but one
        that isn't LF_FIELDLIST at all (here, another struct) -- same
        get_fieldlist() empty-list collapse, same required outcome."""
        records = [
            (LF_STRUCTURE, _make_lf_structure(0, 0, 0, 4, "Other")),  # 0x1000
            (
                LF_STRUCTURE,
                _make_lf_structure(2, 0, 0x1000, 8, "Bad"),
            ),  # 0x1001, field_ti points at 0x1000 (a struct, not a fieldlist)
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "wrong_kind_fieldlist.pdb"
        pdb_file.write_bytes(pdb_bytes)

        meta, adv = parse_pdb_debug_info(pdb_file)

        assert meta.evidence_state == "partial"
        assert "Bad" not in meta.structs
        # The unaffected struct is unaffected by its sibling's bad reference.
        assert "Other" in meta.structs

    def test_enum_with_missing_fieldlist_marks_partial_but_keeps_definition(
        self, tmp_path: Path
    ) -> None:
        """Enum sibling of the struct case: the enum's own name/underlying
        size is still recorded (mirrors the struct-level per-record skip
        principle of not discarding good structural evidence alongside
        bad), but its member list must not be silently reported empty."""
        records = [
            (LF_ENUM, _make_lf_enum(3, 0, 0x74, 0x9999, "BadEnum")),  # 0x1000
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "missing_enum_fieldlist.pdb"
        pdb_file.write_bytes(pdb_bytes)

        meta, adv = parse_pdb_debug_info(pdb_file)

        assert meta.evidence_state == "partial"
        assert "BadEnum" in meta.enums
        assert meta.enums["BadEnum"].members == {}

    def test_zero_field_list_ti_is_not_flagged_partial(self, tmp_path: Path) -> None:
        """Positive control: field_list_ti == 0 means "no fields" by
        CodeView convention (an opaque/forward-declared-with-no-body
        struct, or a valueless enum) -- this is not the same shape as an
        unresolvable reference and must not be flagged."""
        records = [
            (LF_STRUCTURE, _make_lf_structure(0, 0, 0, 0, "Empty")),  # 0x1000
            (LF_ENUM, _make_lf_enum(0, 0, 0x74, 0, "EmptyEnum")),  # 0x1001
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "zero_fieldlist.pdb"
        pdb_file.write_bytes(pdb_bytes)

        meta, adv = parse_pdb_debug_info(pdb_file)

        assert meta.evidence_state == "parsed"
        assert meta.structs["Empty"].fields == []
        assert meta.enums["EmptyEnum"].members == {}


# ---------------------------------------------------------------------------
# Data model consistency: AdvancedDwarfMetadata from PDB
# ---------------------------------------------------------------------------


class TestPdbToAdvancedDwarfMetadata:
    """Verify that PDB-derived AdvancedDwarfMetadata matches DWARF pipeline's model."""

    def test_calling_conventions_not_populated(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """calling_conventions is intentionally empty — TPI type indices are
        not stable across builds, so per-function matching would cause false
        positives in diff_advanced_dwarf().  Populating this dict requires
        stable function identities (linkage names) from the PDB symbol stream.
        """
        _, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert isinstance(adv, AdvancedDwarfMetadata)
        assert adv.has_dwarf is True
        assert adv.calling_conventions == {}

    def test_packed_structs_in_advanced(self, pdb_packed_struct: Path) -> None:
        _, adv = parse_pdb_debug_info(pdb_packed_struct)
        assert "Packed" in adv.packed_structs
        assert "Packed" in adv.all_struct_names

    def test_toolchain_info(self, pdb_with_struct_and_enum: Path) -> None:
        _, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert isinstance(adv.toolchain, ToolchainInfo)
        assert adv.toolchain.compiler == "MSVC"
        assert "MSVC" in adv.toolchain.producer_string
        assert adv.toolchain.version  # should have a version string
        assert "-m64" in adv.toolchain.abi_flags  # AMD64

    def test_toolchain_x86(self, tmp_path: Path) -> None:
        dbi = _build_dbi_stream(machine=0x014C, build_major=19, build_minor=42)
        pdb_bytes = _build_minimal_pdb(dbi_data=dbi)
        pdb_file = tmp_path / "x86.pdb"
        pdb_file.write_bytes(pdb_bytes)
        _, adv = parse_pdb_debug_info(pdb_file)
        assert adv.toolchain.compiler == "MSVC"
        assert "-m32" in adv.toolchain.abi_flags

    def test_toolchain_arm64(self, tmp_path: Path) -> None:
        dbi = _build_dbi_stream(machine=0xAA64, build_major=14, build_minor=36)
        pdb_bytes = _build_minimal_pdb(dbi_data=dbi)
        pdb_file = tmp_path / "arm64.pdb"
        pdb_file.write_bytes(pdb_bytes)
        _, adv = parse_pdb_debug_info(pdb_file)
        assert "-marm64" in adv.toolchain.abi_flags

    def test_toolchain_unknown_machine(self, tmp_path: Path) -> None:
        dbi = _build_dbi_stream(machine=0xFFFF, build_major=14, build_minor=36)
        pdb_bytes = _build_minimal_pdb(dbi_data=dbi)
        pdb_file = tmp_path / "unknown.pdb"
        pdb_file.write_bytes(pdb_bytes)
        _, adv = parse_pdb_debug_info(pdb_file)
        assert "0xffff" in adv.toolchain.producer_string

    def test_toolchain_incremental_link(self, tmp_path: Path) -> None:
        dbi = _build_dbi_stream(machine=0x8664, flags=0x01)
        pdb_bytes = _build_minimal_pdb(dbi_data=dbi)
        pdb_file = tmp_path / "incr.pdb"
        pdb_file.write_bytes(pdb_bytes)
        _, adv = parse_pdb_debug_info(pdb_file)
        assert "/INCREMENTAL" in adv.toolchain.abi_flags

    def test_toolchain_no_incremental(self, tmp_path: Path) -> None:
        dbi = _build_dbi_stream(machine=0x8664, flags=0x00)
        pdb_bytes = _build_minimal_pdb(dbi_data=dbi)
        pdb_file = tmp_path / "no_incr.pdb"
        pdb_file.write_bytes(pdb_bytes)
        _, adv = parse_pdb_debug_info(pdb_file)
        assert "/INCREMENTAL" not in adv.toolchain.abi_flags

    def test_toolchain_msvc_version_from_module(self, tmp_path: Path) -> None:
        """Module obj paths with MSVC version patterns should override version."""
        dbi = _build_dbi_stream(
            machine=0x8664,
            modules=[
                ("foo.obj", r"C:\Program Files\MSVC\14.36.32532\lib\foo.obj"),
            ],
        )
        pdb_bytes = _build_minimal_pdb(dbi_data=dbi)
        pdb_file = tmp_path / "msvc_ver.pdb"
        pdb_file.write_bytes(pdb_bytes)
        _, adv = parse_pdb_debug_info(pdb_file)
        assert adv.toolchain.version == "14.36.32532"
        assert "14.36.32532" in adv.toolchain.producer_string

    def test_no_dbi_stream(self, tmp_path: Path) -> None:
        """PDB without DBI stream should still parse (no toolchain info)."""
        # Build PDB with empty DBI that's too small — will fail DBI parse
        # but parse_pdb_debug_info should handle it gracefully.
        # Actually, _extract_toolchain_info handles pdb.dbi is None.
        from unittest.mock import patch

        pdb_bytes = _build_minimal_pdb(tpi_records=[])
        pdb_file = tmp_path / "no_dbi.pdb"
        pdb_file.write_bytes(pdb_bytes)

        # Patch parse_pdb to return a PdbFile with no DBI
        from abicheck.pdb_parser import parse_pdb

        original = parse_pdb

        def mock_parse(path):
            result = original(path)
            result.dbi = None
            return result

        with patch("abicheck.pdb_metadata.parse_pdb", side_effect=mock_parse):
            meta, adv = parse_pdb_debug_info(pdb_file)

        assert meta.has_dwarf is True
        # No toolchain since DBI is None
        assert adv.toolchain.compiler == ""

    def test_struct_names_tracked(self, pdb_with_struct_and_enum: Path) -> None:
        _, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)
        assert "Vec3" in adv.all_struct_names
        assert "Data" in adv.all_struct_names


# ---------------------------------------------------------------------------
# _is_user_visible -- compiler-internal / anonymous name filtering
# ---------------------------------------------------------------------------

class TestIsUserVisible:
    """``_is_user_visible`` must reject a compiler-internal/anonymous name
    wherever it appears in a qualified name, not just as the whole string's
    own prefix (Codex review, PR #1025) -- CodeView emits a fully-qualified
    name for a nested anonymous aggregate too, e.g. ``"N::O::<unnamed-tag>"``
    for an unnamed struct/union nested inside ``N::O``."""

    def test_forward_ref_rejected(self) -> None:
        assert _is_user_visible("Widget", is_forward_ref=True) is False

    def test_none_name_rejected(self) -> None:
        assert _is_user_visible(None, is_forward_ref=False) is False

    def test_ordinary_qualified_name_accepted(self) -> None:
        assert _is_user_visible("NS::Widget", is_forward_ref=False) is True

    def test_whole_name_compiler_internal_rejected(self) -> None:
        assert _is_user_visible("<unnamed-tag>", is_forward_ref=False) is False
        assert _is_user_visible("__vc_attributes", is_forward_ref=False) is False

    def test_nested_anonymous_leaf_rejected(self) -> None:
        """The bug this test guards against: only the leaf segment is
        compiler-internal, but the qualified name as a whole does not start
        with ``<``/``__``."""
        assert _is_user_visible("N::O::<unnamed-tag>", is_forward_ref=False) is False

    def test_nested_anonymous_middle_segment_rejected(self) -> None:
        """The same defect could also hide in a *middle* segment, not just
        the leaf -- e.g. a named type nested inside an anonymous union
        that is itself nested inside a named enclosing type."""
        assert (
            _is_user_visible("N::<unnamed-tag>::Inner", is_forward_ref=False) is False
        )

    def test_compiler_internal_middle_segment_still_rejected(self) -> None:
        """A genuinely compiler-internal ``__``-prefixed segment (MSVC's own
        ``__vc_attributes``, not a recognized ABI-tag inline namespace) must
        still be rejected wherever it appears, including a middle segment --
        this is exactly what the per-segment ``__`` check exists to catch,
        and narrowing it for ABI-tag namespaces (below) must not widen it."""
        assert (
            _is_user_visible("N::__vc_attributes::Inner", is_forward_ref=False) is False
        )

    def test_libcxx_abi_inline_namespace_preserved(self) -> None:
        """libc++'s ``std::__1`` ABI-tag inline namespace must NOT be treated
        as compiler-internal: ``std::__1::vector<int>`` is a real,
        user-visible type whose layout facts/entity ID/SemanticIR occurrence
        would otherwise be silently dropped (Codex review, PR #1025)."""
        assert _is_user_visible("std::__1::vector<int>", is_forward_ref=False) is True

    def test_libstdcxx_abi_inline_namespace_preserved(self) -> None:
        """libstdc++'s ``std::__cxx11`` dual-ABI inline namespace must NOT be
        treated as compiler-internal, for the same reason as libc++'s
        ``__1`` above."""
        assert (
            _is_user_visible("std::__cxx11::basic_string<char>", is_forward_ref=False)
            is True
        )

    def test_customized_libcxx_abi_namespace_preserved(self) -> None:
        """Codex review, third round, fresh evidence: `_LIBCPP_ABI_NAMESPACE`
        is a documented, build-configurable macro, so a vendor's own
        customized spelling (e.g. `__vendor`) is a real, legitimate inline
        namespace even though it matches no known-standard ABI-tag shape
        (`__1`, `__cxx11`, ...). The non-leaf admit-by-default rule must
        preserve it the same way a recognized tag is preserved -- rejecting
        an unrecognized-but-real ABI-tag namespace would drop this
        declaration's layout facts, entity ID, and SemanticIR occurrence
        entirely."""
        assert _is_user_visible("std::__vendor::Widget", is_forward_ref=False) is True

    def test_abi_inline_namespace_leaf_name_still_checked(self) -> None:
        """A recognized ABI-tag namespace segment does not exempt an
        otherwise-anonymous leaf nested inside it."""
        assert (
            _is_user_visible("std::__1::<unnamed-tag>", is_forward_ref=False) is False
        )

    def test_abi_tag_spelled_leaf_declaration_still_rejected(self) -> None:
        """Codex review, second round, fresh evidence: the ABI-tag exemption
        applies only to a non-leaf (enclosing-scope) segment. A globally
        named UDT whose own declaration leaf happens to be spelled
        ``__1``/``__v2``/``__cxx11`` is not an inline namespace at all --
        admitting it would add spurious layout facts, entity IDs, and
        SemanticIR occurrences for what is, from CodeView's own name, a
        compiler-reserved-looking declaration, not a real inline-namespace
        member."""
        assert _is_user_visible("__1", is_forward_ref=False) is False
        assert _is_user_visible("__v2", is_forward_ref=False) is False
        assert _is_user_visible("__cxx11", is_forward_ref=False) is False

    def test_abi_tag_spelled_leaf_rejected_under_a_real_namespace_too(self) -> None:
        """The same leaf-vs-non-leaf distinction holds when the ABI-tag-
        spelled name is itself the declared type, nested under an ordinary
        (non-ABI-tag) namespace -- only the ENCLOSING ``__1``/``__cxx11``
        segment is ever transparent, never the leaf itself."""
        assert _is_user_visible("NS::__1", is_forward_ref=False) is False
        assert _is_user_visible("NS::__cxx11", is_forward_ref=False) is False


# ---------------------------------------------------------------------------
# Data model structural consistency
# ---------------------------------------------------------------------------


class TestDataModelConsistency:
    """Verify PDB output is structurally compatible with DWARF output.

    The checker's _diff_dwarf() and _diff_advanced_dwarf() functions operate
    on DwarfMetadata and AdvancedDwarfMetadata. These tests ensure PDB
    produces output that these functions can consume without error.
    """

    def test_dwarf_metadata_fields_present(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """DwarfMetadata from PDB has all required attributes."""
        meta, _ = parse_pdb_debug_info(pdb_with_struct_and_enum)

        # Core attributes
        assert hasattr(meta, "structs")
        assert hasattr(meta, "enums")
        assert hasattr(meta, "has_dwarf")

        # Types match
        assert isinstance(meta.structs, dict)
        assert isinstance(meta.enums, dict)
        assert isinstance(meta.has_dwarf, bool)

    def test_struct_layout_fields_present(self, pdb_with_struct_and_enum: Path) -> None:
        """StructLayout from PDB has all required attributes for checker."""
        meta, _ = parse_pdb_debug_info(pdb_with_struct_and_enum)
        for name, layout in meta.structs.items():
            assert isinstance(layout.name, str)
            assert isinstance(layout.byte_size, int)
            assert isinstance(layout.alignment, int)
            assert isinstance(layout.is_union, bool)
            assert isinstance(layout.fields, list)
            for f in layout.fields:
                assert isinstance(f.name, str)
                assert isinstance(f.type_name, str)
                assert isinstance(f.byte_offset, int)
                assert isinstance(f.byte_size, int)
                assert isinstance(f.bit_offset, int)
                assert isinstance(f.bit_size, int)

    def test_enum_info_fields_present(self, pdb_with_struct_and_enum: Path) -> None:
        """EnumInfo from PDB has all required attributes for checker."""
        meta, _ = parse_pdb_debug_info(pdb_with_struct_and_enum)
        for name, info in meta.enums.items():
            assert isinstance(info.name, str)
            assert isinstance(info.underlying_byte_size, int)
            assert isinstance(info.members, dict)
            for member_name, member_val in info.members.items():
                assert isinstance(member_name, str)
                assert isinstance(member_val, int)

    def test_advanced_metadata_fields_present(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """AdvancedDwarfMetadata from PDB has all required attributes."""
        _, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)

        assert isinstance(adv.has_dwarf, bool)
        assert isinstance(adv.toolchain, ToolchainInfo)
        assert isinstance(adv.calling_conventions, dict)
        assert isinstance(adv.packed_structs, set)
        assert isinstance(adv.all_struct_names, set)
        assert isinstance(adv.value_abi_traits, dict)
        assert isinstance(adv.frame_registers, dict)

    def test_toolchain_info_fields_present(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """ToolchainInfo from PDB has all required attributes."""
        _, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)
        tc = adv.toolchain
        assert isinstance(tc.producer_string, str)
        assert isinstance(tc.compiler, str)
        assert isinstance(tc.version, str)
        assert isinstance(tc.abi_flags, set)

    def test_checker_diff_dwarf_compatible(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """Simulate what _diff_dwarf does: iterate structs and enums."""
        meta, _ = parse_pdb_debug_info(pdb_with_struct_and_enum)

        # _diff_dwarf iterates over structs and compares fields
        for name, layout in meta.structs.items():
            assert layout.byte_size >= 0
            for field in layout.fields:
                # Field offsets should be non-negative
                assert field.byte_offset >= 0
                assert field.byte_size >= 0

        # _diff_dwarf iterates over enums and compares members
        for name, info in meta.enums.items():
            assert info.underlying_byte_size >= 0
            for member_name, val in info.members.items():
                assert isinstance(val, int)

    def test_checker_diff_advanced_compatible(
        self, pdb_with_struct_and_enum: Path
    ) -> None:
        """Simulate what _diff_advanced_dwarf does: compare CC, packing, toolchain."""
        _, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)

        # _diff_advanced_dwarf compares calling_conventions
        for key, cc in adv.calling_conventions.items():
            assert isinstance(key, str)
            assert isinstance(cc, str)

        # _diff_advanced_dwarf compares packed_structs
        for name in adv.packed_structs:
            assert name in adv.all_struct_names

        # _diff_advanced_dwarf compares toolchain flags
        assert isinstance(adv.toolchain.abi_flags, set)
        for flag in adv.toolchain.abi_flags:
            assert isinstance(flag, str)


# ---------------------------------------------------------------------------
# Integration: PDB metadata used in AbiSnapshot comparison
# ---------------------------------------------------------------------------


class TestPdbInAbiSnapshot:
    """Test that PDB metadata integrates into the AbiSnapshot model."""

    def test_snapshot_with_pdb_dwarf(self, pdb_with_struct_and_enum: Path) -> None:
        from abicheck.model import AbiSnapshot

        meta, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)
        snap = AbiSnapshot(
            library="test.dll",
            version="1.0",
            dwarf=meta,
            dwarf_advanced=adv,
            platform="pe",
        )

        assert snap.dwarf is not None
        assert snap.dwarf.has_dwarf
        assert snap.dwarf_advanced is not None
        assert snap.dwarf_advanced.has_dwarf
        assert snap.platform == "pe"

    def test_no_tpi_stream(self, tmp_path: Path) -> None:
        """PDB without TPI should return empty metadata."""
        # Create a PDB with no records — tpi.records will be empty,
        # but pdb.types will still be created. Instead, test via a
        # struct.error by corrupting TPI.
        pdb_bytes = _build_minimal_pdb(tpi_records=[])
        pdb_file = tmp_path / "empty_tpi.pdb"
        pdb_file.write_bytes(pdb_bytes)
        meta, adv = parse_pdb_debug_info(pdb_file)
        # Has dwarf because TPI stream exists, just no types
        assert isinstance(meta, DwarfMetadata)

    def test_forward_ref_struct_skipped(self, tmp_path: Path) -> None:
        """Forward-ref structs should not appear in metadata."""
        records = [
            (LF_STRUCTURE, _make_lf_structure(0, 0x0080, 0, 0, "FwdOnly")),
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "fwd.pdb"
        pdb_file.write_bytes(pdb_bytes)
        meta, _ = parse_pdb_debug_info(pdb_file)
        assert "FwdOnly" not in meta.structs

    def test_anonymous_struct_skipped(self, tmp_path: Path) -> None:
        """Structs with names starting with '<' or '__' should be skipped."""
        fl = _make_lf_fieldlist([_make_lf_member(0, 0x74, 0, "x")])
        records = [
            (LF_FIELDLIST, fl),
            (LF_STRUCTURE, _make_lf_structure(1, 0, 0x1000, 4, "<unnamed>")),
            (LF_STRUCTURE, _make_lf_structure(1, 0, 0x1000, 4, "__internal")),
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "anon.pdb"
        pdb_file.write_bytes(pdb_bytes)
        meta, _ = parse_pdb_debug_info(pdb_file)
        assert "<unnamed>" not in meta.structs
        assert "__internal" not in meta.structs

    def test_empty_struct_name_skipped(self, tmp_path: Path) -> None:
        """Structs with empty names should be skipped."""
        fl = _make_lf_fieldlist([_make_lf_member(0, 0x74, 0, "x")])
        records = [
            (LF_FIELDLIST, fl),
            (LF_STRUCTURE, _make_lf_structure(1, 0, 0x1000, 4, "")),
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "noname.pdb"
        pdb_file.write_bytes(pdb_bytes)
        meta, _ = parse_pdb_debug_info(pdb_file)
        assert "" not in meta.structs

    def test_forward_ref_enum_skipped(self, tmp_path: Path) -> None:
        """Forward-ref enums should not appear in metadata."""
        records = [
            (LF_ENUM, _make_lf_enum(0, 0x0080, 0x74, 0, "FwdEnum")),
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "fwd_enum.pdb"
        pdb_file.write_bytes(pdb_bytes)
        meta, _ = parse_pdb_debug_info(pdb_file)
        assert "FwdEnum" not in meta.enums

    def test_anonymous_enum_skipped(self, tmp_path: Path) -> None:
        """Enums with names starting with '<' or '__' should be skipped."""
        fl = _make_lf_fieldlist([_make_lf_enumerate(0, 0, "A")])
        records = [
            (LF_FIELDLIST, fl),
            (LF_ENUM, _make_lf_enum(1, 0, 0x74, 0x1000, "<unnamed-enum>")),
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "anon_enum.pdb"
        pdb_file.write_bytes(pdb_bytes)
        meta, _ = parse_pdb_debug_info(pdb_file)
        assert "<unnamed-enum>" not in meta.enums

    def test_empty_fieldlist_struct(self, tmp_path: Path) -> None:
        """Struct with field_list_ti=0 should have no fields."""
        records = [
            (LF_STRUCTURE, _make_lf_structure(0, 0, 0, 0, "Empty")),
        ]
        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "empty_struct.pdb"
        pdb_file.write_bytes(pdb_bytes)
        meta, _ = parse_pdb_debug_info(pdb_file)
        assert "Empty" in meta.structs
        assert len(meta.structs["Empty"].fields) == 0

    def test_serialization_round_trip(
        self, pdb_with_struct_and_enum: Path, tmp_path: Path
    ) -> None:
        """DwarfMetadata from PDB should survive JSON serialization."""
        import json

        from abicheck.model import AbiSnapshot
        from abicheck.serialization import snapshot_from_dict, snapshot_to_json

        meta, adv = parse_pdb_debug_info(pdb_with_struct_and_enum)
        snap = AbiSnapshot(
            library="test.dll",
            version="1.0",
            dwarf=meta,
            dwarf_advanced=adv,
            platform="pe",
        )

        json_str = snapshot_to_json(snap)
        loaded = snapshot_from_dict(json.loads(json_str))

        assert loaded.dwarf is not None
        assert loaded.dwarf.has_dwarf
        assert "Vec3" in loaded.dwarf.structs
        assert loaded.dwarf.structs["Vec3"].byte_size == 16
        assert "Access" in loaded.dwarf.enums
        assert loaded.dwarf.enums["Access"].members["READ"] == 1

        assert loaded.dwarf_advanced is not None
        assert loaded.dwarf_advanced.has_dwarf
        assert loaded.dwarf_advanced.toolchain.compiler == "MSVC"
