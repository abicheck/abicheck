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

"""Tests for the minimal PDB parser (pdb_parser.py).

These tests validate the MSF container parser, TPI stream parser,
numeric leaf decoding, type database resolution, and DBI stream parsing
using synthetically constructed PDB-like byte streams.

Data model consistency tests ensure that PDB-derived data produces the
same DwarfMetadata / AdvancedDwarfMetadata structures as the DWARF pipeline.
"""
from __future__ import annotations

import math
import struct
from pathlib import Path

import pytest

from abicheck.pdb_parser import (
    _CC_NAMES,
    LF_ARGLIST,
    LF_ARRAY,
    LF_BCLASS,
    LF_BITFIELD,
    LF_CHAR,
    LF_ENUM,
    LF_ENUMERATE,
    LF_FIELDLIST,
    LF_INDEX,
    LF_LONG,
    LF_MEMBER,
    LF_METHOD,
    LF_MFUNCTION,
    LF_MODIFIER,
    LF_NESTTYPE,
    LF_ONEMETHOD,
    LF_POINTER,
    LF_PROCEDURE,
    LF_QUADWORD,
    LF_SHORT,
    LF_STMEMBER,
    LF_STRUCTURE,
    LF_ULONG,
    LF_UNION,
    LF_UQUADWORD,
    LF_USHORT,
    LF_VBCLASS,
    LF_VFUNCTAB,
    TypeDatabase,
    _read_cstring,
    _read_numeric_leaf,
    parse_dbi_stream,
    parse_msf,
    parse_tpi_stream,
)

# ---------------------------------------------------------------------------
# Helper: build a minimal MSF PDB file in memory
# ---------------------------------------------------------------------------

_BLOCK_SIZE = 4096
_MSF_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1a\x44\x53\x00\x00\x00"


def _pad_block(data: bytes, block_size: int = _BLOCK_SIZE) -> bytes:
    """Pad data to a multiple of block_size."""
    remainder = len(data) % block_size
    if remainder:
        data += b"\x00" * (block_size - remainder)
    return data


def _build_tpi_stream(records: list[tuple[int, bytes]]) -> bytes:
    """Build a TPI stream from a list of (leaf_type, payload) tuples.

    Returns the raw TPI stream bytes (header + records).
    """
    ti_begin = 0x1000
    ti_end = ti_begin + len(records)

    # Build record data
    rec_data = b""
    for leaf, payload in records:
        rec_len = 2 + len(payload)  # leaf (2) + payload
        rec_bytes = struct.pack("<HH", rec_len, leaf) + payload
        # 4-byte alignment
        pad = (4 - (len(rec_bytes) % 4)) % 4
        rec_bytes += b"\x00" * pad
        rec_data += rec_bytes

    # TPI header (56 bytes)
    version = 20040203
    header_size = 56
    # The rest of the header fields (hash info) — zeros
    header = struct.pack("<IIIII", version, header_size, ti_begin, ti_end, len(rec_data))
    header += b"\x00" * (header_size - len(header))

    return header + rec_data


def _build_dbi_stream(
    machine: int = 0x8664,
    build_major: int = 14,
    build_minor: int = 36,
    flags: int = 0,
    modules: list[tuple[str, str]] | None = None,
) -> bytes:
    """Build a minimal DBI stream.

    Args:
        machine: CPU type (0x8664 = AMD64, 0x014C = x86)
        build_major: major version (bits 8-14 of build_number)
        build_minor: minor version (bits 0-7 of build_number)
        flags: DBI flags
        modules: list of (module_name, obj_file_name) pairs
    """
    modules = modules or []
    build_number = (build_major << 8) | build_minor | 0x8000  # new format flag

    # Build module info substream
    mod_data = b""
    for mod_name, obj_name in modules:
        # Fixed part (64 bytes)
        fixed = struct.pack(
            "<IHHiiIHHIIHHIIIHHIII",
            0,              # Unused1
            0, 0,           # Section, Padding1
            0, 0,           # Offset, Size
            0,              # Characteristics
            0, 0,           # ModuleIndex, Padding2
            0, 0,           # DataCrc, RelocCrc
            0,              # Flags
            0xFFFF,         # ModuleSymStream (nil)
            0, 0, 0,        # SymByteSize, C11ByteSize, C13ByteSize
            0, 0,           # SourceFileCount, Padding
            0,              # Unused2
            0, 0,           # SourceFileNameIndex, PdbFilePathNameIndex
        )
        entry = fixed + mod_name.encode() + b"\x00" + obj_name.encode() + b"\x00"
        # 4-byte align
        pad = (4 - (len(entry) % 4)) % 4
        entry += b"\x00" * pad
        mod_data += entry

    # DBI header (64 bytes)
    header = struct.pack(
        "<iIIHHHHHHiiiiiIiiHHI",
        -1,                     # VersionSignature
        20040203,               # VersionHeader
        1,                      # Age
        0xFFFF,                 # GlobalStreamIndex
        build_number,           # BuildNumber
        0xFFFF,                 # PublicStreamIndex
        0,                      # PdbDllVersion
        0xFFFF,                 # SymRecordStream
        0,                      # PdbDllRbld (skipped in unpack — mapped as fields[8])
        len(mod_data),          # ModInfoSize
        0,                      # SectionContributionSize
        0,                      # SectionMapSize
        0,                      # SourceInfoSize
        0,                      # TypeServerMapSize
        0,                      # MFCTypeServerIndex
        0,                      # OptionalDbgHeaderSize
        0,                      # ECSubstreamSize
        flags,                  # Flags
        machine,                # Machine
        0,                      # Padding
    )

    return header + mod_data


