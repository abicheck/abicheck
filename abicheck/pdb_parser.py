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

"""Minimal PDB (Program Database) parser for Windows debug info.

Pure-Python implementation using only the ``struct`` module — no GPL/AGPL
dependencies.  Parses the MSF container format and exposes the TPI (type
information) and DBI (debug information) streams needed for ABI checking.

Reference specifications:
- LLVM PDB documentation: https://llvm.org/docs/PDB/
- Microsoft PDB: https://github.com/microsoft/microsoft-pdb
- CodeView type records: microsoft-pdb/include/cvinfo.h (MIT licensed)

Only the subset of CodeView records relevant to ABI checking is implemented:
LF_STRUCTURE, LF_CLASS, LF_UNION, LF_ENUM, LF_FIELDLIST, LF_MEMBER,
LF_ENUMERATE, LF_PROCEDURE, LF_MFUNCTION, LF_MODIFIER, LF_POINTER,
LF_ARRAY, LF_BITFIELD, LF_INDEX.
"""
from __future__ import annotations

import logging
import math
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ValidationError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MSF_MAGIC = b"Microsoft C/C++ MSF 7.00\r\n\x1a\x44\x53\x00\x00\x00"
_MSF_MAGIC_LEN = 32

# Well-known stream indices
_PDB_STREAM = 1
_TPI_STREAM = 2
_DBI_STREAM = 3
_IPI_STREAM = 4

# TPI header version
_TPI_VERSION_V80 = 20040203

# Type index base (indices below this are "simple" / built-in types)
_TI_BASE = 0x1000

# CodeView leaf type constants (from cvinfo.h — MIT licensed)
LF_MODIFIER = 0x1001
LF_POINTER = 0x1002
LF_PROCEDURE = 0x1008
LF_MFUNCTION = 0x1009
LF_ARGLIST = 0x1201
LF_FIELDLIST = 0x1203
LF_BITFIELD = 0x1205
LF_INDEX = 0x1602
LF_ENUMERATE = 0x1502
LF_ARRAY = 0x1503
LF_CLASS = 0x1504
LF_STRUCTURE = 0x1505
LF_UNION = 0x1506
LF_ENUM = 0x1507
LF_MEMBER = 0x150D
LF_STMEMBER = 0x150E
LF_NESTTYPE = 0x1510
LF_ONEMETHOD = 0x1511
LF_VFUNCTAB = 0x1409
LF_BCLASS = 0x1400
LF_VBCLASS = 0x1401
LF_IVBCLASS = 0x1402
LF_METHOD = 0x150F

# IPI (stream 4) "id" leaf records — carry source-file provenance for UDTs.
LF_STRING_ID = 0x1605        # { substr_list_id: u32, name: char[] }
LF_UDT_SRC_LINE = 0x1606     # { udt_ti: u32, src_string_id: u32, line: u32 }
LF_UDT_MOD_SRC_LINE = 0x1607  # { udt_ti: u32, src_string_id: u32, line: u32, mod: u16 }

# Numeric leaf constants
LF_NUMERIC = 0x8000
LF_CHAR = 0x8000
LF_SHORT = 0x8001
LF_USHORT = 0x8002
LF_LONG = 0x8003
LF_ULONG = 0x8004
LF_QUADWORD = 0x8009
LF_UQUADWORD = 0x800A

# CV_call_e — calling convention values (from cvconst.h)
CV_CALL_NEAR_C = 0x00
CV_CALL_NEAR_PASCAL = 0x02
CV_CALL_NEAR_FAST = 0x04
CV_CALL_NEAR_STD = 0x07
CV_CALL_THISCALL = 0x0B
CV_CALL_CLRCALL = 0x16
CV_CALL_INLINE = 0x17
CV_CALL_NEAR_VECTOR = 0x18

_CC_NAMES: dict[int, str] = {
    0x00: "cdecl",
    0x01: "far_cdecl",
    0x02: "pascal",
    0x03: "far_pascal",
    0x04: "fastcall",
    0x05: "far_fastcall",
    0x07: "stdcall",
    0x08: "far_stdcall",
    0x09: "syscall",
    0x0A: "far_syscall",
    0x0B: "thiscall",
    0x0D: "generic",
    0x11: "armcall",
    0x16: "clrcall",
    0x17: "inline",
    0x18: "vectorcall",
}

# CV_prop_t flags
_PROP_FORWARD_REF = 0x0080
_PROP_PACKED = 0x0800

# Simple type kind (lower 8 bits of type index < 0x1000)
_SIMPLE_TYPE_NAMES: dict[int, str] = {
    0x00: "void",
    0x03: "void",
    0x10: "signed char",
    0x11: "short",
    0x12: "long",
    0x13: "long long",
    0x20: "unsigned char",
    0x21: "unsigned short",
    0x22: "unsigned long",
    0x23: "unsigned long long",
    0x30: "bool",
    0x40: "float",
    0x41: "double",
    0x42: "long double",
    0x68: "char",
    0x69: "wchar_t",
    0x70: "int",
    0x71: "unsigned int",
    0x72: "char16_t",
    0x73: "char32_t",
    0x74: "int",        # 32-bit signed int
    0x75: "unsigned int",  # 32-bit unsigned int
    0x76: "long long",  # 64-bit signed
    0x77: "unsigned long long",  # 64-bit unsigned
}

# Simple type sizes in bytes (by kind, lower 8 bits)
_SIMPLE_TYPE_SIZES: dict[int, int] = {
    0x00: 0, 0x03: 0,
    0x10: 1, 0x20: 1, 0x68: 1,
    0x11: 2, 0x21: 2, 0x72: 2,
    0x12: 4, 0x22: 4, 0x70: 4, 0x71: 4, 0x74: 4, 0x75: 4,
    0x13: 8, 0x23: 8, 0x76: 8, 0x77: 8,
    0x30: 1, 0x69: 2, 0x73: 4,
    0x40: 4, 0x41: 8, 0x42: 16,
}


# ---------------------------------------------------------------------------
# MSF (Multi-Stream File) container parser
# ---------------------------------------------------------------------------

@dataclass
class MsfFile:
    """Parsed MSF container — provides access to individual streams."""
    block_size: int
    num_blocks: int
    stream_sizes: list[int]
    stream_blocks: list[list[int]]
    _data: bytes = field(repr=False)

    def stream_count(self) -> int:
        return len(self.stream_sizes)

    def stream_data(self, index: int) -> bytes:
        """Read and concatenate all blocks for stream *index*."""
        if index < 0 or index >= len(self.stream_sizes):
            return b""
        size = self.stream_sizes[index]
        if size <= 0:
            return b""
        blocks = self.stream_blocks[index]
        parts: list[bytes] = []
        remaining = size
        for blk in blocks:
            offset = blk * self.block_size
            chunk = min(remaining, self.block_size)
            parts.append(self._data[offset:offset + chunk])
            remaining -= chunk
            if remaining <= 0:
                break
        return b"".join(parts)[:size]


