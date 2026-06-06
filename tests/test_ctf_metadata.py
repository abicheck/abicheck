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

"""Tests for CTF (Compact C Type Format) parser."""

from __future__ import annotations

import struct
import zlib
from unittest.mock import MagicMock, patch

import pytest

from abicheck.ctf_metadata import (
    _CTF_V2_LSTRUCT_THRESH,
    CTF_F_COMPRESS,
    CTF_K_ARRAY,
    CTF_K_CONST,
    CTF_K_ENUM,
    CTF_K_FLOAT,
    CTF_K_FORWARD,
    CTF_K_FUNCTION,
    CTF_K_INTEGER,
    CTF_K_POINTER,
    CTF_K_RESTRICT,
    CTF_K_STRUCT,
    CTF_K_TYPEDEF,
    CTF_K_UNION,
    CTF_K_VOLATILE,
    CTF_MAGIC,
    CTF_VERSION_2,
    CTF_VERSION_3,
    CtfMetadata,
    CtfType,
    _decompress_if_needed,
    _extra_data_size,
    _extract_enums,
    _parse_header,
    _parse_info_v2,
    _parse_info_v3,
    _parse_types,
    _read_ctf_section,
    _read_string,
    _TypeResolver,
    parse_ctf_from_bytes,
    parse_ctf_metadata,
)

# ---------------------------------------------------------------------------
# Helpers to build synthetic CTF v3 blobs
# ---------------------------------------------------------------------------


class CtfBuilder:
    """Helper to construct synthetic CTF v3 binary blobs for testing."""

    def __init__(self) -> None:
        self._strings = bytearray(b"\x00")  # string section starts with NUL
        self._type_entries: list[bytes] = []
        self._str_offsets: dict[str, int] = {"": 0}

    def add_string(self, s: str) -> int:
        if s in self._str_offsets:
            return self._str_offsets[s]
        off = len(self._strings)
        self._strings.extend(s.encode("utf-8") + b"\x00")
        self._str_offsets[s] = off
        return off

    def add_type(
        self,
        name: str,
        kind: int,
        vlen: int,
        size_or_type: int,
        extra: bytes = b"",
        isroot: bool = False,
    ) -> int:
        """Add a CTF v3 type entry, return its 1-based type ID."""
        name_off = self.add_string(name) if name else 0
        # v3 info: isroot(1) + kind(5) in upper byte, vlen(16) in lower
        info = (int(isroot) << 31) | (kind << 24) | (vlen & 0xFFFF)
        entry = struct.pack("<III", name_off, info, size_or_type) + extra
        self._type_entries.append(entry)
        return len(self._type_entries)  # 1-based

    def build(self, *, compress: bool = False) -> bytes:
        """Build a complete CTF v3 blob."""
        type_data = b"".join(self._type_entries)
        str_data = bytes(self._strings)
        # Offsets are relative to the end of the header
        label_off = 0
        object_off = 0
        func_off = 0
        type_off = 0
        str_off = len(type_data)
        str_len = len(str_data)

        flags = CTF_F_COMPRESS if compress else 0

        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, flags)
        header += struct.pack(
            "<IIIIIIII",
            0,  # parent_label
            0,  # parent_name
            label_off,
            object_off,
            func_off,
            type_off,
            str_off,
            str_len,
        )

        body = type_data + str_data

        if compress:
            # Compress everything after the preamble (4 bytes)
            preamble = header[:4]
            rest = header[4:] + body
            compressed = zlib.compress(rest)
            return preamble + compressed
        else:
            return header + body


# ---------------------------------------------------------------------------
# String table
# ---------------------------------------------------------------------------


class TestReadString:
    def test_basic(self) -> None:
        data = b"hello\x00world\x00"
        assert _read_string(data, 0) == "hello"
        assert _read_string(data, 6) == "world"

    def test_empty(self) -> None:
        assert _read_string(b"\x00", 0) == ""

    def test_out_of_bounds(self) -> None:
        assert _read_string(b"abc\x00", 100) == ""


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


class TestParseHeader:
    def test_valid_header(self) -> None:
        b = CtfBuilder()
        data = b.build()
        hdr = _parse_header(data)
        assert hdr.magic == CTF_MAGIC
        assert hdr.version == CTF_VERSION_3

    def test_bad_magic(self) -> None:
        data = struct.pack("<HBB", 0xDEAD, 3, 0) + b"\x00" * 40
        with pytest.raises(ValueError, match="Bad CTF magic"):
            _parse_header(data)

    def test_too_small(self) -> None:
        with pytest.raises(ValueError, match="too small"):
            _parse_header(b"\x00")


# ---------------------------------------------------------------------------
# Full parse: structs
# ---------------------------------------------------------------------------