def _build_minimal_pdb(
    tpi_records: list[tuple[int, bytes]] | None = None,
    dbi_data: bytes | None = None,
    ipi_records: list[tuple[int, bytes]] | None = None,
) -> bytes:
    """Construct a minimal valid PDB 7.0 file in memory.

    The file contains:
    - Block 0: superblock
    - Block 1: FPM1
    - Block 2: FPM2
    - Blocks 3+: stream directory and stream data

    When *ipi_records* is provided, a stream 4 (IPI) is appended carrying those
    records (same on-disk layout as TPI), enabling UDT source-file provenance
    tests (ADR-024 Phase 1).
    """
    tpi_data = _build_tpi_stream(tpi_records or [])
    dbi_data = dbi_data or _build_dbi_stream()

    # Stream 0: Old MSF directory (empty)
    # Stream 1: PDB info stream (minimal)
    pdb_info = struct.pack("<III", 20000404, 0, 1)  # version, signature, age
    pdb_info += b"\x00" * 16  # GUID
    # Stream 2: TPI
    # Stream 3: DBI

    streams = [
        b"",        # Stream 0 (old directory)
        pdb_info,   # Stream 1 (PDB info)
        tpi_data,   # Stream 2 (TPI)
        dbi_data,   # Stream 3 (DBI)
    ]
    if ipi_records is not None:
        streams.append(_build_tpi_stream(ipi_records))  # Stream 4 (IPI)

    # Allocate blocks for each stream
    # First 3 blocks: superblock, FPM1, FPM2
    next_block = 5  # blocks 3-4 reserved for stream directory

    stream_sizes = [len(s) for s in streams]
    stream_blocks: list[list[int]] = []

    for s in streams:
        n_blocks = math.ceil(len(s) / _BLOCK_SIZE) if s else 0
        blocks = list(range(next_block, next_block + n_blocks))
        stream_blocks.append(blocks)
        next_block += n_blocks

    # Build stream directory
    dir_data = struct.pack("<I", len(streams))
    for sz in stream_sizes:
        dir_data += struct.pack("<i", sz)
    for blocks in stream_blocks:
        for b in blocks:
            dir_data += struct.pack("<I", b)

    dir_blocks_needed = math.ceil(len(dir_data) / _BLOCK_SIZE) or 1
    # Place directory at blocks 3,4,...
    dir_block_list = list(range(3, 3 + dir_blocks_needed))
    # Block map block: points to the directory block indices
    block_map_block = 3 + dir_blocks_needed
    if block_map_block >= next_block:
        next_block = block_map_block + 1

    num_blocks = next_block

    # Build the full file
    file_data = bytearray(num_blocks * _BLOCK_SIZE)

    # Superblock (block 0)
    superblock = _MSF_MAGIC
    superblock += struct.pack("<IIIIII",
                              _BLOCK_SIZE,     # BlockSize
                              1,               # FreeBlockMapBlock
                              num_blocks,      # NumBlocks
                              len(dir_data),   # NumDirectoryBytes
                              0,               # Unknown
                              block_map_block) # BlockMapAddr
    file_data[:len(superblock)] = superblock

    # Write directory blocks
    dir_padded = _pad_block(dir_data, _BLOCK_SIZE)
    for i, blk in enumerate(dir_block_list):
        start = blk * _BLOCK_SIZE
        chunk = dir_padded[i * _BLOCK_SIZE:(i + 1) * _BLOCK_SIZE]
        file_data[start:start + len(chunk)] = chunk

    # Write block map (at block_map_block)
    bm_data = b""
    for blk in dir_block_list:
        bm_data += struct.pack("<I", blk)
    bm_offset = block_map_block * _BLOCK_SIZE
    file_data[bm_offset:bm_offset + len(bm_data)] = bm_data

    # Write stream data
    for i, s in enumerate(streams):
        for j, blk in enumerate(stream_blocks[i]):
            start = blk * _BLOCK_SIZE
            chunk_start = j * _BLOCK_SIZE
            chunk = s[chunk_start:chunk_start + _BLOCK_SIZE]
            file_data[start:start + len(chunk)] = chunk

    return bytes(file_data)


# ---------------------------------------------------------------------------
# Helper: build CodeView type records
# ---------------------------------------------------------------------------

def _cv_cstring(name: str) -> bytes:
    """Null-terminated string."""
    return name.encode("utf-8") + b"\x00"


def _cv_numeric(value: int) -> bytes:
    """Encode a CodeView numeric leaf."""
    if 0 <= value < 0x8000:
        return struct.pack("<H", value)
    if -128 <= value <= 127:
        return struct.pack("<Hb", LF_CHAR, value)
    if 0 <= value <= 0xFFFF:
        return struct.pack("<HH", LF_USHORT, value)
    if -32768 <= value <= 32767:
        return struct.pack("<Hh", LF_SHORT, value)
    if 0 <= value <= 0xFFFFFFFF:
        return struct.pack("<HI", LF_ULONG, value)
    return struct.pack("<Hi", LF_LONG, value)


def _make_lf_member(attr: int, type_ti: int, offset: int, name: str) -> bytes:
    """Build an LF_MEMBER sub-record (for inside LF_FIELDLIST)."""
    return struct.pack("<HHI", LF_MEMBER, attr, type_ti) + _cv_numeric(offset) + _cv_cstring(name)


def _make_lf_enumerate(attr: int, value: int, name: str) -> bytes:
    """Build an LF_ENUMERATE sub-record."""
    return struct.pack("<HH", LF_ENUMERATE, attr) + _cv_numeric(value) + _cv_cstring(name)


def _make_lf_fieldlist(sub_records: list[bytes]) -> bytes:
    """Build LF_FIELDLIST payload from sub-record bytes.

    Padding bytes follow the CodeView spec: 0xF0 + pad_length (so pad==1 -> 0xF1,
    pad==2 -> 0xF2, pad==3 -> 0xF3) repeated *pad* times for 4-byte alignment.
    """
    data = b""
    for rec in sub_records:
        data += rec
        # 4-byte align with spec-compliant LF_PADn bytes (0xF0 + n, repeated n times)
        pad = (4 - (len(data) % 4)) % 4
        data += bytes([0xF0 + pad] * pad) if pad else b""
    return data


def _make_lf_structure(
    count: int, prop: int, field_ti: int,
    byte_size: int, name: str,
) -> bytes:
    """Build LF_STRUCTURE / LF_CLASS payload."""
    return struct.pack("<HHIII", count, prop, field_ti, 0, 0) + _cv_numeric(byte_size) + _cv_cstring(name)


def _make_lf_union(count: int, prop: int, field_ti: int, byte_size: int, name: str) -> bytes:
    """Build LF_UNION payload."""
    return struct.pack("<HHI", count, prop, field_ti) + _cv_numeric(byte_size) + _cv_cstring(name)


def _make_lf_enum(count: int, prop: int, utype_ti: int, field_ti: int, name: str) -> bytes:
    """Build LF_ENUM payload."""
    return struct.pack("<HHII", count, prop, utype_ti, field_ti) + _cv_cstring(name)


def _make_lf_procedure(rvtype: int, calltype: int, parmcount: int, arglist: int) -> bytes:
    """Build LF_PROCEDURE payload."""
    return struct.pack("<IBBHI", rvtype, calltype, 0, parmcount, arglist)