def parse_msf(data: bytes) -> MsfFile:
    """Parse the MSF 7.0 container from raw file bytes.

    Raises ``ValueError`` on invalid format.
    """
    if len(data) < _MSF_MAGIC_LEN + 24:
        raise ValidationError("File too small to be a PDB")
    if data[:_MSF_MAGIC_LEN] != _MSF_MAGIC:
        raise ValidationError("Not a PDB 7.0 file (bad magic)")

    (block_size, _fpm_block, num_blocks, dir_bytes, _unknown,
     block_map_addr) = struct.unpack_from("<IIIIII", data, _MSF_MAGIC_LEN)

    if block_size not in (512, 1024, 2048, 4096):
        raise ValidationError(f"Unsupported PDB block size: {block_size}")

    # Number of blocks the stream directory occupies
    dir_block_count = math.ceil(dir_bytes / block_size)

    # The block at block_map_addr contains the block indices of the directory
    bm_offset = block_map_addr * block_size
    dir_block_indices: list[int] = []
    for i in range(dir_block_count):
        if bm_offset + i * 4 + 4 > len(data):
            raise ValidationError("PDB block map address out of bounds")
        idx = struct.unpack_from("<I", data, bm_offset + i * 4)[0]
        dir_block_indices.append(idx)

    # Assemble the stream directory (use list+join for O(n) rather than O(n²))
    dir_parts: list[bytes] = []
    remaining = dir_bytes
    for blk in dir_block_indices:
        off = blk * block_size
        chunk = min(remaining, block_size)
        if off + chunk > len(data):
            raise ValidationError(f"PDB block {blk} out of bounds (file too small)")
        dir_parts.append(data[off:off + chunk])
        remaining -= chunk
    dir_data = b"".join(dir_parts)

    # Parse the stream directory
    pos = 0
    if pos + 4 > len(dir_data):
        raise ValidationError("PDB stream directory truncated (no num_streams)")
    (num_streams,) = struct.unpack_from("<I", dir_data, pos)
    pos += 4

    stream_sizes: list[int] = []
    for _ in range(num_streams):
        if pos + 4 > len(dir_data):
            raise ValidationError("PDB stream directory truncated (stream sizes)")
        (sz,) = struct.unpack_from("<i", dir_data, pos)
        pos += 4
        # -1 or 0xFFFFFFFF means "nil stream"
        stream_sizes.append(max(sz, 0))

    stream_blocks: list[list[int]] = []
    for sz in stream_sizes:
        if sz <= 0:
            stream_blocks.append([])
            continue
        n_blocks = math.ceil(sz / block_size)
        blocks = []
        for _ in range(n_blocks):
            if pos + 4 > len(dir_data):
                raise ValidationError("PDB stream directory truncated (block indices)")
            (blk,) = struct.unpack_from("<I", dir_data, pos)
            pos += 4
            blocks.append(blk)
        stream_blocks.append(blocks)

    return MsfFile(
        block_size=block_size,
        num_blocks=num_blocks,
        stream_sizes=stream_sizes,
        stream_blocks=stream_blocks,
        _data=data,
    )


# ---------------------------------------------------------------------------
# Numeric leaf decoding
# ---------------------------------------------------------------------------

def _read_numeric_leaf(
    data: bytes, offset: int, *, unsupported: list[bool] | None = None
) -> tuple[int, int]:
    """Read a CodeView numeric leaf at *offset*.

    Returns ``(value, new_offset)`` where *new_offset* points past the leaf.
    If the 16-bit value at *offset* is < 0x8000 it is the value itself.
    Otherwise it is a leaf type tag followed by the actual value.

    ``unsupported``, when given a list, has ``True`` appended whenever the
    trailing "unknown leaf type" fallback below fires (P2 review, fresh
    evidence, Codex): that branch silently substitutes 0 for the leaf's
    real value, distinguishable from a legitimately-zero-valued leaf only
    through this signal -- every caller that only reads back the returned
    ``value`` cannot tell the two apart, so a discarded size/offset/enum
    value previously left the enclosing record (and the receipt built from
    it) reading as a complete, successful parse.
    """
    if offset + 2 > len(data):
        return (0, offset + 2)
    (val,) = struct.unpack_from("<H", data, offset)
    if val < LF_NUMERIC:
        return (val, offset + 2)
    if val == LF_CHAR:
        (v,) = struct.unpack_from("<b", data, offset + 2)
        return (v, offset + 3)
    if val == LF_SHORT:
        (v,) = struct.unpack_from("<h", data, offset + 2)
        return (v, offset + 4)
    if val == LF_USHORT:
        (v,) = struct.unpack_from("<H", data, offset + 2)
        return (v, offset + 4)
    if val == LF_LONG:
        (v,) = struct.unpack_from("<i", data, offset + 2)
        return (v, offset + 6)
    if val == LF_ULONG:
        (v,) = struct.unpack_from("<I", data, offset + 2)
        return (v, offset + 6)
    if val == LF_QUADWORD:
        (v,) = struct.unpack_from("<q", data, offset + 2)
        return (v, offset + 10)
    if val == LF_UQUADWORD:
        (v,) = struct.unpack_from("<Q", data, offset + 2)
        return (v, offset + 10)
    # Unknown numeric leaf — best-effort skip of 6 bytes (2-byte tag + 4-byte
    # value), which is correct for most CodeView numeric encodings.  May be
    # wrong for exotic leaf types; if this fires frequently, consider adding
    # explicit support for the leaf type.
    # Note: skip length is not validated; unknown leaves may cause mis-alignment.
    log.debug("Unknown numeric leaf 0x%04x at offset %d", val, offset)
    if unsupported is not None:
        unsupported.append(True)
    return (0, offset + 6)


def _read_cstring(data: bytes, offset: int) -> tuple[str, int, bool]:
    """Read a null-terminated string at *offset*.

    Returns ``(string, new_offset, terminated)`` -- ``new_offset`` is past the
    null terminator on success. ``terminated`` is False (P2 review) when no
    NUL byte was found before the end of *data*: the caller previously
    couldn't distinguish this from a legitimate empty string (both returned
    ``("", ...)``-shaped results with no way to compare the returned offset
    back to ``len(data)``, since a NUL as the very last byte also yields
    ``new_offset == len(data)``). Callers that track record completeness
    (``TypeDatabase``'s ``_parse_*``/``_skip_subrecord`` methods) must fold
    this into their own return value rather than trusting the decoded name.
    """
    end = data.find(b"\x00", offset)
    if end < 0:
        return ("", len(data), False)
    return (data[offset:end].decode("utf-8", errors="replace"), end + 1, True)