class TestCtfStructs:
    def test_simple_struct(self) -> None:
        b = CtfBuilder()
        # Add INT type (id=1): encoding word = nr_bits in lower 16 bits
        int_enc = struct.pack("<I", 32)  # 32 bits
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

        # Add struct with 2 members (id=2)
        # v3 small struct member: name_off(4) + packed(type<<16 | bit_offset)(4)
        m1_name = b.add_string("x")
        m2_name = b.add_string("y")
        members = struct.pack("<II", m1_name, (1 << 16) | 0)  # type=1, offset=0
        members += struct.pack("<II", m2_name, (1 << 16) | 32)  # type=1, offset=32
        b.add_type("point", CTF_K_STRUCT, 2, 8, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf
        assert "point" in meta.structs

        s = meta.structs["point"]
        assert s.name == "point"
        assert s.byte_size == 8
        assert len(s.fields) == 2
        assert s.fields[0].name == "x"
        assert s.fields[0].byte_offset == 0
        assert s.fields[1].name == "y"
        assert s.fields[1].byte_offset == 4

    def test_union(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

        m1_name = b.add_string("i")
        m2_name = b.add_string("f")
        members = struct.pack("<II", m1_name, (1 << 16) | 0)
        members += struct.pack("<II", m2_name, (1 << 16) | 0)
        b.add_type("my_union", CTF_K_UNION, 2, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert "my_union" in meta.structs
        assert meta.structs["my_union"].is_union

    def test_anonymous_struct_skipped(self) -> None:
        b = CtfBuilder()
        b.add_type("", CTF_K_STRUCT, 0, 0, extra=b"")
        meta = parse_ctf_from_bytes(b.build())
        assert len(meta.structs) == 0


# ---------------------------------------------------------------------------
# Full parse: enums
# ---------------------------------------------------------------------------


class TestCtfEnums:
    def test_simple_enum(self) -> None:
        b = CtfBuilder()
        e1_name = b.add_string("RED")
        e2_name = b.add_string("GREEN")
        e3_name = b.add_string("BLUE")
        entries = struct.pack("<Ii", e1_name, 0)
        entries += struct.pack("<Ii", e2_name, 1)
        entries += struct.pack("<Ii", e3_name, 2)
        b.add_type("color", CTF_K_ENUM, 3, 4, extra=entries)

        meta = parse_ctf_from_bytes(b.build())
        assert "color" in meta.enums
        e = meta.enums["color"]
        assert e.underlying_byte_size == 4
        assert e.members == {"RED": 0, "GREEN": 1, "BLUE": 2}

    def test_negative_enum_values(self) -> None:
        b = CtfBuilder()
        e_name = b.add_string("NEG")
        entries = struct.pack("<Ii", e_name, -42)
        b.add_type("signed_enum", CTF_K_ENUM, 1, 4, extra=entries)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.enums["signed_enum"].members["NEG"] == -42


# ---------------------------------------------------------------------------
# Full parse: typedefs
# ---------------------------------------------------------------------------


class TestCtfTypedefs:
    def test_typedef(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("myint", CTF_K_TYPEDEF, 0, 1)  # typedef int myint

        meta = parse_ctf_from_bytes(b.build())
        assert meta.typedefs.get("myint") == "int"


# ---------------------------------------------------------------------------
# Compression
# ---------------------------------------------------------------------------


class TestCtfCompression:
    def test_compressed_ctf(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

        m_name = b.add_string("val")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("simple", CTF_K_STRUCT, 1, 4, extra=members)

        data = b.build(compress=True)
        meta = parse_ctf_from_bytes(data)
        assert meta.has_ctf
        assert "simple" in meta.structs


# ---------------------------------------------------------------------------
# to_dwarf_metadata conversion
# ---------------------------------------------------------------------------


class TestToDwarfMetadata:
    def test_conversion(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        m_name = b.add_string("val")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("simple", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        dwarf = meta.to_dwarf_metadata()

        assert dwarf.has_dwarf
        assert "simple" in dwarf.structs


# ---------------------------------------------------------------------------
# Error handling / graceful degradation
# ---------------------------------------------------------------------------


class TestCtfErrorHandling:
    def test_empty_data(self) -> None:
        meta = parse_ctf_from_bytes(b"")
        assert not meta.has_ctf

    def test_bad_magic(self) -> None:
        meta = parse_ctf_from_bytes(b"\x00" * 100)
        assert not meta.has_ctf


# ---------------------------------------------------------------------------
# TypeMetadataSource protocol
# ---------------------------------------------------------------------------


class TestTypeMetadataSourceProtocol:
    def test_protocol_methods(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        m_name = b.add_string("x")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("point", CTF_K_STRUCT, 1, 4, extra=members)

        e_name = b.add_string("A")
        entries = struct.pack("<Ii", e_name, 0)
        b.add_type("my_enum", CTF_K_ENUM, 1, 4, extra=entries)

        meta = parse_ctf_from_bytes(b.build())

        assert meta.has_data is True
        assert meta.get_struct_layout("point") is not None
        assert meta.get_struct_layout("nonexistent") is None
        assert meta.get_enum_info("my_enum") is not None
        assert meta.get_enum_info("nonexistent") is None

    def test_isinstance_check(self) -> None:
        from abicheck.type_metadata import TypeMetadataSource

        meta = CtfMetadata(has_ctf=True)
        assert isinstance(meta, TypeMetadataSource)


# ---------------------------------------------------------------------------
# CtfMetadata accessor methods
# ---------------------------------------------------------------------------


class TestCtfMetadataAccessors:
    def test_get_function_proto(self) -> None:
        meta = CtfMetadata(has_ctf=True)
        assert meta.get_function_proto("x") is None

    def test_get_typedef(self) -> None:
        meta = CtfMetadata(has_ctf=True)
        assert meta.get_typedef("x") is None

    def test_empty_metadata(self) -> None:
        meta = CtfMetadata()
        assert meta.has_data is False
        assert meta.get_struct_layout("x") is None
        assert meta.get_enum_info("x") is None
        assert meta.get_function_proto("x") is None
        assert meta.get_typedef("x") is None

    def test_isroot_property(self) -> None:
        from abicheck.ctf_metadata import CtfType

        t = CtfType(
            type_id=1,
            name_off=0,
            info=(1 << 31) | (CTF_K_INTEGER << 24),
            size_or_type=4,
        )
        assert t.isroot is True
        t2 = CtfType(type_id=2, name_off=0, info=(CTF_K_INTEGER << 24), size_or_type=4)
        assert t2.isroot is False


# ---------------------------------------------------------------------------
# Parse info functions
# ---------------------------------------------------------------------------


class TestParseInfo:
    def test_parse_info_v2(self) -> None:
        # v2: kind(5) at bits 15-11, isroot(1) at bit 10, vlen(10) at bits 9-0
        info = (CTF_K_STRUCT << 11) | (1 << 10) | 5
        kind, vlen, isroot = _parse_info_v2(info)
        assert kind == CTF_K_STRUCT
        assert vlen == 5
        assert isroot is True

    def test_parse_info_v3(self) -> None:
        # v3: kind(5) at bits 28-24, isroot(1) at bit 31, vlen(16) at bits 15-0
        info = (1 << 31) | (CTF_K_ENUM << 24) | 42
        kind, vlen, isroot = _parse_info_v3(info)
        assert kind == CTF_K_ENUM
        assert vlen == 42
        assert isroot is True


# ---------------------------------------------------------------------------
# _extra_data_size coverage
# ---------------------------------------------------------------------------


class TestCtfExtraDataSize:
    def test_integer(self) -> None:
        assert _extra_data_size(CTF_K_INTEGER, 0, CTF_VERSION_3, 0) == 4

    def test_float(self) -> None:
        assert _extra_data_size(CTF_K_FLOAT, 0, CTF_VERSION_3, 0) == 4

    def test_array_v3(self) -> None:
        assert _extra_data_size(CTF_K_ARRAY, 0, CTF_VERSION_3, 0) == 12

    def test_array_v2(self) -> None:
        assert _extra_data_size(CTF_K_ARRAY, 0, CTF_VERSION_2, 0) == 6

    def test_struct_v3_small(self) -> None:
        assert _extra_data_size(CTF_K_STRUCT, 3, CTF_VERSION_3, 100) == 24  # 3*8

    def test_struct_v3_large(self) -> None:
        assert _extra_data_size(CTF_K_STRUCT, 3, CTF_VERSION_3, 0x2000) == 36  # 3*12

    def test_struct_v2_small(self) -> None:
        assert _extra_data_size(CTF_K_STRUCT, 3, CTF_VERSION_2, 100) == 12  # 3*4

    def test_struct_v2_large(self) -> None:
        assert _extra_data_size(CTF_K_STRUCT, 3, CTF_VERSION_2, 0x2000) == 24  # 3*8

    def test_enum(self) -> None:
        assert _extra_data_size(CTF_K_ENUM, 5, CTF_VERSION_3, 0) == 40  # 5*8

    def test_function_v3(self) -> None:
        # 2 args * 4 bytes = 8, already aligned
        assert _extra_data_size(CTF_K_FUNCTION, 2, CTF_VERSION_3, 0) == 8

    def test_function_v3_padding(self) -> None:
        # 3 args * 4 = 12, already aligned
        assert _extra_data_size(CTF_K_FUNCTION, 3, CTF_VERSION_3, 0) == 12

    def test_function_v2(self) -> None:
        # 2 args * 2 = 4, already aligned
        assert _extra_data_size(CTF_K_FUNCTION, 2, CTF_VERSION_2, 0) == 4

    def test_function_v2_padding(self) -> None:
        # 3 args * 2 = 6, padded to 8
        assert _extra_data_size(CTF_K_FUNCTION, 3, CTF_VERSION_2, 0) == 8

    def test_pointer_no_extra(self) -> None:
        assert _extra_data_size(CTF_K_POINTER, 0, CTF_VERSION_3, 0) == 0

    def test_typedef_no_extra(self) -> None:
        assert _extra_data_size(CTF_K_TYPEDEF, 0, CTF_VERSION_3, 0) == 0

    def test_forward_no_extra(self) -> None:
        assert _extra_data_size(CTF_K_FORWARD, 0, CTF_VERSION_3, 0) == 0

    def test_const_no_extra(self) -> None:
        assert _extra_data_size(CTF_K_CONST, 0, CTF_VERSION_3, 0) == 0


# ---------------------------------------------------------------------------
# Decompression
# ---------------------------------------------------------------------------


class TestDecompression:
    def test_decompress_not_needed(self) -> None:
        from abicheck.ctf_metadata import CtfHeader

        hdr = CtfHeader(
            magic=CTF_MAGIC,
            version=CTF_VERSION_3,
            flags=0,
            parent_label=0,
            parent_name=0,
            label_off=0,
            object_off=0,
            func_off=0,
            type_off=0,
            str_off=0,
            str_len=0,
        )
        data = b"some data"
        result = _decompress_if_needed(data, hdr)
        assert result == data

    def test_decompress_exceeds_size_limit(self) -> None:
        """Zip bomb guard: decompression must fail when output exceeds 256 MiB."""
        from abicheck.ctf_metadata import CtfHeader

        hdr = CtfHeader(
            magic=CTF_MAGIC,
            version=CTF_VERSION_3,
            flags=CTF_F_COMPRESS,
            parent_label=0,
            parent_name=0,
            label_off=0,
            object_off=0,
            func_off=0,
            type_off=0,
            str_off=0,
            str_len=0,
        )
        # Build a zlib-compressed blob of zeros that decompresses to ~300 MiB.
        # Zeros compress extremely well, so the compressed payload is tiny.
        big_data = b"\x00" * (300 * 1024 * 1024)
        compressed = zlib.compress(big_data)
        preamble = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, CTF_F_COMPRESS)
        data = preamble + compressed
        with pytest.raises(ValueError, match="exceeds 256 MiB limit"):
            _decompress_if_needed(data, hdr)

    def test_decompress_bad_data(self) -> None:
        from abicheck.ctf_metadata import CtfHeader

        hdr = CtfHeader(
            magic=CTF_MAGIC,
            version=CTF_VERSION_3,
            flags=CTF_F_COMPRESS,
            parent_label=0,
            parent_name=0,
            label_off=0,
            object_off=0,
            func_off=0,
            type_off=0,
            str_off=0,
            str_len=0,
        )
        # Preamble + garbage (not valid zlib)
        data = (
            struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, CTF_F_COMPRESS) + b"garbage"
        )
        with pytest.raises(ValueError, match="decompression failed"):
            _decompress_if_needed(data, hdr)


# ---------------------------------------------------------------------------
# Type resolver extended coverage
# ---------------------------------------------------------------------------


class TestCtfTypeResolverExtended:
    def test_pointer_name(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        # Pointer: size_or_type = type id of pointee
        # In v3, info = kind << 24
        name_off = 0
        info = CTF_K_POINTER << 24
        entry = struct.pack("<III", name_off, info, 1)  # points to int (id=1)
        b._type_entries.append(entry)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf

    def test_volatile_name(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_VOLATILE, 0, 1)  # volatile int
        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf

    def test_const_type(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_CONST, 0, 1)
        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf

    def test_restrict_type(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_RESTRICT, 0, 1)
        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf

    def test_forward_type(self) -> None:
        b = CtfBuilder()
        b.add_type("fwd_struct", CTF_K_FORWARD, 0, 0)
        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf

    def test_array_type(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        # v3 array: contents(4) + index(4) + nelems(4)
        array_extra = struct.pack("<III", 1, 1, 5)
        b.add_type("", CTF_K_ARRAY, 0, 0, extra=array_extra)
        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf

    def test_float_type(self) -> None:
        b = CtfBuilder()
        float_enc = struct.pack("<I", 64)
        b.add_type("double", CTF_K_FLOAT, 0, 8, extra=float_enc)
        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf

    def test_function_type(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        # function with 2 params: return type in size_or_type, params as extra
        params = struct.pack("<II", 1, 1)  # two int params
        b.add_type("", CTF_K_FUNCTION, 2, 1, extra=params)
        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf


# ---------------------------------------------------------------------------
# Header edge cases
# ---------------------------------------------------------------------------


class TestCtfHeaderExtended:
    def test_unsupported_version(self) -> None:
        data = struct.pack("<HBB", CTF_MAGIC, 99, 0) + b"\x00" * 40
        with pytest.raises(ValueError, match="Unsupported CTF version"):
            _parse_header(data)

    def test_truncated_header(self) -> None:
        data = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, 0)  # only 4 bytes
        with pytest.raises(ValueError, match="truncated"):
            _parse_header(data)

    def test_v2_header(self) -> None:
        """V2 header should also parse since header structure is the same."""
        b = CtfBuilder()
        data = b.build()
        # Replace version byte with V2
        data = data[:2] + bytes([CTF_VERSION_2]) + data[3:]
        hdr = _parse_header(data)
        assert hdr.version == CTF_VERSION_2


# ---------------------------------------------------------------------------
# CTF error/edge cases
# ---------------------------------------------------------------------------


class TestCtfEdgeCases:
    def test_anonymous_enum_skipped(self) -> None:
        b = CtfBuilder()
        e_name = b.add_string("A")
        entries = struct.pack("<Ii", e_name, 0)
        b.add_type("", CTF_K_ENUM, 1, 4, extra=entries)
        meta = parse_ctf_from_bytes(b.build())
        assert len(meta.enums) == 0

    def test_anonymous_typedef_skipped(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_TYPEDEF, 0, 1)
        meta = parse_ctf_from_bytes(b.build())
        assert len(meta.typedefs) == 0

    def test_section_bounds_exceed_data(self) -> None:
        """Header claiming sections beyond data size."""
        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, 0)
        header += struct.pack("<IIIIIIII", 0, 0, 0, 0, 0, 0, 99999, 1)
        data = header + b"\x00"
        meta = parse_ctf_from_bytes(data)
        assert not meta.has_ctf

    def test_type_count(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("float", CTF_K_FLOAT, 0, 4, extra=int_enc)
        meta = parse_ctf_from_bytes(b.build())
        assert meta.type_count == 2

    def test_duplicate_struct_first_wins(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

        m_name = b.add_string("x")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("dup", CTF_K_STRUCT, 1, 4, extra=members)
        b.add_type("dup", CTF_K_STRUCT, 1, 8, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["dup"].byte_size == 4

    def test_duplicate_enum_first_wins(self) -> None:
        b = CtfBuilder()
        e_name = b.add_string("A")
        entries = struct.pack("<Ii", e_name, 0)
        b.add_type("dup", CTF_K_ENUM, 1, 4, extra=entries)

        e2_name = b.add_string("B")
        entries2 = struct.pack("<Ii", e2_name, 1)
        b.add_type("dup", CTF_K_ENUM, 1, 4, extra=entries2)

        meta = parse_ctf_from_bytes(b.build())
        assert "A" in meta.enums["dup"].members

    def test_string_out_of_bounds(self) -> None:
        assert _read_string(b"abc\x00", 100) == ""
        assert _read_string(b"abc\x00", -1) == ""

    def test_string_no_null(self) -> None:
        assert _read_string(b"no_null", 0) == "no_null"


# ---------------------------------------------------------------------------
# Resolver coverage through struct member type resolution
# ---------------------------------------------------------------------------


class TestCtfResolverThroughExtraction:
    """Test resolver name/size paths by creating structs with typed members."""

    def test_pointer_member(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)  # id=1
        # Pointer to int (id=2)
        b.add_type("", CTF_K_POINTER, 0, 1)
        # Struct with pointer member
        m_name = b.add_string("ptr")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 8, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        s = meta.structs["s"]
        assert s.fields[0].type_name == "int *"
        assert s.fields[0].byte_size == 8

    def test_const_member(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)  # id=1
        b.add_type("", CTF_K_CONST, 0, 1)  # id=2, const int
        m_name = b.add_string("c")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "const int"

    def test_volatile_member(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_VOLATILE, 0, 1)
        m_name = b.add_string("v")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "volatile int"

    def test_restrict_member(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_POINTER, 0, 1)  # ptr to int
        b.add_type("", CTF_K_RESTRICT, 0, 2)  # restrict ptr
        m_name = b.add_string("r")
        members = struct.pack("<II", m_name, (3 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 8, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "restrict int *"

    def test_typedef_member(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("size_t", CTF_K_TYPEDEF, 0, 1)  # typedef int size_t
        m_name = b.add_string("sz")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "size_t"
        # typedef size should resolve through to int
        assert meta.structs["s"].fields[0].byte_size == 4

    def test_float_member(self) -> None:
        b = CtfBuilder()
        float_enc = struct.pack("<I", 64)  # 64 bits
        b.add_type("double", CTF_K_FLOAT, 0, 8, extra=float_enc)
        m_name = b.add_string("d")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 8, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "double"
        assert meta.structs["s"].fields[0].byte_size == 8

    def test_array_member(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        # v3 array: contents(4) + index(4) + nelems(4)
        array_extra = struct.pack("<III", 1, 1, 5)
        b.add_type("", CTF_K_ARRAY, 0, 0, extra=array_extra)
        m_name = b.add_string("arr")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 20, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "int[5]"
        assert meta.structs["s"].fields[0].byte_size == 20

    def test_forward_member(self) -> None:
        b = CtfBuilder()
        b.add_type("fwd_struct", CTF_K_FORWARD, 0, 0)
        m_name = b.add_string("fwd")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 0, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "fwd_struct"

    def test_function_member(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        # function type: returns int, 0 params
        b.add_type("", CTF_K_FUNCTION, 0, 1, extra=b"")
        m_name = b.add_string("fn")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 8, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "int(...)"

    def test_void_member(self) -> None:
        b = CtfBuilder()
        # Pointer to void (type 0)
        b.add_type("", CTF_K_POINTER, 0, 0)
        m_name = b.add_string("p")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 8, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "void *"

    def test_anon_enum_name(self) -> None:
        b = CtfBuilder()
        b.add_type("", CTF_K_ENUM, 0, 4, extra=b"")
        m_name = b.add_string("e")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "<anon enum>"

    def test_anon_struct_name(self) -> None:
        b = CtfBuilder()
        b.add_type("", CTF_K_STRUCT, 0, 4, extra=b"")
        m_name = b.add_string("inner")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("outer", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["outer"].fields[0].type_name == "<anon struct>"

    def test_anon_union_name(self) -> None:
        b = CtfBuilder()
        b.add_type("", CTF_K_UNION, 0, 4, extra=b"")
        m_name = b.add_string("u")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "<anon union>"

    def test_anon_fwd_name(self) -> None:
        b = CtfBuilder()
        b.add_type("", CTF_K_FORWARD, 0, 0)
        m_name = b.add_string("f")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 0, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "<fwd>"

    def test_anon_int_name(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("", CTF_K_INTEGER, 0, 4, extra=int_enc)
        m_name = b.add_string("x")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "int"

    def test_anon_float_name(self) -> None:
        b = CtfBuilder()
        float_enc = struct.pack("<I", 32)
        b.add_type("", CTF_K_FLOAT, 0, 4, extra=float_enc)
        m_name = b.add_string("f")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "float"

    def test_anon_typedef_resolves(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_TYPEDEF, 0, 1)  # anon typedef -> resolves to target
        m_name = b.add_string("x")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].type_name == "int"

    def test_unknown_type_id(self) -> None:
        b = CtfBuilder()
        # Struct member references non-existent type id 99
        m_name = b.add_string("x")
        members = struct.pack("<II", m_name, (99 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 0, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert "<ctf:" in meta.structs["s"].fields[0].type_name

    def test_unknown_kind(self) -> None:
        b = CtfBuilder()
        # Add a type with unrecognized kind (30)
        name_off = 0
        info = (30 << 24) | 0
        entry = struct.pack("<III", name_off, info, 0)
        b._type_entries.append(entry)
        # Struct referencing it
        m_name = b.add_string("x")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 0, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert "<ctf_kind_30:" in meta.structs["s"].fields[0].type_name

    def test_resolver_size_void(self) -> None:
        """Void type returns size 0."""
        b = CtfBuilder()
        m_name = b.add_string("x")
        members = struct.pack("<II", m_name, (0 << 16) | 0)  # type=0 (void)
        b.add_type("s", CTF_K_STRUCT, 1, 0, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].byte_size == 0

    def test_resolver_size_pointer(self) -> None:
        """Pointer size is 8 (64-bit assumed)."""
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_POINTER, 0, 1)  # ptr to int
        m_name = b.add_string("p")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 8, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].byte_size == 8

    def test_resolver_size_const(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        b.add_type("", CTF_K_CONST, 0, 1)
        m_name = b.add_string("c")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].byte_size == 4

    def test_resolver_size_array(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        array_extra = struct.pack("<III", 1, 1, 3)  # 3 ints
        b.add_type("", CTF_K_ARRAY, 0, 0, extra=array_extra)
        m_name = b.add_string("a")
        members = struct.pack("<II", m_name, (2 << 16) | 0)
        b.add_type("s", CTF_K_STRUCT, 1, 12, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].byte_size == 12


# ---------------------------------------------------------------------------
# CTF v2 format coverage (v2 builder + direct parser tests)
# ---------------------------------------------------------------------------


class CtfV2Builder:
    """Helper to construct synthetic CTF *v2* binary blobs for testing."""

    def __init__(self) -> None:
        self._strings = bytearray(b"\x00")
        self._type_entries: list[bytes] = []
        self._str_offsets: dict[str, int] = {"": 0}

    def add_string(self, s: str) -> int:
        if s in self._str_offsets:
            return self._str_offsets[s]
        off = len(self._strings)
        self._strings.extend(s.encode("utf-8") + b"\x00")
        self._str_offsets[s] = off
        return off

    def add_type(
        self,
        name: str,
        kind: int,
        vlen: int,
        size_or_type: int,
        extra: bytes = b"",
        isroot: bool = False,
    ) -> int:
        name_off = self.add_string(name) if name else 0
        # v2 info: kind(5) << 11 | isroot << 10 | vlen(10)
        info = (kind << 11) | (int(isroot) << 10) | (vlen & 0x3FF)
        entry = struct.pack("<I", name_off) + struct.pack("<H", info)
        if (
            kind in (CTF_K_STRUCT, CTF_K_UNION)
            and size_or_type >= _CTF_V2_LSTRUCT_THRESH
        ):
            # short marker >= threshold, followed by real 4-byte size
            entry += struct.pack("<H", _CTF_V2_LSTRUCT_THRESH) + struct.pack(
                "<I", size_or_type
            )
        else:
            entry += struct.pack("<H", size_or_type)
        entry += extra
        self._type_entries.append(entry)
        return len(self._type_entries)

    def build(self) -> bytes:
        type_data = b"".join(self._type_entries)
        str_data = bytes(self._strings)
        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_2, 0)
        header += struct.pack(
            "<IIIIIIII", 0, 0, 0, 0, 0, 0, len(type_data), len(str_data)
        )
        return header + type_data + str_data


class TestCtfV2Parsing:
    def test_v2_integer(self) -> None:
        b = CtfV2Builder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf
        assert meta.type_count == 1

    def test_v2_small_struct(self) -> None:
        b = CtfV2Builder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        m_name = b.add_string("x")
        # v2 small member: name(2) + off_val(2); type in bits 10-15, offset bits 0-9
        m_off_val = (1 << 10) | 0  # type=1, offset=0
        members = struct.pack("<HH", m_name, m_off_val)
        b.add_type("S", CTF_K_STRUCT, 1, 4, extra=members)
        meta = parse_ctf_from_bytes(b.build())
        assert "S" in meta.structs
        assert meta.structs["S"].fields[0].name == "x"

    def test_v2_large_struct(self) -> None:
        b = CtfV2Builder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        m_name = b.add_string("y")
        # v2 large member: name(2) + type(2) + off_hi(2) + off_lo(2)
        members = struct.pack("<HHHH", m_name, 1, 0, 0)
        b.add_type("Big", CTF_K_STRUCT, 1, 0x2000, extra=members)
        meta = parse_ctf_from_bytes(b.build())
        assert "Big" in meta.structs
        assert meta.structs["Big"].fields[0].name == "y"

    def test_v2_array_name_and_size(self) -> None:
        b = CtfV2Builder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        # v2 array extra: contents(2) + index(2) + nelems(2)
        array_extra = struct.pack("<HHH", 1, 1, 4)
        b.add_type("", CTF_K_ARRAY, 0, 0, extra=array_extra)
        m_name = b.add_string("arr")
        members = struct.pack("<HH", m_name, (2 << 10) | 0)  # type=2 (array)
        b.add_type("Holder", CTF_K_STRUCT, 1, 16, extra=members)
        meta = parse_ctf_from_bytes(b.build())
        fld = meta.structs["Holder"].fields[0]
        assert fld.type_name == "int[4]"
        assert fld.byte_size == 16  # 4 ints * 4 bytes


class TestParseTypesTruncation:
    def test_v3_truncated_header_breaks(self) -> None:
        # Fewer than 12 bytes → loop breaks immediately.
        assert _parse_types(b"\x00" * 8, CTF_VERSION_3) == [
            CtfType(type_id=0, name_off=0, info=0, size_or_type=0)
        ]

    def test_v2_truncated_header_breaks(self) -> None:
        # Fewer than 6 bytes → loop breaks immediately.
        result = _parse_types(b"\x00" * 4, CTF_VERSION_2)
        assert len(result) == 1  # only the null type

    def test_v2_truncated_size_field_breaks(self) -> None:
        # name(4) + info(2) present, but no room for size_or_type short.
        data = struct.pack("<I", 0) + struct.pack("<H", (CTF_K_POINTER << 11))
        result = _parse_types(data, CTF_VERSION_2)
        assert len(result) == 1

    def test_v3_truncated_extra_breaks(self) -> None:
        # A v3 integer claims 4 bytes of extra but none are present.
        info = CTF_K_INTEGER << 24
        data = struct.pack("<III", 0, info, 4)  # no extra encoding word follows
        result = _parse_types(data, CTF_VERSION_3)
        assert len(result) == 1  # type truncated → skipped


class TestResolverEdgeCases:
    def test_integer_missing_encoding_size_zero(self) -> None:

        # Integer type with no extra encoding word → size resolves to 0.
        t = CtfType(
            type_id=1, name_off=0, info=(CTF_K_INTEGER << 24), size_or_type=4, extra=b""
        )
        resolver = _TypeResolver([CtfType(0, 0, 0, 0), t], b"\x00", CTF_VERSION_3)
        assert resolver.size(1) == 0

    def test_float_missing_encoding_size_zero(self) -> None:

        t = CtfType(
            type_id=1, name_off=0, info=(CTF_K_FLOAT << 24), size_or_type=4, extra=b""
        )
        resolver = _TypeResolver([CtfType(0, 0, 0, 0), t], b"\x00", CTF_VERSION_3)
        assert resolver.size(1) == 0

    def test_array_v2_short_extra_size_zero(self) -> None:

        # v2 array with too-short extra → size guard returns 0.
        t = CtfType(
            type_id=1, name_off=0, info=(CTF_K_ARRAY << 24), size_or_type=0, extra=b""
        )
        resolver = _TypeResolver([CtfType(0, 0, 0, 0), t], b"\x00", CTF_VERSION_2)
        assert resolver.size(1) == 0

    def test_cyclic_name_resolution(self) -> None:
        # Build a typedef that points to itself → name resolution must not recurse forever.

        # type 1 is a typedef whose target is itself (id 1).
        t1 = CtfType(type_id=1, name_off=0, info=(CTF_K_TYPEDEF << 24), size_or_type=1)
        resolver = _TypeResolver([CtfType(0, 0, 0, 0), t1], b"\x00", CTF_VERSION_3)
        # Recursion guard returns "..." when re-entered.
        assert resolver.name(1) is not None

    def test_cyclic_size_resolution(self) -> None:

        t1 = CtfType(type_id=1, name_off=0, info=(CTF_K_TYPEDEF << 24), size_or_type=1)
        resolver = _TypeResolver([CtfType(0, 0, 0, 0), t1], b"\x00", CTF_VERSION_3)
        assert resolver.size(1) == 0  # guard breaks the cycle

    def test_array_unparseable_name_fallback(self) -> None:

        # Array with too-short extra → name falls back to "[]".
        arr = CtfType(
            type_id=1, name_off=0, info=(CTF_K_ARRAY << 24), size_or_type=0, extra=b""
        )
        resolver = _TypeResolver([CtfType(0, 0, 0, 0), arr], b"\x00", CTF_VERSION_3)
        assert resolver._resolve_name_array(arr) == "[]"


class TestEnumTruncation:
    def test_enum_truncated_members(self) -> None:

        # vlen claims 2 entries but only one (8 bytes) of extra is provided,
        # so the member loop breaks early on the second iteration.
        # str_data: offset 1 = "E" (type name), offset 3 = "A" (member name).
        str_data = b"\x00E\x00A\x00"
        entries = struct.pack("<Ii", 3, 0)  # one entry: name_off=3 ("A"), value=0
        t = CtfType(
            type_id=1,
            name_off=1,
            info=(CTF_K_ENUM << 24) | 2,
            size_or_type=4,
            extra=entries,
        )
        enums = _extract_enums([CtfType(0, 0, 0, 0), t], str_data)
        assert enums["E"].members == {"A": 0}


class TestReadCtfSection:
    def test_reads_dot_ctf(self, tmp_path) -> None:
        f = tmp_path / "libfoo.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 60)
        section = MagicMock()
        section.data.return_value = b"CTFDATA"
        elf = MagicMock()
        elf.get_section_by_name.side_effect = lambda n: section if n == ".ctf" else None
        with patch("elftools.elf.elffile.ELFFile", return_value=elf):
            assert _read_ctf_section(f) == b"CTFDATA"

    def test_reads_sunw_ctf(self, tmp_path) -> None:
        f = tmp_path / "libfoo.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 60)
        section = MagicMock()
        section.data.return_value = b"SUNW"
        elf = MagicMock()
        elf.get_section_by_name.side_effect = lambda n: (
            section if n == ".SUNW_ctf" else None
        )
        with patch("elftools.elf.elffile.ELFFile", return_value=elf):
            assert _read_ctf_section(f) == b"SUNW"

    def test_no_ctf_section_returns_none(self, tmp_path) -> None:
        f = tmp_path / "libfoo.so"
        f.write_bytes(b"\x7fELF" + b"\x00" * 60)
        elf = MagicMock()
        elf.get_section_by_name.return_value = None
        with patch("elftools.elf.elffile.ELFFile", return_value=elf):
            assert _read_ctf_section(f) is None


class TestParseCtfMetadata:
    def test_read_error_returns_empty(self, tmp_path) -> None:
        f = tmp_path / "libfoo.so"
        f.write_bytes(b"\x7fELF")
        with patch(
            "abicheck.ctf_metadata._read_ctf_section", side_effect=OSError("boom")
        ):
            meta = parse_ctf_metadata(f)
        assert not meta.has_ctf

    def test_no_section_returns_empty(self, tmp_path) -> None:
        f = tmp_path / "libfoo.so"
        f.write_bytes(b"\x7fELF")
        with patch("abicheck.ctf_metadata._read_ctf_section", return_value=None):
            meta = parse_ctf_metadata(f)
        assert not meta.has_ctf

    def test_success_delegates_to_parse_bytes(self, tmp_path) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        f = tmp_path / "libfoo.so"
        f.write_bytes(b"\x7fELF")
        with patch("abicheck.ctf_metadata._read_ctf_section", return_value=b.build()):
            meta = parse_ctf_metadata(f)
        assert meta.has_ctf


class TestParseCtfFromBytesErrorPaths:
    def test_decompression_failure_returns_empty(self) -> None:
        # Compress flag set but body is not valid zlib data.
        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, CTF_F_COMPRESS)
        header += struct.pack("<IIIIIIII", 0, 0, 0, 0, 0, 0, 0, 0)
        data = header + b"not zlib compressed garbage"
        meta = parse_ctf_from_bytes(data)
        assert not meta.has_ctf

    def test_section_bounds_exceed_data(self) -> None:
        # str_off/str_len point past the end of the data.
        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, 0)
        header += struct.pack("<IIIIIIII", 0, 0, 0, 0, 0, 0, 1000, 1000)
        meta = parse_ctf_from_bytes(header)
        assert not meta.has_ctf

    def test_struct_extraction_failure_is_caught(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        data = b.build()
        with patch(
            "abicheck.ctf_metadata._extract_structs", side_effect=RuntimeError("boom")
        ):
            meta = parse_ctf_from_bytes(data)
        # Header still parsed; struct extraction error swallowed.
        assert meta.has_ctf
        assert meta.structs == {}

    def test_enum_extraction_failure_is_caught(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        data = b.build()
        with patch(
            "abicheck.ctf_metadata._extract_enums", side_effect=RuntimeError("boom")
        ):
            meta = parse_ctf_from_bytes(data)
        assert meta.has_ctf
        assert meta.enums == {}

    def test_typedef_extraction_failure_is_caught(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        data = b.build()
        with patch(
            "abicheck.ctf_metadata._extract_typedefs", side_effect=RuntimeError("boom")
        ):
            meta = parse_ctf_from_bytes(data)
        assert meta.has_ctf
        assert meta.typedefs == {}

    def test_type_parsing_failure_returns_empty(self) -> None:
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        data = b.build()
        with patch(
            "abicheck.ctf_metadata._parse_types", side_effect=struct.error("boom")
        ):
            meta = parse_ctf_from_bytes(data)
        assert not meta.has_ctf