def _make_lf_mfunction(
    rvtype: int, classtype: int, thistype: int,
    calltype: int, parmcount: int, arglist: int, thisadjust: int = 0,
) -> bytes:
    """Build LF_MFUNCTION payload."""
    return struct.pack("<IIIBBHIi", rvtype, classtype, thistype,
                       calltype, 0, parmcount, arglist, thisadjust)


def _make_lf_pointer(referent_ti: int, size: int = 8, mode: int = 0) -> bytes:
    """Build LF_POINTER payload."""
    # attrs: bits 13-18 = size, bits 5-7 = mode
    attrs = (size << 13) | (mode << 5) | 0x0C  # near64
    return struct.pack("<II", referent_ti, attrs)


def _make_lf_modifier(modified_ti: int, is_const: bool = False, is_volatile: bool = False) -> bytes:
    """Build LF_MODIFIER payload."""
    attr = 0
    if is_const:
        attr |= 0x01
    if is_volatile:
        attr |= 0x02
    return struct.pack("<IH", modified_ti, attr)


def _make_lf_bitfield(underlying_ti: int, length: int, position: int) -> bytes:
    """Build LF_BITFIELD payload."""
    return struct.pack("<IBB", underlying_ti, length, position)


def _make_lf_array(elem_ti: int, idx_ti: int, byte_size: int, name: str = "") -> bytes:
    """Build LF_ARRAY payload."""
    return struct.pack("<II", elem_ti, idx_ti) + _cv_numeric(byte_size) + _cv_cstring(name)


# ---------------------------------------------------------------------------
# Tests: numeric leaf decoding
# ---------------------------------------------------------------------------

class TestNumericLeaf:
    def test_inline_value(self) -> None:
        data = struct.pack("<H", 42)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == 42
        assert pos == 2

    def test_lf_char(self) -> None:
        data = struct.pack("<Hb", LF_CHAR, -5)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == -5
        assert pos == 3

    def test_lf_short(self) -> None:
        data = struct.pack("<Hh", LF_SHORT, -1000)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == -1000
        assert pos == 4

    def test_lf_ushort(self) -> None:
        data = struct.pack("<HH", LF_USHORT, 50000)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == 50000
        assert pos == 4

    def test_lf_long(self) -> None:
        data = struct.pack("<Hi", LF_LONG, -100000)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == -100000
        assert pos == 6

    def test_lf_ulong(self) -> None:
        data = struct.pack("<HI", LF_ULONG, 0xDEADBEEF)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == 0xDEADBEEF
        assert pos == 6

    def test_zero(self) -> None:
        data = struct.pack("<H", 0)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == 0
        assert pos == 2


class TestCString:
    def test_simple(self) -> None:
        data = b"hello\x00world"
        s, pos = _read_cstring(data, 0)
        assert s == "hello"
        assert pos == 6

    def test_at_offset(self) -> None:
        data = b"\x00\x00foo\x00"
        s, pos = _read_cstring(data, 2)
        assert s == "foo"
        assert pos == 6

    def test_empty(self) -> None:
        data = b"\x00rest"
        s, pos = _read_cstring(data, 0)
        assert s == ""
        assert pos == 1


# ---------------------------------------------------------------------------
# Tests: MSF parser
# ---------------------------------------------------------------------------

class TestMsfParser:
    def test_valid_pdb(self, tmp_path: Path) -> None:
        pdb_data = _build_minimal_pdb()
        pdb_file = tmp_path / "test.pdb"
        pdb_file.write_bytes(pdb_data)
        msf = parse_msf(pdb_data)
        assert msf.block_size == _BLOCK_SIZE
        assert msf.stream_count() == 4

    def test_bad_magic(self) -> None:
        with pytest.raises(ValueError, match="Not a PDB"):
            parse_msf(b"not a pdb file" + b"\x00" * 100)

    def test_too_small(self) -> None:
        with pytest.raises(ValueError, match="too small"):
            parse_msf(b"tiny")

    def test_stream_data_round_trip(self) -> None:
        """Verify stream data can be read back correctly."""
        dbi_data = _build_dbi_stream(machine=0x014C)
        pdb_data = _build_minimal_pdb(dbi_data=dbi_data)
        msf = parse_msf(pdb_data)
        # Stream 3 = DBI
        stream3 = msf.stream_data(3)
        assert len(stream3) == len(dbi_data)
        assert stream3 == dbi_data


# ---------------------------------------------------------------------------
# Tests: TPI stream parser
# ---------------------------------------------------------------------------

class TestTpiParser:
    def test_empty_tpi(self) -> None:
        tpi_data = _build_tpi_stream([])
        tpi = parse_tpi_stream(tpi_data)
        assert tpi.type_index_begin == 0x1000
        assert tpi.type_index_end == 0x1000
        assert len(tpi.records) == 0

    def test_single_structure(self) -> None:
        fieldlist_payload = _make_lf_fieldlist([
            _make_lf_member(0, 0x74, 0, "x"),   # int x at offset 0
            _make_lf_member(0, 0x74, 4, "y"),   # int y at offset 4
        ])
        struct_payload = _make_lf_structure(
            count=2, prop=0, field_ti=0x1000,
            byte_size=8, name="Point",
        )
        tpi_data = _build_tpi_stream([
            (LF_FIELDLIST, fieldlist_payload),   # ti=0x1000
            (LF_STRUCTURE, struct_payload),      # ti=0x1001
        ])
        tpi = parse_tpi_stream(tpi_data)
        assert len(tpi.records) == 2
        assert tpi.records[0].type_index == 0x1000
        assert tpi.records[0].leaf == LF_FIELDLIST
        assert tpi.records[1].type_index == 0x1001
        assert tpi.records[1].leaf == LF_STRUCTURE

    def test_too_small_tpi(self) -> None:
        with pytest.raises(ValueError, match="too small"):
            parse_tpi_stream(b"\x00" * 10)


# ---------------------------------------------------------------------------
# Tests: TypeDatabase
# ---------------------------------------------------------------------------