# ---------------------------------------------------------------------------
# TPI stream parser
# ---------------------------------------------------------------------------

@dataclass
class TpiRecord:
    """A single CodeView type record from the TPI stream."""
    type_index: int
    leaf: int       # record kind (LF_xxx)
    data: bytes     # record payload (after leaf type field)


@dataclass
class TpiStream:
    """Parsed TPI (or IPI) stream."""
    type_index_begin: int
    type_index_end: int
    records: list[TpiRecord]
    truncated: bool = False  #: stopped short of type_index_end (P2 review)
    _record_map: dict[int, TpiRecord] = field(default_factory=dict, repr=False)

    def get(self, ti: int) -> TpiRecord | None:
        """Look up a type record by type index."""
        if not self._record_map:
            self._record_map = {r.type_index: r for r in self.records}
        return self._record_map.get(ti)


def parse_tpi_stream(data: bytes) -> TpiStream:
    """Parse TPI stream header + all type records."""
    if len(data) < 56:
        raise ValidationError("TPI stream too small")

    (version, header_size, ti_begin, ti_end, type_bytes,
     ) = struct.unpack_from("<IIIII", data, 0)

    if version != _TPI_VERSION_V80:
        log.warning("Unexpected TPI version %d (expected %d)", version, _TPI_VERSION_V80)

    records: list[TpiRecord] = []
    pos = header_size
    end = header_size + type_bytes
    current_ti = ti_begin

    while pos + 4 <= end and current_ti < ti_end:
        (rec_len,) = struct.unpack_from("<H", data, pos)
        # P2 review, fresh evidence: bound the record by both the header's
        # own declared type-data boundary (``end``) and the buffer's actual
        # length. Checking only ``len(data)`` let a record whose declared
        # ``rec_len`` crosses ``end`` still be accepted whenever the PDB
        # stream carries trailing bytes past the type section (e.g. a
        # hash/index substream appended after it) -- the parser would then
        # consume those non-type-record bytes as if they were part of this
        # record's own payload, potentially reaching ti_end and reporting
        # ``truncated=False`` for a stream that never actually held that
        # many well-formed records.
        if rec_len < 2 or pos + 2 + rec_len > end or pos + 2 + rec_len > len(data):
            break
        (leaf,) = struct.unpack_from("<H", data, pos + 2)
        rec_data = data[pos + 4:pos + 2 + rec_len]
        records.append(TpiRecord(
            type_index=current_ti,
            leaf=leaf,
            data=rec_data,
        ))
        # Records are 4-byte aligned
        pos += 2 + rec_len
        pos = (pos + 3) & ~3
        current_ti += 1

    return TpiStream(
        type_index_begin=ti_begin,
        type_index_end=ti_end,
        records=records,
        truncated=current_ti < ti_end,
    )


def _read_id_string(data: bytes, off: int) -> str:
    """Read a NUL-terminated UTF-8 string from *data* at *off* (best effort).

    Unlike :func:`_read_cstring` this returns only the decoded string (the IPI
    ``LF_STRING_ID`` payload ends with the name, so no trailing offset needed).
    """
    end = data.find(b"\x00", off)
    raw = data[off:] if end < 0 else data[off:end]
    return raw.decode("utf-8", errors="replace")


def extract_udt_source_files(ipi: TpiStream) -> dict[int, str]:
    """Map each UDT's TPI type index to its defining source file.

    Walks the IPI stream (same record layout as TPI, stream 4): collects
    ``LF_STRING_ID`` records (id → string) then resolves ``LF_UDT_SRC_LINE`` /
    ``LF_UDT_MOD_SRC_LINE`` records, each of which ties a TPI UDT type index to
    a source-file string id.  Returns ``{udt_tpi_ti: source_file}``.

    This is the provenance signal MSVC records for user-defined types — the
    PDB equivalent of DWARF ``DW_AT_decl_file`` (ADR-024 Phase 1).  Malformed
    records are skipped rather than fatal.
    """
    string_by_id: dict[int, str] = {}
    src_line_recs: list[tuple[int, int]] = []  # (udt_ti, src_string_id)

    for rec in ipi.records:
        data = rec.data
        try:
            if rec.leaf == LF_STRING_ID:
                # { substr_list_id: u32, name: char[] }
                if len(data) >= 4:
                    string_by_id[rec.type_index] = _read_id_string(data, 4)
            elif rec.leaf in (LF_UDT_SRC_LINE, LF_UDT_MOD_SRC_LINE):
                # Both start with { udt_ti: u32, src_string_id: u32, ... }
                if len(data) >= 8:
                    udt_ti, src_id = struct.unpack_from("<II", data, 0)
                    src_line_recs.append((udt_ti, src_id))
        except struct.error:  # pragma: no cover - defensive
            continue

    out: dict[int, str] = {}
    for udt_ti, src_id in src_line_recs:
        src = string_by_id.get(src_id)
        if src:
            # First definition wins (matches the ODR convention used elsewhere).
            out.setdefault(udt_ti, src)
    return out


# ---------------------------------------------------------------------------
# DBI stream parser
# ---------------------------------------------------------------------------

@dataclass
class DbiHeader:
    """Parsed DBI stream header (64 bytes)."""
    version_signature: int
    version_header: int
    age: int
    global_stream_index: int
    build_number: int
    public_stream_index: int
    pdb_dll_version: int
    sym_record_stream: int
    mod_info_size: int
    section_contribution_size: int
    section_map_size: int
    source_info_size: int
    type_server_map_size: int
    mfc_type_server_index: int
    optional_dbg_header_size: int
    ec_substream_size: int
    flags: int
    machine: int
    padding: int


@dataclass
class DbiModuleInfo:
    """One module entry from the DBI module info substream."""
    module_name: str
    obj_file_name: str
    module_sym_stream: int
    sym_byte_size: int
    c13_byte_size: int
    source_file_count: int


@dataclass
class DbiStream:
    """Parsed DBI stream."""
    header: DbiHeader
    modules: list[DbiModuleInfo]