class TestTypeDatabase:
    def _make_db(self, records: list[tuple[int, bytes]]) -> TypeDatabase:
        tpi_data = _build_tpi_stream(records)
        tpi = parse_tpi_stream(tpi_data)
        db = TypeDatabase(tpi)
        db.parse_all()
        return db

    def test_simple_type_names(self) -> None:
        db = self._make_db([])
        assert db.type_name(0x74) == "int"       # int (kind=0x74)
        assert db.type_name(0x75) == "unsigned int"
        assert db.type_name(0x03) == "void"
        assert db.type_name(0x40) == "float"
        assert db.type_name(0x41) == "double"

    def test_simple_type_sizes(self) -> None:
        db = self._make_db([])
        assert db.type_size(0x74) == 4   # int
        assert db.type_size(0x41) == 8   # double
        assert db.type_size(0x10) == 1   # signed char
        assert db.type_size(0x03) == 0   # void

    def test_simple_pointer_type(self) -> None:
        db = self._make_db([])
        # Near64 pointer to int (kind=0x74, mode=0x06)
        ti = 0x0674
        assert "int" in db.type_name(ti)
        assert "*" in db.type_name(ti)
        assert db.type_size(ti) == 8

    def test_struct_resolution(self) -> None:
        fieldlist_payload = _make_lf_fieldlist([
            _make_lf_member(0, 0x74, 0, "x"),
            _make_lf_member(0, 0x74, 4, "y"),
        ])
        struct_payload = _make_lf_structure(
            count=2, prop=0, field_ti=0x1000,
            byte_size=8, name="Point",
        )
        db = self._make_db([
            (LF_FIELDLIST, fieldlist_payload),
            (LF_STRUCTURE, struct_payload),
        ])

        s = db.resolve_struct(0x1001)
        assert s is not None
        assert s.name == "Point"
        assert s.byte_size == 8
        assert s.count == 2
        assert not s.is_forward_ref
        assert not s.is_union

        fields = db.get_fieldlist(0x1000)
        assert len(fields) == 2
        assert fields[0].name == "x"
        assert fields[0].offset == 0
        assert fields[1].name == "y"
        assert fields[1].offset == 4

    def test_forward_ref_resolution(self) -> None:
        """Forward-ref struct should resolve to the definition."""
        fwd_payload = _make_lf_structure(
            count=0, prop=0x0080, field_ti=0,
            byte_size=0, name="Foo",
        )
        fieldlist_payload = _make_lf_fieldlist([
            _make_lf_member(0, 0x74, 0, "val"),
        ])
        def_payload = _make_lf_structure(
            count=1, prop=0, field_ti=0x1001,
            byte_size=4, name="Foo",
        )
        db = self._make_db([
            (LF_STRUCTURE, fwd_payload),       # ti=0x1000 (fwd ref)
            (LF_FIELDLIST, fieldlist_payload),  # ti=0x1001
            (LF_STRUCTURE, def_payload),        # ti=0x1002 (definition)
        ])

        s = db.resolve_struct(0x1000)
        assert s is not None
        assert s.name == "Foo"
        assert s.byte_size == 4

    def test_union(self) -> None:
        fieldlist = _make_lf_fieldlist([
            _make_lf_member(0, 0x74, 0, "i"),
            _make_lf_member(0, 0x40, 0, "f"),
        ])
        union_payload = _make_lf_union(
            count=2, prop=0, field_ti=0x1000,
            byte_size=4, name="Data",
        )
        db = self._make_db([
            (LF_FIELDLIST, fieldlist),
            (LF_UNION, union_payload),
        ])
        s = db.resolve_struct(0x1001)
        assert s is not None
        assert s.is_union
        assert s.name == "Data"
        assert s.byte_size == 4

    def test_enum(self) -> None:
        fieldlist = _make_lf_fieldlist([
            _make_lf_enumerate(0, 0, "RED"),
            _make_lf_enumerate(0, 1, "GREEN"),
            _make_lf_enumerate(0, 2, "BLUE"),
        ])
        enum_payload = _make_lf_enum(
            count=3, prop=0, utype_ti=0x74,
            field_ti=0x1000, name="Color",
        )
        db = self._make_db([
            (LF_FIELDLIST, fieldlist),
            (LF_ENUM, enum_payload),
        ])
        e = db.resolve_enum(0x1001)
        assert e is not None
        assert e.name == "Color"
        assert e.count == 3
        members = db.get_fieldlist(0x1000)
        assert len(members) == 3
        assert members[0].name == "RED" and members[0].value == 0
        assert members[2].name == "BLUE" and members[2].value == 2

    def test_procedure(self) -> None:
        proc_payload = _make_lf_procedure(0x74, 0x00, 2, 0x1001)
        db = self._make_db([
            (LF_PROCEDURE, proc_payload),
        ])
        p = db.get_procedure(0x1000)
        assert p is not None
        assert p.calling_convention == 0x00  # cdecl
        assert p.param_count == 2
        assert db.calling_convention_name(0x00) == "cdecl"

    def test_mfunction(self) -> None:
        mf_payload = _make_lf_mfunction(0x74, 0x1002, 0x1003, 0x0B, 1, 0x1004)
        db = self._make_db([
            (LF_MFUNCTION, mf_payload),
        ])
        mf = db.get_mfunction(0x1000)
        assert mf is not None
        assert mf.calling_convention == 0x0B  # thiscall
        assert db.calling_convention_name(0x0B) == "thiscall"

    def test_pointer_type_name(self) -> None:
        ptr_payload = _make_lf_pointer(0x74, size=8)
        db = self._make_db([
            (LF_POINTER, ptr_payload),
        ])
        assert "int" in db.type_name(0x1000)
        assert "*" in db.type_name(0x1000)

    def test_modifier_const(self) -> None:
        mod_payload = _make_lf_modifier(0x74, is_const=True)
        db = self._make_db([
            (LF_MODIFIER, mod_payload),
        ])
        name = db.type_name(0x1000)
        assert "const" in name
        assert "int" in name

    def test_bitfield(self) -> None:
        bf_payload = _make_lf_bitfield(0x74, length=3, position=5)
        db = self._make_db([
            (LF_BITFIELD, bf_payload),
        ])
        # Bitfield type name resolves to underlying type
        assert db.type_name(0x1000) == "int"
        assert db.type_size(0x1000) == 4

    def test_array(self) -> None:
        arr_payload = _make_lf_array(0x74, 0x74, 40, "")
        db = self._make_db([
            (LF_ARRAY, arr_payload),
        ])
        assert "int" in db.type_name(0x1000)
        assert "[]" in db.type_name(0x1000)
        assert db.type_size(0x1000) == 40

    def test_packed_struct(self) -> None:
        fieldlist = _make_lf_fieldlist([
            _make_lf_member(0, 0x74, 0, "a"),
        ])
        struct_payload = _make_lf_structure(
            count=1, prop=0x0800, field_ti=0x1000,  # packed flag
            byte_size=4, name="Packed",
        )
        db = self._make_db([
            (LF_FIELDLIST, fieldlist),
            (LF_STRUCTURE, struct_payload),
        ])
        s = db.resolve_struct(0x1001)
        assert s is not None
        assert s.is_packed

    def test_calling_convention_names(self) -> None:
        assert _CC_NAMES[0x00] == "cdecl"
        assert _CC_NAMES[0x04] == "fastcall"
        assert _CC_NAMES[0x07] == "stdcall"
        assert _CC_NAMES[0x0B] == "thiscall"
        assert _CC_NAMES[0x18] == "vectorcall"


# ---------------------------------------------------------------------------
# Tests: DBI stream parser
# ---------------------------------------------------------------------------

class TestDbiParser:
    def test_minimal_dbi(self) -> None:
        dbi_data = _build_dbi_stream(machine=0x8664, build_major=14, build_minor=36)
        dbi = parse_dbi_stream(dbi_data)
        assert dbi.header.machine == 0x8664
        # Build number: (14 << 8) | 36 | 0x8000
        assert (dbi.header.build_number >> 8) & 0x7F == 14
        assert dbi.header.build_number & 0xFF == 36

    def test_dbi_with_modules(self) -> None:
        modules = [
            ("foo.obj", "C:\\src\\foo.cpp"),
            ("bar.obj", "C:\\src\\bar.cpp"),
        ]
        dbi_data = _build_dbi_stream(modules=modules)
        dbi = parse_dbi_stream(dbi_data)
        assert len(dbi.modules) == 2
        assert dbi.modules[0].module_name == "foo.obj"
        assert dbi.modules[0].obj_file_name == "C:\\src\\foo.cpp"
        assert dbi.modules[1].module_name == "bar.obj"

    def test_dbi_x86(self) -> None:
        dbi_data = _build_dbi_stream(machine=0x014C)
        dbi = parse_dbi_stream(dbi_data)
        assert dbi.header.machine == 0x014C

    def test_too_small(self) -> None:
        with pytest.raises(ValueError, match="too small"):
            parse_dbi_stream(b"\x00" * 10)


# ---------------------------------------------------------------------------
# Tests: full PDB parse round-trip
# ---------------------------------------------------------------------------

class TestPdbRoundTrip:
    def test_full_parse(self, tmp_path: Path) -> None:
        """Build a PDB with struct + enum, parse it, verify types."""
        # Fieldlist for struct Point { int x; int y; }
        fl_struct = _make_lf_fieldlist([
            _make_lf_member(0, 0x74, 0, "x"),
            _make_lf_member(0, 0x74, 4, "y"),
        ])
        # Fieldlist for enum Color { RED=0, GREEN=1, BLUE=2 }
        fl_enum = _make_lf_fieldlist([
            _make_lf_enumerate(0, 0, "RED"),
            _make_lf_enumerate(0, 1, "GREEN"),
            _make_lf_enumerate(0, 2, "BLUE"),
        ])

        records = [
            (LF_FIELDLIST, fl_struct),                                    # 0x1000
            (LF_STRUCTURE, _make_lf_structure(2, 0, 0x1000, 8, "Point")), # 0x1001
            (LF_FIELDLIST, fl_enum),                                      # 0x1002
            (LF_ENUM, _make_lf_enum(3, 0, 0x74, 0x1002, "Color")),       # 0x1003
            (LF_PROCEDURE, _make_lf_procedure(0x74, 0x07, 1, 0)),        # 0x1004 stdcall
        ]

        pdb_bytes = _build_minimal_pdb(tpi_records=records)
        pdb_file = tmp_path / "test.pdb"
        pdb_file.write_bytes(pdb_bytes)

        from abicheck.pdb_parser import parse_pdb
        pdb = parse_pdb(pdb_file)

        assert pdb.tpi is not None
        assert pdb.types is not None
        assert pdb.dbi is not None

        # Struct
        s = pdb.types.resolve_struct(0x1001)
        assert s is not None
        assert s.name == "Point"
        assert s.byte_size == 8

        # Enum
        e = pdb.types.resolve_enum(0x1003)
        assert e is not None
        assert e.name == "Color"

        # Procedure
        p = pdb.types.get_procedure(0x1004)
        assert p is not None
        assert p.calling_convention == 0x07  # stdcall


# ---------------------------------------------------------------------------
# Tests: additional coverage for edge cases
# ---------------------------------------------------------------------------

class TestNumericLeafExtended:
    def test_truncated_data(self) -> None:
        """Offset past end of data returns (0, offset+2)."""
        val, pos = _read_numeric_leaf(b"", 0)
        assert val == 0
        assert pos == 2

    def test_lf_quadword(self) -> None:
        data = struct.pack("<Hq", LF_QUADWORD, -(2**60))
        val, pos = _read_numeric_leaf(data, 0)
        assert val == -(2**60)
        assert pos == 10

    def test_lf_uquadword(self) -> None:
        data = struct.pack("<HQ", LF_UQUADWORD, 2**63)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == 2**63
        assert pos == 10

    def test_unknown_leaf(self) -> None:
        """Unknown leaf tag should return (0, offset+6)."""
        data = struct.pack("<HI", 0x800F, 0xDEADBEEF)
        val, pos = _read_numeric_leaf(data, 0)
        assert val == 0
        assert pos == 6


class TestCStringExtended:
    def test_no_null_terminator(self) -> None:
        """String without null terminator returns empty string."""
        data = b"abc"
        s, pos = _read_cstring(data, 0)
        assert s == ""
        assert pos == len(data)


class TestMsfParserExtended:
    def test_stream_data_negative_index(self) -> None:
        pdb_data = _build_minimal_pdb()
        msf = parse_msf(pdb_data)
        assert msf.stream_data(-1) == b""

    def test_stream_data_out_of_range(self) -> None:
        pdb_data = _build_minimal_pdb()
        msf = parse_msf(pdb_data)
        assert msf.stream_data(999) == b""

    def test_invalid_block_size(self) -> None:
        """Invalid block size in header should raise ValueError."""
        pdb_data = bytearray(_build_minimal_pdb())
        # Patch block_size field (at offset 32) to an unsupported value
        struct.pack_into("<I", pdb_data, 32, 8192)
        with pytest.raises(ValueError, match="Unsupported PDB block size"):
            parse_msf(bytes(pdb_data))