def parse_dbi_stream(data: bytes) -> DbiStream:
    """Parse DBI stream header and module info substream."""
    if len(data) < 64:
        raise ValidationError("DBI stream too small")

    fields = struct.unpack_from("<iIIHHHHHHiiiiiIiiHHI", data, 0)
    header = DbiHeader(
        version_signature=fields[0],
        version_header=fields[1],
        age=fields[2],
        global_stream_index=fields[3],
        build_number=fields[4],
        public_stream_index=fields[5],
        pdb_dll_version=fields[6],
        sym_record_stream=fields[7],
        mod_info_size=fields[9],
        section_contribution_size=fields[10],
        section_map_size=fields[11],
        source_info_size=fields[12],
        type_server_map_size=fields[13],
        mfc_type_server_index=fields[14],
        optional_dbg_header_size=fields[15],
        ec_substream_size=fields[16],
        flags=fields[17],
        machine=fields[18],
        padding=fields[19],
    )

    modules: list[DbiModuleInfo] = []
    pos = 64
    end = 64 + header.mod_info_size

    while pos + 64 <= end:
        # Fixed-size part of ModInfo (64 bytes)
        # Layout: Unused1(4) + SectionContribEntry(28) + rest(32)
        (_unused1, _sec, _pad1, _offset, _size, _chars,
         _mod_idx, _pad2, _data_crc, _reloc_crc,
         _mod_flags, mod_sym_stream,
         sym_byte_size, _c11_byte_size, c13_byte_size,
         source_file_count, _pad3, _unused2,
         _src_name_idx, _pdb_path_idx,
         ) = struct.unpack_from("<IHHiiIHHIIHHIIIHHIII", data, pos)
        pos += 64

        # Two null-terminated strings: ModuleName, ObjFileName
        mod_name, pos, _ = _read_cstring(data, pos)
        obj_name, pos, _ = _read_cstring(data, pos)

        # 4-byte align
        pos = (pos + 3) & ~3

        modules.append(DbiModuleInfo(
            module_name=mod_name,
            obj_file_name=obj_name,
            module_sym_stream=mod_sym_stream,
            sym_byte_size=sym_byte_size,
            c13_byte_size=c13_byte_size,
            source_file_count=source_file_count,
        ))

    return DbiStream(header=header, modules=modules)


# ---------------------------------------------------------------------------
# High-level type record interpretation
# ---------------------------------------------------------------------------

@dataclass
class CvStruct:
    """Parsed LF_STRUCTURE / LF_CLASS / LF_UNION."""
    type_index: int
    name: str
    field_list_ti: int
    byte_size: int
    is_forward_ref: bool
    is_packed: bool
    is_union: bool
    count: int  # number of members


@dataclass
class CvEnum:
    """Parsed LF_ENUM."""
    type_index: int
    name: str
    field_list_ti: int
    underlying_type_ti: int
    is_forward_ref: bool
    count: int


@dataclass
class CvMember:
    """Parsed LF_MEMBER (non-static data member)."""
    name: str
    type_ti: int
    offset: int
    access: int  # CV_fldattr_t access bits


@dataclass
class CvEnumerator:
    """Parsed LF_ENUMERATE."""
    name: str
    value: int


@dataclass
class CvOneMethod:
    """Parsed LF_ONEMETHOD (non-overloaded member function).

    Carries the method's function-type index so the calling convention of the
    referenced LF_MFUNCTION/LF_PROCEDURE can be resolved by name — the piece
    the PDB path needs to feed ``AdvancedDwarfMetadata.calling_conventions``
    (a PDB has no DWARF-style per-symbol linkage, but methods are named right
    in their class's fieldlist).
    """
    name: str
    type_ti: int


@dataclass
class CvProcedure:
    """Parsed LF_PROCEDURE."""
    type_index: int
    return_type_ti: int
    calling_convention: int
    param_count: int
    arglist_ti: int


@dataclass
class CvMemberFunction:
    """Parsed LF_MFUNCTION."""
    type_index: int
    return_type_ti: int
    class_type_ti: int
    this_type_ti: int
    calling_convention: int
    param_count: int
    arglist_ti: int
    this_adjust: int


@dataclass
class CvPointer:
    """Parsed LF_POINTER."""
    type_index: int
    referent_ti: int
    attrs: int
    byte_size: int


@dataclass
class CvArray:
    """Parsed LF_ARRAY."""
    type_index: int
    element_type_ti: int
    index_type_ti: int
    byte_size: int
    name: str


@dataclass
class CvModifier:
    """Parsed LF_MODIFIER."""
    type_index: int
    modified_ti: int
    is_const: bool
    is_volatile: bool
    is_unaligned: bool


@dataclass
class CvBitfield:
    """Parsed LF_BITFIELD."""
    type_index: int
    underlying_ti: int
    length: int   # bit width
    position: int  # bit position


class TypeDatabase:
    """Indexed collection of parsed CodeView type records.

    Provides name and size resolution for type indices, including simple
    (built-in) types and user-defined types from the TPI stream.
    """

    def __init__(self, tpi: TpiStream) -> None:
        self._tpi = tpi
        self._structs: dict[int, CvStruct] = {}
        self._enums: dict[int, CvEnum] = {}
        self._procedures: dict[int, CvProcedure] = {}
        self._mfunctions: dict[int, CvMemberFunction] = {}
        self._pointers: dict[int, CvPointer] = {}
        self._arrays: dict[int, CvArray] = {}
        self._modifiers: dict[int, CvModifier] = {}
        self._bitfields: dict[int, CvBitfield] = {}
        self._fieldlists: dict[int, list[Any]] = {}  # ti → list of CvMember/CvEnumerator/etc.
        self._arglists: dict[int, list[int]] = {}  # ti → list of type indices
        # Forward-ref → definition mapping
        self._fwd_to_def: dict[int, int] = {}
        self._name_cache: dict[int, str] = {}
        self._size_cache: dict[int, int] = {}
        self._parsed = False
        self.failed_record_count = 0  # records _parse_record raised on (P2 review)
        # Type indices referenced by name()/size() that resolve to no known
        # record at all (P2 review, fresh evidence): populated lazily as
        # type_name()/type_size() are actually called, since both are
        # memoized and a cache hit would otherwise silently skip re-recording
        # the same completeness gap on a later call.
        self._unresolved_type_refs: set[int] = set()

    def parse_all(self) -> None:
        """Parse all TPI records into structured objects."""
        if self._parsed:
            return
        self._parsed = True

        for rec in self._tpi.records:
            try:
                self._parse_record(rec)
            except (struct.error, IndexError, ValueError) as exc:
                self.failed_record_count += 1
                log.debug("Failed to parse TPI record ti=0x%x leaf=0x%x: %s",
                          rec.type_index, rec.leaf, exc)

        # Build forward-ref → definition mapping in 2 passes:
        # Pass 1: collect all definitions (structs + enums) by name
        name_to_def: dict[str, int] = {}
        for ti, s in self._structs.items():
            if not s.is_forward_ref:
                name_to_def[s.name] = ti
        for ti, e in self._enums.items():
            if not e.is_forward_ref:
                name_to_def[e.name] = ti
        # Pass 2: link forward refs to definitions (structs + enums)
        for ti, s in self._structs.items():
            if s.is_forward_ref and s.name in name_to_def:
                self._fwd_to_def[ti] = name_to_def[s.name]
        for ti, e in self._enums.items():
            if e.is_forward_ref and e.name in name_to_def:
                self._fwd_to_def[ti] = name_to_def[e.name]

    def _parse_record(self, rec: TpiRecord) -> None:
        d = rec.data
        ti = rec.type_index
        leaf = rec.leaf

        # P2 review: every _parse_* below returns True/False (complete or a
        # non-exception early exit) instead of the previous silent no-op;
        # False counts toward failed_record_count like a caught exception.
        complete = True
        if leaf in (LF_STRUCTURE, LF_CLASS):
            complete = self._parse_struct(ti, d, is_union=False)
        elif leaf == LF_UNION:
            complete = self._parse_struct(ti, d, is_union=True)
        elif leaf == LF_ENUM:
            complete = self._parse_enum(ti, d)
        elif leaf == LF_FIELDLIST:
            complete = self._parse_fieldlist(ti, d)
        elif leaf == LF_PROCEDURE:
            complete = self._parse_procedure(ti, d)
        elif leaf == LF_MFUNCTION:
            complete = self._parse_mfunction(ti, d)
        elif leaf == LF_POINTER:
            complete = self._parse_pointer(ti, d)
        elif leaf == LF_ARRAY:
            complete = self._parse_array(ti, d)
        elif leaf == LF_MODIFIER:
            complete = self._parse_modifier(ti, d)
        elif leaf == LF_BITFIELD:
            complete = self._parse_bitfield(ti, d)
        elif leaf == LF_ARGLIST:
            complete = self._parse_arglist(ti, d)
        if not complete:
            self.failed_record_count += 1

    def _parse_struct(self, ti: int, d: bytes, *, is_union: bool) -> bool:
        """Parse LF_STRUCTURE, LF_CLASS, or LF_UNION into a CvStruct.

        LF_STRUCTURE/LF_CLASS have a 16-byte header (count, prop, field_ti,
        derived_ti, vshape_ti); LF_UNION has an 8-byte header (count, prop,
        field_ti).  The ``is_union`` flag selects the appropriate layout.
        Returns False (payload too short for the header) rather than raising.
        """
        if is_union:
            if len(d) < 8:
                return False
            (count, prop, field_ti) = struct.unpack_from("<HHI", d, 0)
            pos = 8
        else:
            if len(d) < 16:
                return False
            (count, prop, field_ti, _derived_ti, _vshape_ti) = struct.unpack_from(
                "<HHIII", d, 0)
            pos = 16
        leaf_unsupported: list[bool] = []
        byte_size, pos = _read_numeric_leaf(d, pos, unsupported=leaf_unsupported)
        name, _pos, name_terminated = _read_cstring(d, pos)
        self._structs[ti] = CvStruct(
            type_index=ti,
            name=name,
            field_list_ti=field_ti,
            byte_size=byte_size,
            is_forward_ref=bool(prop & _PROP_FORWARD_REF),
            is_packed=bool(prop & _PROP_PACKED),
            is_union=is_union,
            count=count,
        )
        return name_terminated and not leaf_unsupported

    def _parse_enum(self, ti: int, d: bytes) -> bool:
        if len(d) < 12:
            return False
        (count, prop, utype_ti, field_ti) = struct.unpack_from("<HHII", d, 0)
        name, _, name_terminated = _read_cstring(d, 12)
        self._enums[ti] = CvEnum(
            type_index=ti,
            name=name,
            field_list_ti=field_ti,
            underlying_type_ti=utype_ti,
            is_forward_ref=bool(prop & _PROP_FORWARD_REF),
            count=count,
        )
        return name_terminated

    def _parse_fieldlist(
        self, ti: int, d: bytes,
        _visited: set[int] | None = None,
    ) -> bool:
        """P2 review: False whenever a sub-record was cut short (or an
        unrecognized sub-leaf/circular LF_INDEX forced an early stop) --
        the fieldlist stored on this ti is real but incomplete."""
        if _visited is None:
            _visited = set()
        if ti in _visited:
            log.warning("Circular LF_INDEX reference at ti=0x%x, skipping", ti)
            return False
        _visited.add(ti)

        members: list[Any] = []
        pos = 0
        complete = True
        while pos + 2 <= len(d):
            # Detect single-byte padding (LF_PAD1..LF_PADn = 0xF1..0xFF) before
            # consuming the 2-byte sub_leaf: a byte >= 0xF0 at pos is a pad byte,
            # not the start of a sub-leaf record.
            if d[pos] >= 0xF0:
                skip = d[pos] & 0x0F  # lower nibble = total pad length
                pos += skip if skip > 0 else 1
                continue
            (sub_leaf,) = struct.unpack_from("<H", d, pos)
            pos += 2

            if sub_leaf == LF_MEMBER:
                new_pos = self._parse_lf_member(d, pos, members)
            elif sub_leaf == LF_ENUMERATE:
                new_pos = self._parse_lf_enumerate(d, pos, members)
            elif sub_leaf == LF_INDEX:
                new_pos = self._parse_lf_index(d, pos, members, _visited)
            elif sub_leaf == LF_ONEMETHOD:
                new_pos = self._parse_lf_onemethod(d, pos, members)
            elif sub_leaf in (LF_STMEMBER, LF_NESTTYPE,
                              LF_VFUNCTAB, LF_BCLASS, LF_VBCLASS,
                              LF_IVBCLASS, LF_METHOD):
                # Skip known sub-records we don't need
                new_pos = self._skip_subrecord(sub_leaf, d, pos)
            else:
                # Unknown sub-record — can't safely continue
                log.debug("Unknown fieldlist sub-leaf 0x%04x at pos %d", sub_leaf, pos)
                complete = False
                break

            if new_pos is None:  # truncated sub-record
                complete = False
                break
            # 4-byte alignment within fieldlist
            pos = (new_pos + 3) & ~3

        # P2 review, fresh evidence: the loop's own `pos + 2 <= len(d)`
        # guard never examines a trailing tail shorter than 2 bytes -- a
        # sub-record whose 2-byte leaf tag itself was cut to 0-1 bytes
        # exits the loop silently with `complete` still True. Consume any
        # legitimate trailing LF_PAD* byte(s) the same way the loop body
        # does; anything left over (including a pad claim overshooting the
        # buffer) is a genuine truncation.
        while pos < len(d) and d[pos] >= 0xF0:
            skip = d[pos] & 0x0F
            pos += skip if skip > 0 else 1
        if pos != len(d):
            complete = False

        self._fieldlists[ti] = members
        return complete

    def _parse_lf_member(
        self, d: bytes, pos: int, members: list[Any],
    ) -> int | None:
        """Parse an LF_MEMBER sub-record; return the new position, or None if truncated."""
        if pos + 6 > len(d):
            return None
        (attr, type_ti) = struct.unpack_from("<HI", d, pos)
        pos += 6
        leaf_unsupported: list[bool] = []
        offset_val, pos = _read_numeric_leaf(d, pos, unsupported=leaf_unsupported)
        name, pos, name_terminated = _read_cstring(d, pos)
        members.append(CvMember(
            name=name, type_ti=type_ti,
            offset=offset_val, access=attr & 0x03,
        ))
        return pos if name_terminated and not leaf_unsupported else None

    def _parse_lf_enumerate(
        self, d: bytes, pos: int, members: list[Any],
    ) -> int | None:
        """Parse an LF_ENUMERATE sub-record; return the new position, or None if truncated."""
        if pos + 2 > len(d):
            return None
        (_attr,) = struct.unpack_from("<H", d, pos)
        pos += 2
        leaf_unsupported: list[bool] = []
        val, pos = _read_numeric_leaf(d, pos, unsupported=leaf_unsupported)
        name, pos, name_terminated = _read_cstring(d, pos)
        members.append(CvEnumerator(name=name, value=val))
        return pos if name_terminated and not leaf_unsupported else None

    def _parse_lf_index(
        self, d: bytes, pos: int, members: list[Any], _visited: set[int],
    ) -> int | None:
        """Parse an LF_INDEX continuation sub-record; return the new position, or None if truncated."""
        # LF_INDEX — continuation to another LF_FIELDLIST.
        # Structure: 2-byte sub_leaf (already consumed) + 2-byte padding + 4-byte TI = 6 bytes total.
        if pos + 6 > len(d):
            return None
        (cont_ti,) = struct.unpack_from("<I", d, pos + 2)
        pos += 6
        # Resolve continuation
        cont_rec = self._tpi.get(cont_ti)
        if cont_rec and cont_rec.leaf == LF_FIELDLIST:
            cont_complete = self._parse_fieldlist(cont_ti, cont_rec.data, _visited)
            cont_members = self._fieldlists.get(cont_ti, [])
            members.extend(cont_members)
            if not cont_complete:  # propagate via the same None convention
                return None
        else:
            # P2 review: an unresolved continuation (missing TI or wrong
            # leaf) previously fell through to `return pos` unconditionally,
            # reporting complete despite the continuation's members never
            # being resolved. Propagate via the same None convention.
            return None
        return pos

    def _parse_lf_onemethod(
        self, d: bytes, pos: int, members: list[Any],
    ) -> int | None:
        """Parse an LF_ONEMETHOD sub-record; return the new position, or None if truncated."""
        # attr(2) + type_ti(4) [+ vbaseoff(4) if intro virtual] + name.
        # Parsed (not skipped) so the method's calling convention can
        # be resolved by name via its LF_MFUNCTION type.
        if pos + 6 > len(d):
            return None
        (attr, m_type_ti) = struct.unpack_from("<HI", d, pos)
        pos += 6
        mprop = (attr >> 2) & 0x07
        if mprop in (4, 6):  # intro/pure intro virtual — has vbaseoff
            pos += 4
        m_name, pos, name_terminated = _read_cstring(d, pos)
        if m_name:
            members.append(CvOneMethod(name=m_name, type_ti=m_type_ti))
        return pos if name_terminated else None

    def _skip_subrecord(self, sub_leaf: int, d: bytes, pos: int) -> int | None:
        """Skip known sub-record types we don't parse.

        Returns the new position, or None if truncated (P2 review: used to
        return len(d) here, which the caller's own None check never saw --
        LF_VFUNCTAB in particular had no bounds check at all).
        """
        if sub_leaf == LF_STMEMBER:
            # attr(2) + type_ti(4) + name(variable)
            if pos + 6 > len(d):
                return None
            pos += 6
            _, pos, name_terminated = _read_cstring(d, pos)
            return pos if name_terminated else None

        if sub_leaf == LF_NESTTYPE:
            # padding(2) + type_ti(4) + name(variable)
            if pos + 6 > len(d):
                return None
            pos += 6
            _, pos, name_terminated = _read_cstring(d, pos)
            return pos if name_terminated else None

        # LF_ONEMETHOD is dispatched to _parse_lf_onemethod by _parse_fieldlist
        # before it reaches this fallback, so it is intentionally absent here.

        if sub_leaf == LF_METHOD:
            # count(2) + mlist_ti(4) + name(variable)
            if pos + 6 > len(d):
                return None
            pos += 6
            _, pos, name_terminated = _read_cstring(d, pos)
            return pos if name_terminated else None

        if sub_leaf == LF_VFUNCTAB:
            # padding(2) + type_ti(4)
            if pos + 6 > len(d):
                return None
            return pos + 6

        if sub_leaf == LF_BCLASS:
            # attr(2) + type_ti(4) + offset(numeric leaf)
            if pos + 6 > len(d):
                return None
            pos += 6
            leaf_unsupported: list[bool] = []
            _, pos = _read_numeric_leaf(d, pos, unsupported=leaf_unsupported)
            return None if leaf_unsupported else pos

        if sub_leaf in (LF_VBCLASS, LF_IVBCLASS):
            # attr(2) + direct_ti(4) + vbptr_ti(4) + vbpoff(numeric) + vbtableoff(numeric)
            if pos + 10 > len(d):
                return None
            pos += 10
            leaf_unsupported = []
            _, pos = _read_numeric_leaf(d, pos, unsupported=leaf_unsupported)
            _, pos = _read_numeric_leaf(d, pos, unsupported=leaf_unsupported)
            return None if leaf_unsupported else pos

        return None  # unreachable: caller pre-filters to the leaves above

    def _parse_procedure(self, ti: int, d: bytes) -> bool:
        if len(d) < 12:
            return False
        (rvtype, calltype, _funcattr, parmcount, arglist) = struct.unpack_from(
            "<IBBHI", d, 0)
        self._procedures[ti] = CvProcedure(
            type_index=ti,
            return_type_ti=rvtype,
            calling_convention=calltype,
            param_count=parmcount,
            arglist_ti=arglist,
        )
        return True

    def _parse_mfunction(self, ti: int, d: bytes) -> bool:
        if len(d) < 24:
            return False
        (rvtype, classtype, thistype, calltype, _funcattr,
         parmcount, arglist, thisadjust) = struct.unpack_from(
            "<IIIBBHIi", d, 0)
        self._mfunctions[ti] = CvMemberFunction(
            type_index=ti,
            return_type_ti=rvtype,
            class_type_ti=classtype,
            this_type_ti=thistype,
            calling_convention=calltype,
            param_count=parmcount,
            arglist_ti=arglist,
            this_adjust=thisadjust,
        )
        return True

    def _parse_pointer(self, ti: int, d: bytes) -> bool:
        if len(d) < 8:
            return False
        (referent, attrs) = struct.unpack_from("<II", d, 0)
        size = (attrs >> 13) & 0x3F
        self._pointers[ti] = CvPointer(
            type_index=ti,
            referent_ti=referent,
            attrs=attrs,
            byte_size=size if size else 8,  # default to 8 for 64-bit
        )
        return True

    def _parse_array(self, ti: int, d: bytes) -> bool:
        if len(d) < 8:
            return False
        (elem_ti, idx_ti) = struct.unpack_from("<II", d, 0)
        pos = 8
        leaf_unsupported: list[bool] = []
        byte_size, pos = _read_numeric_leaf(d, pos, unsupported=leaf_unsupported)
        name, _, name_terminated = _read_cstring(d, pos)
        self._arrays[ti] = CvArray(
            type_index=ti,
            element_type_ti=elem_ti,
            index_type_ti=idx_ti,
            byte_size=byte_size,
            name=name,
        )
        return name_terminated and not leaf_unsupported

    def _parse_modifier(self, ti: int, d: bytes) -> bool:
        if len(d) < 6:
            return False
        (mod_ti, attr) = struct.unpack_from("<IH", d, 0)
        self._modifiers[ti] = CvModifier(
            type_index=ti,
            modified_ti=mod_ti,
            is_const=bool(attr & 0x01),
            is_volatile=bool(attr & 0x02),
            is_unaligned=bool(attr & 0x04),
        )
        return True

    def _parse_bitfield(self, ti: int, d: bytes) -> bool:
        if len(d) < 6:
            return False
        (underlying, length, position) = struct.unpack_from("<IBB", d, 0)
        self._bitfields[ti] = CvBitfield(
            type_index=ti,
            underlying_ti=underlying,
            length=length,
            position=position,
        )
        return True

    def _parse_arglist(self, ti: int, d: bytes) -> bool:
        if len(d) < 4:
            return False
        (count,) = struct.unpack_from("<I", d, 0)
        args = []
        pos = 4
        complete = True
        for _ in range(count):
            if pos + 4 > len(d):
                complete = False
                break
            (arg_ti,) = struct.unpack_from("<I", d, pos)
            pos += 4
            args.append(arg_ti)
        self._arglists[ti] = args
        return complete

    # --- Public query API ---

    def resolve_struct(self, ti: int) -> CvStruct | None:
        """Resolve a type index to a CvStruct (following forward refs)."""
        real_ti = self._fwd_to_def.get(ti, ti)
        return self._structs.get(real_ti)

    def resolve_enum(self, ti: int) -> CvEnum | None:
        """Resolve a type index to a CvEnum (following forward refs)."""
        real_ti = self._fwd_to_def.get(ti, ti)
        return self._enums.get(real_ti)

    @property
    def unresolved_type_ref_count(self) -> int:
        """Count of distinct type indices name()/size() could not resolve.

        P2 review, fresh evidence: a valid ``LF_MEMBER``/``LF_ENUM`` whose
        ``type_ti``/``utype_ti`` names an index absent from the TPI database
        (or a ``LF_POINTER``/``LF_MODIFIER``/``LF_ARRAY`` wrapper naming one)
        previously fell through ``type_name()``/``type_size()`` to a
        ``"<ti:0x...>"`` placeholder and a size of 0 with no completeness
        signal -- the same silent-fallback shape ``has_fieldlist()`` above
        fixes for field-list references, one layer down at the individual
        member/underlying *type* reference itself.
        """
        return len(self._unresolved_type_refs)

    def get_fieldlist(self, ti: int) -> list[Any]:
        """Get the parsed fieldlist members for type index *ti*."""
        return self._fieldlists.get(ti, [])

    def has_fieldlist(self, ti: int) -> bool:
        """Return True if *ti* names an actually-parsed LF_FIELDLIST record.

        P2 review, fresh evidence: a fully-framed LF_STRUCTURE/LF_UNION/
        LF_ENUM can declare a non-zero ``field_list_ti`` that names an index
        the TPI stream never defined at all, or that resolves to some other
        (non-fieldlist) record kind -- both collapse to the same empty
        ``get_fieldlist()`` result as a struct/enum that legitimately has no
        members, with no way for a caller to tell "zero members" apart from
        "member list unresolvable". Callers use this to distinguish the two
        before trusting an empty ``get_fieldlist()`` result.
        """
        return ti in self._fieldlists

    def get_procedure(self, ti: int) -> CvProcedure | None:
        return self._procedures.get(ti)

    def get_mfunction(self, ti: int) -> CvMemberFunction | None:
        return self._mfunctions.get(ti)

    def all_structs(self) -> dict[int, CvStruct]:
        return self._structs

    def all_enums(self) -> dict[int, CvEnum]:
        return self._enums

    def get_bitfield(self, ti: int) -> CvBitfield | None:
        """Return the CvBitfield for type index *ti*, or None."""
        return self._bitfields.get(ti)

    def all_procedures(self) -> dict[int, CvProcedure]:
        return self._procedures

    def all_mfunctions(self) -> dict[int, CvMemberFunction]:
        return self._mfunctions

    def type_name(self, ti: int, depth: int = 0) -> str:
        """Resolve a type index to a human-readable name."""
        if depth > 10:
            return "..."
        if ti in self._name_cache:
            return self._name_cache[ti]

        name = self._resolve_type_name(ti, depth)
        self._name_cache[ti] = name
        return name

    def type_size(self, ti: int, depth: int = 0) -> int:
        """Resolve a type index to its byte size."""
        if depth > 10:
            return 0
        if ti in self._size_cache:
            return self._size_cache[ti]

        size = self._resolve_type_size(ti, depth)
        self._size_cache[ti] = size
        return size

    def _resolve_type_name(self, ti: int, depth: int) -> str:
        # Simple (built-in) types
        if ti < _TI_BASE:
            kind = ti & 0xFF
            mode = (ti >> 8) & 0x0F
            base = _SIMPLE_TYPE_NAMES.get(kind, f"<simple:0x{kind:02x}>")
            if mode == 0:
                return base
            if mode in (0x02, 0x06):  # near32 / near64
                return f"{base} *"
            return f"{base} *"

        s = self._structs.get(ti)
        if s:
            real_ti = self._fwd_to_def.get(ti, ti)
            real = self._structs.get(real_ti, s)
            return real.name

        e = self._enums.get(ti)
        if e:
            return f"enum {e.name}"

        p = self._pointers.get(ti)
        if p:
            ref_name = self.type_name(p.referent_ti, depth + 1)
            mode = (p.attrs >> 5) & 0x07
            if mode == 1:  # LValueReference
                return f"{ref_name} &"
            if mode == 4:  # RValueReference
                return f"{ref_name} &&"
            return f"{ref_name} *"

        m = self._modifiers.get(ti)
        if m:
            base = self.type_name(m.modified_ti, depth + 1)
            quals = []
            if m.is_const:
                quals.append("const")
            if m.is_volatile:
                quals.append("volatile")
            return f"{' '.join(quals)} {base}" if quals else base

        a = self._arrays.get(ti)
        if a:
            elem = self.type_name(a.element_type_ti, depth + 1)
            return f"{elem}[]"

        bf = self._bitfields.get(ti)
        if bf:
            return self.type_name(bf.underlying_ti, depth + 1)

        proc = self._procedures.get(ti)
        if proc:
            return "fn(...)"

        mf = self._mfunctions.get(ti)
        if mf:
            return "fn(...)"

        # Reaching here means ti >= _TI_BASE (the simple-type branch above
        # always returns first) and matched none of the known record
        # categories -- a genuinely unresolvable reference, not a
        # legitimately-untyped one (P2 review, fresh evidence).
        self._unresolved_type_refs.add(ti)
        return f"<ti:0x{ti:04x}>"

    def _resolve_type_size(self, ti: int, depth: int) -> int:
        if ti < _TI_BASE:
            kind = ti & 0xFF
            mode = (ti >> 8) & 0x0F
            if mode == 0:
                return _SIMPLE_TYPE_SIZES.get(kind, 0)
            # Pointer modes: size depends on 32-bit vs 64-bit
            if mode == 0x02:  # near32
                return 4
            if mode == 0x06:  # near64
                return 8
            return 8  # default pointer size

        s = self._structs.get(ti)
        if s:
            real_ti = self._fwd_to_def.get(ti, ti)
            real = self._structs.get(real_ti, s)
            return real.byte_size

        p = self._pointers.get(ti)
        if p:
            return p.byte_size

        m = self._modifiers.get(ti)
        if m:
            return self.type_size(m.modified_ti, depth + 1)

        a = self._arrays.get(ti)
        if a:
            return a.byte_size

        bf = self._bitfields.get(ti)
        if bf:
            return self.type_size(bf.underlying_ti, depth + 1)

        e = self._enums.get(ti)
        if e:
            return self.type_size(e.underlying_type_ti, depth + 1)

        # Procedures/member-functions are known but legitimately sizeless
        # (a function has no byte size) -- not unresolved, mirroring
        # type_name()'s own proc/mfunc branch above.
        if ti in self._procedures or ti in self._mfunctions:
            return 0

        # Reaching here means ti >= _TI_BASE and matched no known record
        # category at all -- a genuinely unresolvable reference (P2 review,
        # fresh evidence; same shape as _resolve_type_name's fallback).
        self._unresolved_type_refs.add(ti)
        return 0

    def calling_convention_name(self, cc: int) -> str:
        """Map a CV_call_e value to a human-readable name."""
        return _CC_NAMES.get(cc, f"cc_{cc:#x}")

    def function_calling_convention(self, ti: int) -> int | None:
        """Return the CV_call_e value of a function type index, if known.

        Resolves both member-function (LF_MFUNCTION) and free-function
        (LF_PROCEDURE) type records; ``None`` for anything else.
        """
        mf = self._mfunctions.get(ti)
        if mf is not None:
            return mf.calling_convention
        proc = self._procedures.get(ti)
        if proc is not None:
            return proc.calling_convention
        return None