class TestTpiParserExtended:
    def test_record_with_small_rec_len(self) -> None:
        """Record with rec_len < 2 should stop parsing."""
        # Build TPI header for 1 record
        ti_begin = 0x1000
        ti_end = 0x1001
        # Create a record with rec_len=1 (< 2 threshold)
        rec_data = struct.pack("<H", 1)  # rec_len = 1
        rec_data += b"\x00" * 2  # padding
        header = struct.pack("<IIIII", 20040203, 56, ti_begin, ti_end, len(rec_data))
        header += b"\x00" * (56 - len(header))
        tpi = parse_tpi_stream(header + rec_data)
        assert len(tpi.records) == 0

    def test_tpi_get_method(self) -> None:
        """TpiStream.get() returns records by type index."""
        fieldlist_payload = _make_lf_fieldlist([
            _make_lf_member(0, 0x74, 0, "x"),
        ])
        tpi_data = _build_tpi_stream([(LF_FIELDLIST, fieldlist_payload)])
        tpi = parse_tpi_stream(tpi_data)
        rec = tpi.get(0x1000)
        assert rec is not None
        assert rec.leaf == LF_FIELDLIST
        assert tpi.get(0xFFFF) is None


class TestTypeDatabaseExtended:
    def _make_db(self, records: list[tuple[int, bytes]]) -> TypeDatabase:
        tpi_data = _build_tpi_stream(records)
        tpi = parse_tpi_stream(tpi_data)
        db = TypeDatabase(tpi)
        db.parse_all()
        return db

    def test_all_structs(self) -> None:
        struct_payload = _make_lf_structure(0, 0, 0, 4, "Foo")
        db = self._make_db([(LF_STRUCTURE, struct_payload)])
        assert len(db.all_structs()) == 1

    def test_all_enums(self) -> None:
        fl = _make_lf_fieldlist([_make_lf_enumerate(0, 0, "A")])
        enum_payload = _make_lf_enum(1, 0, 0x74, 0x1000, "E")
        db = self._make_db([(LF_FIELDLIST, fl), (LF_ENUM, enum_payload)])
        assert len(db.all_enums()) == 1

    def test_all_procedures(self) -> None:
        db = self._make_db([(LF_PROCEDURE, _make_lf_procedure(0x74, 0, 0, 0))])
        assert len(db.all_procedures()) == 1

    def test_all_mfunctions(self) -> None:
        db = self._make_db([(LF_MFUNCTION, _make_lf_mfunction(0x74, 0, 0, 0, 0, 0))])
        assert len(db.all_mfunctions()) == 1

    def test_modifier_volatile(self) -> None:
        mod_payload = _make_lf_modifier(0x74, is_volatile=True)
        db = self._make_db([(LF_MODIFIER, mod_payload)])
        name = db.type_name(0x1000)
        assert "volatile" in name
        assert "int" in name

    def test_type_name_depth_limit(self) -> None:
        db = self._make_db([])
        assert db.type_name(0x74, depth=11) == "..."

    def test_type_size_depth_limit(self) -> None:
        db = self._make_db([])
        assert db.type_size(0x74, depth=11) == 0

    def test_type_name_unknown_ti(self) -> None:
        db = self._make_db([])
        name = db.type_name(0x9999)
        assert "<ti:0x9999>" == name

    def test_type_size_enum(self) -> None:
        fl = _make_lf_fieldlist([_make_lf_enumerate(0, 0, "X")])
        enum_payload = _make_lf_enum(1, 0, 0x74, 0x1000, "MyEnum")
        db = self._make_db([(LF_FIELDLIST, fl), (LF_ENUM, enum_payload)])
        # Enum size should resolve to underlying type (int = 4)
        assert db.type_size(0x1001) == 4

    def test_type_name_enum(self) -> None:
        fl = _make_lf_fieldlist([_make_lf_enumerate(0, 0, "X")])
        enum_payload = _make_lf_enum(1, 0, 0x74, 0x1000, "MyEnum")
        db = self._make_db([(LF_FIELDLIST, fl), (LF_ENUM, enum_payload)])
        assert db.type_name(0x1001) == "enum MyEnum"

    def test_type_name_procedure(self) -> None:
        db = self._make_db([(LF_PROCEDURE, _make_lf_procedure(0x74, 0, 0, 0))])
        assert db.type_name(0x1000) == "fn(...)"

    def test_type_name_mfunction(self) -> None:
        db = self._make_db([(LF_MFUNCTION, _make_lf_mfunction(0x74, 0, 0, 0, 0, 0))])
        assert db.type_name(0x1000) == "fn(...)"

    def test_simple_type_near32_pointer(self) -> None:
        """Near32 pointer mode (0x02) should resolve to pointer."""
        db = self._make_db([])
        ti = 0x0274  # mode=0x02 (near32), kind=0x74 (int)
        name = db.type_name(ti)
        assert "*" in name
        assert db.type_size(ti) == 4

    def test_simple_type_other_pointer_mode(self) -> None:
        """Unknown pointer mode should still produce pointer name."""
        db = self._make_db([])
        ti = 0x0374  # mode=0x03, kind=0x74
        name = db.type_name(ti)
        assert "*" in name
        assert db.type_size(ti) == 8  # default ptr size

    def test_pointer_lvalue_reference(self) -> None:
        """LValueReference pointer mode (1)."""
        # attrs: mode=1 at bits 5-7 → (1 << 5) = 0x20, plus near64 marker
        attrs = (8 << 13) | (1 << 5) | 0x0C
        payload = struct.pack("<II", 0x74, attrs)
        db = self._make_db([(LF_POINTER, payload)])
        name = db.type_name(0x1000)
        assert "&" in name
        assert "&&" not in name

    def test_pointer_rvalue_reference(self) -> None:
        """RValueReference pointer mode (4)."""
        attrs = (8 << 13) | (4 << 5) | 0x0C
        payload = struct.pack("<II", 0x74, attrs)
        db = self._make_db([(LF_POINTER, payload)])
        name = db.type_name(0x1000)
        assert "&&" in name

    def test_calling_convention_unknown(self) -> None:
        db = self._make_db([])
        assert db.calling_convention_name(0xFF) == "cc_0xff"

    def test_parse_all_idempotent(self) -> None:
        """Calling parse_all() twice should not break anything."""
        db = self._make_db([(LF_STRUCTURE, _make_lf_structure(0, 0, 0, 4, "S"))])
        db.parse_all()  # second call, should be no-op
        assert len(db.all_structs()) == 1

    def test_truncated_struct_data(self) -> None:
        """Struct with truncated data should be skipped."""
        db = self._make_db([(LF_STRUCTURE, b"\x00" * 4)])  # < 16 bytes
        assert len(db.all_structs()) == 0

    def test_truncated_union_data(self) -> None:
        db = self._make_db([(LF_UNION, b"\x00" * 4)])  # < 8 bytes
        assert len(db.all_structs()) == 0

    def test_truncated_enum_data(self) -> None:
        db = self._make_db([(LF_ENUM, b"\x00" * 4)])  # < 12 bytes
        assert len(db.all_enums()) == 0

    def test_truncated_procedure_data(self) -> None:
        db = self._make_db([(LF_PROCEDURE, b"\x00" * 4)])  # < 12 bytes
        assert len(db.all_procedures()) == 0

    def test_truncated_mfunction_data(self) -> None:
        db = self._make_db([(LF_MFUNCTION, b"\x00" * 4)])  # < 24 bytes
        assert len(db.all_mfunctions()) == 0

    def test_truncated_pointer_data(self) -> None:
        db = self._make_db([(LF_POINTER, b"\x00" * 4)])  # < 8 bytes
        # Should be silently skipped
        assert db.type_name(0x1000) == "<ti:0x1000>"

    def test_truncated_modifier_data(self) -> None:
        db = self._make_db([(LF_MODIFIER, b"\x00" * 2)])  # < 6 bytes
        assert db.type_name(0x1000) == "<ti:0x1000>"

    def test_truncated_bitfield_data(self) -> None:
        db = self._make_db([(LF_BITFIELD, b"\x00" * 2)])  # < 6 bytes
        assert db.type_name(0x1000) == "<ti:0x1000>"

    def test_truncated_array_data(self) -> None:
        db = self._make_db([(LF_ARRAY, b"\x00" * 4)])  # < 8 bytes
        assert db.type_name(0x1000) == "<ti:0x1000>"

    def test_truncated_arglist_data(self) -> None:
        db = self._make_db([(LF_ARGLIST, b"\x00" * 2)])  # < 4 bytes
        assert db.type_name(0x1000) == "<ti:0x1000>"

    def test_arglist_truncated_entries(self) -> None:
        """Arglist with count > available data should parse partial."""
        # count=5 but only room for 2
        payload = struct.pack("<III", 5, 0x74, 0x74)
        db = self._make_db([(LF_ARGLIST, payload)])
        args = db._arglists.get(0x1000, [])
        assert len(args) == 2