# ---------------------------------------------------------------------------
# Top-level PDB file parser
# ---------------------------------------------------------------------------

@dataclass
class PdbFile:
    """Fully parsed PDB file."""
    msf: MsfFile
    tpi: TpiStream | None = None
    dbi: DbiStream | None = None
    types: TypeDatabase | None = None
    ipi: TpiStream | None = None
    # UDT name → defining source file, from the IPI UDT_SRC_LINE records
    # (ADR-024 Phase 1 provenance). Empty when the PDB has no IPI stream.
    udt_source_files: dict[str, str] = field(default_factory=dict)


def parse_pdb(path: Path) -> PdbFile:
    """Parse a PDB file and return structured data.

    Raises ``ValueError`` on invalid format, ``OSError`` on I/O errors.
    """
    data = path.read_bytes()
    msf = parse_msf(data)

    pdb = PdbFile(msf=msf)

    # Parse TPI stream (stream 2)
    if msf.stream_count() > _TPI_STREAM:
        tpi_data = msf.stream_data(_TPI_STREAM)
        if tpi_data:
            try:
                pdb.tpi = parse_tpi_stream(tpi_data)
            except (ValueError, struct.error) as exc:
                log.debug("Failed to parse TPI stream from %s: %s", path, exc)
            else:
                pdb.types = TypeDatabase(pdb.tpi)
                pdb.types.parse_all()

    # Parse DBI stream (stream 3) — failures are non-fatal: TPI data is preserved.
    if msf.stream_count() > _DBI_STREAM:
        dbi_data = msf.stream_data(_DBI_STREAM)
        if dbi_data:
            try:
                pdb.dbi = parse_dbi_stream(dbi_data)
            except (ValueError, struct.error) as exc:
                log.debug("Failed to parse DBI stream from %s: %s", path, exc)

    # Parse IPI stream (stream 4) for UDT source-file provenance — non-fatal.
    if pdb.types is not None and msf.stream_count() > _IPI_STREAM:
        ipi_data = msf.stream_data(_IPI_STREAM)
        if ipi_data:
            try:
                pdb.ipi = parse_tpi_stream(ipi_data)
                pdb.udt_source_files = _resolve_udt_source_files(pdb.ipi, pdb.types)
            except (ValueError, struct.error) as exc:
                log.debug("Failed to parse IPI stream from %s: %s", path, exc)

    return pdb


def _resolve_udt_source_files(
    ipi: TpiStream, types: TypeDatabase
) -> dict[str, str]:
    """Resolve the IPI UDT-source map (ti → file) to a UDT *name* → file map."""
    by_ti = extract_udt_source_files(ipi)
    if not by_ti:
        return {}
    name_by_ti: dict[int, str] = {}
    for ti, cv in types.all_structs().items():
        if cv.name:
            name_by_ti[ti] = cv.name
    for ti, cv_enum in types.all_enums().items():
        if cv_enum.name:
            name_by_ti[ti] = cv_enum.name
    out: dict[str, str] = {}
    for ti, src in by_ti.items():
        name = name_by_ti.get(ti)
        if name:
            out.setdefault(name, src)
    return out