class TestFieldlistExtended:
    def _make_db(self, records: list[tuple[int, bytes]]) -> TypeDatabase:
        tpi_data = _build_tpi_stream(records)
        tpi = parse_tpi_stream(tpi_data)
        db = TypeDatabase(tpi)
        db.parse_all()
        return db

    def test_lf_index_continuation(self) -> None:
        """LF_INDEX should follow continuation to another fieldlist."""
        # First fieldlist: member "a", then LF_INDEX pointing to 0x1001
        fl1_data = _make_lf_member(0, 0x74, 0, "a")
        # Pad to 4-byte boundary
        pad = (4 - (len(fl1_data) % 4)) % 4
        fl1_data += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        # Add LF_INDEX sub-record: leaf(2) + pad(2) + cont_ti(4)
        fl1_data += struct.pack("<HHI", LF_INDEX, 0, 0x1001)

        # Second fieldlist: member "b"
        fl2_data = _make_lf_fieldlist([_make_lf_member(0, 0x74, 4, "b")])

        db = self._make_db([
            (LF_FIELDLIST, fl1_data),      # 0x1000
            (LF_FIELDLIST, fl2_data),       # 0x1001
        ])
        members = db.get_fieldlist(0x1000)
        names = [m.name for m in members]
        assert "a" in names
        assert "b" in names

    def test_circular_lf_index(self) -> None:
        """Circular LF_INDEX reference should be detected and skipped."""
        # Fieldlist with LF_INDEX pointing to itself (0x1000)
        fl_data = struct.pack("<HHI", LF_INDEX, 0, 0x1000)
        db = self._make_db([(LF_FIELDLIST, fl_data)])
        # Should not raise (infinite recursion guarded)
        members = db.get_fieldlist(0x1000)
        assert isinstance(members, list)

    def test_padding_bytes(self) -> None:
        """Padding bytes (>= 0xF0) should be skipped correctly."""
        fl = _make_lf_fieldlist([
            _make_lf_member(0, 0x74, 0, "a"),
            _make_lf_member(0, 0x74, 4, "b"),
        ])
        db = self._make_db([(LF_FIELDLIST, fl)])
        members = db.get_fieldlist(0x1000)
        assert len(members) == 2

    def test_unknown_sub_leaf(self) -> None:
        """Unknown sub-leaf should stop parsing (break)."""
        # Unknown leaf 0x0001 followed by valid member
        data = struct.pack("<H", 0x0001)  # unknown sub-leaf
        data += _make_lf_member(0, 0x74, 0, "x")
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert len(members) == 0  # stopped at unknown

    def test_truncated_lf_member(self) -> None:
        """LF_MEMBER with truncated data should break out."""
        data = struct.pack("<H", LF_MEMBER) + b"\x00" * 2  # only 2 extra bytes, need 6
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert len(members) == 0

    def test_truncated_lf_enumerate(self) -> None:
        """LF_ENUMERATE with truncated data should break out."""
        data = struct.pack("<H", LF_ENUMERATE)  # no attr bytes
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert len(members) == 0

    def test_truncated_lf_index(self) -> None:
        """LF_INDEX with truncated data should break out."""
        data = struct.pack("<H", LF_INDEX) + b"\x00" * 2  # need 4+ extra bytes
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert len(members) == 0

    def test_truncated_lf_index_4_bytes(self) -> None:
        """LF_INDEX with only 4 extra bytes (need 6) should break out."""
        # LF_INDEX sub-record needs 2-byte sub_leaf + 2-byte pad + 4-byte TI = 6 bytes.
        # Here we have 2 (sub_leaf) + 4 extra = 6 total, but sub_leaf already consumed 2,
        # so pos + 6 > len(d) check: pos=2, remaining=4 bytes → 2+6=8 > 6 → break.
        data = struct.pack("<H", LF_INDEX) + b"\x00" * 4  # exactly 4 extra bytes (need 6)
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert len(members) == 0

    def test_truncated_lf_index_5_bytes(self) -> None:
        """LF_INDEX with 5 extra bytes (need 6) should break out."""
        data = struct.pack("<H", LF_INDEX) + b"\x00" * 5  # 5 extra bytes (need 6)
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert len(members) == 0

    def test_padding_lf_pad1_between_members(self) -> None:
        """Single-byte LF_PAD1 (0xF1) between members should be skipped correctly.

        _make_lf_member(0, 0x74, 0, "") produces 11 bytes:
          2 (LF_MEMBER) + 2 (attr) + 4 (type_ti) + 2 (_cv_numeric(0)) + 1 (empty cstring)
        11 % 4 == 3 → needs exactly 1 pad byte (LF_PAD1 = 0xF1) to align to 12.
        """
        # Empty name gives 11-byte record → 1-byte pad needed
        first = _make_lf_member(0, 0x74, 0, "")
        assert len(first) % 4 == 3, f"Expected 3 mod 4, got {len(first) % 4}"
        # Insert explicit LF_PAD1 byte (0xF1 = 0xF0 + 1)
        padded = first + bytes([0xF1])
        assert len(padded) % 4 == 0
        second = _make_lf_member(0, 0x74, 4, "b")
        fl_data = padded + second
        db = self._make_db([(LF_FIELDLIST, fl_data)])
        members = db.get_fieldlist(0x1000)
        assert len(members) == 2
        assert members[1].name == "b"


class TestSkipSubrecord:
    def _make_db(self, records: list[tuple[int, bytes]]) -> TypeDatabase:
        tpi_data = _build_tpi_stream(records)
        tpi = parse_tpi_stream(tpi_data)
        db = TypeDatabase(tpi)
        db.parse_all()
        return db

    def test_lf_stmember(self) -> None:
        """LF_STMEMBER sub-record should be skipped, allowing next member."""
        # Build fieldlist with LF_STMEMBER then LF_MEMBER
        stmember = struct.pack("<HHI", LF_STMEMBER, 0, 0x74) + _cv_cstring("sval")
        pad = (4 - (len(stmember) % 4)) % 4
        stmember += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        member = _make_lf_member(0, 0x74, 0, "x")
        data = stmember + member
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert any(m.name == "x" for m in members)

    def test_lf_nesttype(self) -> None:
        """LF_NESTTYPE should be skipped."""
        nesttype = struct.pack("<HHI", LF_NESTTYPE, 0, 0x1001) + _cv_cstring("Inner")
        pad = (4 - (len(nesttype) % 4)) % 4
        nesttype += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        member = _make_lf_member(0, 0x74, 0, "val")
        data = nesttype + member
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert any(m.name == "val" for m in members)

    def test_lf_onemethod_nonvirtual(self) -> None:
        """LF_ONEMETHOD (non-virtual) should be skipped."""
        # attr with mprop=0 (not virtual), so no vbaseoff
        onemethod = struct.pack("<HHI", LF_ONEMETHOD, 0, 0x1001) + _cv_cstring("foo")
        pad = (4 - (len(onemethod) % 4)) % 4
        onemethod += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        member = _make_lf_member(0, 0x74, 0, "val")
        data = onemethod + member
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert any(m.name == "val" for m in members)

    def test_lf_onemethod_virtual(self) -> None:
        """LF_ONEMETHOD (intro virtual, mprop=4) has extra vbaseoff field."""
        # mprop=4 → attr = 4 << 2 = 16
        attr = 4 << 2
        onemethod = struct.pack("<HHI", LF_ONEMETHOD, attr, 0x1001)
        onemethod += struct.pack("<I", 0)  # vbaseoff
        onemethod += _cv_cstring("vfunc")
        pad = (4 - (len(onemethod) % 4)) % 4
        onemethod += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        member = _make_lf_member(0, 0x74, 0, "data")
        data = onemethod + member
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert any(m.name == "data" for m in members)

    def test_lf_method(self) -> None:
        """LF_METHOD should be skipped."""
        method = struct.pack("<HHI", LF_METHOD, 1, 0x1001) + _cv_cstring("bar")
        pad = (4 - (len(method) % 4)) % 4
        method += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        member = _make_lf_member(0, 0x74, 0, "v")
        data = method + member
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert any(m.name == "v" for m in members)

    def test_lf_vfunctab(self) -> None:
        """LF_VFUNCTAB should skip 6 bytes."""
        vfunctab = struct.pack("<HHI", LF_VFUNCTAB, 0, 0x1001)
        pad = (4 - (len(vfunctab) % 4)) % 4
        vfunctab += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        member = _make_lf_member(0, 0x74, 0, "w")
        data = vfunctab + member
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert any(m.name == "w" for m in members)

    def test_lf_bclass(self) -> None:
        """LF_BCLASS should skip attr(2) + type_ti(4) + numeric leaf."""
        bclass = struct.pack("<HHI", LF_BCLASS, 0, 0x1001) + _cv_numeric(0)
        pad = (4 - (len(bclass) % 4)) % 4
        bclass += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        member = _make_lf_member(0, 0x74, 0, "bval")
        data = bclass + member
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert any(m.name == "bval" for m in members)

    def test_lf_vbclass(self) -> None:
        """LF_VBCLASS should skip attr(2)+direct(4)+vbptr(4)+2 numeric leaves."""
        vbclass = struct.pack("<HHII", LF_VBCLASS, 0, 0x1001, 0x1002)
        vbclass += _cv_numeric(0)  # vbpoff
        vbclass += _cv_numeric(0)  # vbtableoff
        pad = (4 - (len(vbclass) % 4)) % 4
        vbclass += bytes([0xF0 + n for n in range(pad, 0, -1)]) if pad else b""
        member = _make_lf_member(0, 0x74, 0, "vb")
        data = vbclass + member
        db = self._make_db([(LF_FIELDLIST, data)])
        members = db.get_fieldlist(0x1000)
        assert any(m.name == "vb" for m in members)

    def test_subrecord_truncated_data(self) -> None:
        """Truncated sub-record data should return len(d)."""
        # LF_STMEMBER with only 2 bytes after leaf — needs 6
        data = struct.pack("<H", LF_STMEMBER) + b"\x00" * 2
        db = self._make_db([(LF_FIELDLIST, data)])
        # Should not crash, just stop parsing
        members = db.get_fieldlist(0x1000)
        assert members == []


class TestDbiParserExtended:
    def test_dbi_flags(self) -> None:
        dbi_data = _build_dbi_stream(flags=0x01)
        dbi = parse_dbi_stream(dbi_data)
        assert dbi.header.flags == 0x01
