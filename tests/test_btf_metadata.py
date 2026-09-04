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

"""Tests for BTF (BPF Type Format) parser."""

from __future__ import annotations

import struct

import pytest

from abicheck.btf_metadata import (
    BTF_KIND_ARRAY,
    BTF_KIND_CONST,
    BTF_KIND_DATASEC,
    BTF_KIND_DECL_TAG,
    BTF_KIND_ENUM,
    BTF_KIND_ENUM64,
    BTF_KIND_FLOAT,
    BTF_KIND_FUNC,
    BTF_KIND_FUNC_PROTO,
    BTF_KIND_FWD,
    BTF_KIND_INT,
    BTF_KIND_PTR,
    BTF_KIND_RESTRICT,
    BTF_KIND_STRUCT,
    BTF_KIND_TYPE_TAG,
    BTF_KIND_TYPEDEF,
    BTF_KIND_UNION,
    BTF_KIND_VAR,
    BTF_KIND_VOLATILE,
    BTF_MAGIC,
    BTF_VERSION,
    BtfMetadata,
    BtfType,
    _extra_data_size,
    _parse_header,
    _parse_types,
    _read_btf_section,
    _read_string,
    _TypeResolver,
    parse_btf_from_bytes,
    parse_btf_metadata,
)

# ---------------------------------------------------------------------------
# Helpers to build synthetic BTF blobs
# ---------------------------------------------------------------------------


class BtfBuilder:
    """Helper to construct synthetic BTF binary blobs for testing."""

    def __init__(self) -> None:
        self._strings = bytearray(b"\x00")  # string section starts with NUL
        self._type_entries: list[bytes] = []
        self._str_offsets: dict[str, int] = {"": 0}

    def add_string(self, s: str) -> int:
        """Add a string to the string table, return its offset."""
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
        kflag: int = 0,
    ) -> int:
        """Add a type entry, return its 1-based type ID."""
        name_off = self.add_string(name) if name else 0
        info = (kflag << 31) | (kind << 24) | (vlen & 0xFFFF)
        entry = struct.pack("<III", name_off, info, size_or_type) + extra
        self._type_entries.append(entry)
        return len(self._type_entries)  # 1-based

    def build(self) -> bytes:
        """Build a complete BTF blob with header + types + strings."""
        type_data = b"".join(self._type_entries)
        str_data = bytes(self._strings)
        hdr_len = 24
        type_off = 0
        type_len = len(type_data)
        str_off = type_len
        str_len = len(str_data)
        header = struct.pack(
            "<HBBIIIII",
            BTF_MAGIC,
            BTF_VERSION,
            0,
            hdr_len,
            type_off,
            type_len,
            str_off,
            str_len,
        )
        return header + type_data + str_data


# ---------------------------------------------------------------------------
# String table
# ---------------------------------------------------------------------------


class TestReadString:
    def test_basic(self) -> None:
        data = b"hello\x00world\x00"
        assert _read_string(data, 0) == ("hello", True)
        assert _read_string(data, 6) == ("world", True)

    def test_empty(self) -> None:
        """A NUL right at the offset is a legitimate zero-length name --
        valid=True, unlike an out-of-bounds offset below."""
        assert _read_string(b"\x00", 0) == ("", True)

    def test_out_of_bounds(self) -> None:
        """P2 review: an out-of-bounds offset must be distinguishable from
        a legitimate empty name -- valid=False."""
        assert _read_string(b"abc\x00", 100) == ("", False)
        assert _read_string(b"abc\x00", -1) == ("", False)


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------


class TestParseHeader:
    def test_valid_header(self) -> None:
        b = BtfBuilder()
        data = b.build()
        hdr = _parse_header(data)
        assert hdr.magic == BTF_MAGIC
        assert hdr.version == BTF_VERSION
        assert hdr.hdr_len == 24

    def test_bad_magic(self) -> None:
        data = struct.pack("<HBBI", 0xDEAD, 1, 0, 24) + b"\x00" * 16
        with pytest.raises(ValueError, match="Bad BTF magic"):
            _parse_header(data)

    def test_too_small(self) -> None:
        with pytest.raises(ValueError, match="too small"):
            _parse_header(b"\x00" * 10)


# ---------------------------------------------------------------------------
# Type parsing
# ---------------------------------------------------------------------------


class TestParseTypes:
    def test_empty_section(self) -> None:
        types = _parse_types(b"")
        assert len(types) == 1  # only void sentinel
        assert types[0].type_id == 0

    def test_int_type(self) -> None:
        b = BtfBuilder()
        # INT encoding: bits=32, offset=0, signed
        int_enc = struct.pack("<I", (0 << 16) | (0 << 8) | 32)  # 32 bits
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        data = b.build()
        # Parse just the type section
        hdr = _parse_header(data)
        type_start = hdr.hdr_len + hdr.type_off
        type_end = type_start + hdr.type_len
        types = _parse_types(data[type_start:type_end])
        assert len(types) == 2  # void + int
        assert types[1].kind == BTF_KIND_INT

    def test_empty_section_is_not_truncated(self) -> None:
        """A zero-length type section is a legitimately empty (not
        truncated) parse -- `truncated` must not be set."""
        truncated: list[bool] = []
        types = _parse_types(b"", truncated)
        assert len(types) == 1
        assert truncated == []

    def test_extra_data_cut_off_mid_entry_sets_truncated(self) -> None:
        """P2 review, fresh evidence: a type whose fixed 12-byte header is
        present but whose kind-specific extra data is cut off must not
        raise -- it logs and returns every type parsed before the cut, and
        the opt-in `truncated` out-param must record the incompleteness."""
        # A BTF_KIND_INT header claiming 4 bytes of extra encoding data, but
        # with zero bytes of extra data actually following it.
        info = BTF_KIND_INT << 24
        header_only = struct.pack("<III", 0, info, 4)
        truncated: list[bool] = []
        types = _parse_types(header_only, truncated)
        assert len(types) == 1  # only the void sentinel -- the INT was cut
        assert truncated == [True]

    def test_trailing_bytes_too_short_for_next_header_sets_truncated(self) -> None:
        """A well-formed final type followed by fewer than 12 trailing
        bytes -- not enough for even the next entry's fixed header -- is
        also a truncation, via the outer post-loop check rather than the
        inner extra-data one."""
        int_enc = struct.pack("<I", 32)
        info = BTF_KIND_INT << 24
        one_type = struct.pack("<III", 0, info, 4) + int_enc
        truncated: list[bool] = []
        types = _parse_types(one_type + b"\x00\x00\x00", truncated)
        assert len(types) == 2  # void + the one complete INT
        assert truncated == [True]

    def test_fully_consumed_section_is_not_truncated(self) -> None:
        """Sanity check: a type section with no leftover bytes at all must
        not be flagged, positive control for the two truncation tests
        above."""
        int_enc = struct.pack("<I", 32)
        info = BTF_KIND_INT << 24
        one_type = struct.pack("<III", 0, info, 4) + int_enc
        truncated: list[bool] = []
        types = _parse_types(one_type, truncated)
        assert len(types) == 2
        assert truncated == []


# ---------------------------------------------------------------------------
# Full parse: structs
# ---------------------------------------------------------------------------


class TestBtfStructs:
    def test_simple_struct(self) -> None:
        b = BtfBuilder()
        # Add INT type (id=1)
        int_enc = struct.pack("<I", 32)  # 32 bits
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)

        # Add struct with 2 members (id=2)
        # Member: name_off(4) + type(4) + offset(4)
        m1_name = b.add_string("x")
        m2_name = b.add_string("y")
        members = struct.pack("<III", m1_name, 1, 0)  # x: int at offset 0
        members += struct.pack("<III", m2_name, 1, 32)  # y: int at offset 32 bits
        b.add_type("point", BTF_KIND_STRUCT, 2, 8, extra=members)

        meta = parse_btf_from_bytes(b.build())
        assert meta.has_btf
        assert "point" in meta.structs

        s = meta.structs["point"]
        assert s.name == "point"
        assert s.byte_size == 8
        assert len(s.fields) == 2
        assert s.fields[0].name == "x"
        assert s.fields[0].byte_offset == 0
        assert s.fields[0].type_name == "int"
        assert s.fields[1].name == "y"
        assert s.fields[1].byte_offset == 4

    def test_int_member_uses_declared_storage_size_not_bit_width(self) -> None:
        """P2 review, fresh evidence (Codex): BTF_KIND_INT's size_or_type
        is the type's real storage size in bytes; the encoding word's own
        nr_bits (bits 0-7) is the *occupied bit width within that
        storage*, not the storage size -- a narrower value can legitimately
        sit in wider storage (e.g. an 8-bit value in 4-byte storage).
        Reproduces the exact finding scenario: size_or_type=4 (4-byte
        storage), nr_bits=8 (encoded via offset=8, width=8 to also
        exercise the offset field) -- byte_size must follow the declared
        storage size (4), not (nr_bits + 7) // 8 (which would wrongly
        report 1)."""
        b = BtfBuilder()
        # encoding word: bits 0-7 = nr_bits(8), bits 16-23 = offset(8)
        int_enc = struct.pack("<I", 8 | (8 << 16))
        b.add_type("narrow_int", BTF_KIND_INT, 0, 4, extra=int_enc)

        m_name = b.add_string("v")
        members = struct.pack("<III", m_name, 1, 0)
        b.add_type("s", BTF_KIND_STRUCT, 1, 4, extra=members)

        meta = parse_btf_from_bytes(b.build())
        assert meta.structs["s"].fields[0].byte_size == 4
        assert meta.extraction_partial is False

    def test_union(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)

        m1_name = b.add_string("i")
        m2_name = b.add_string("f")
        members = struct.pack("<III", m1_name, 1, 0)
        members += struct.pack("<III", m2_name, 1, 0)
        b.add_type("my_union", BTF_KIND_UNION, 2, 4, extra=members)

        meta = parse_btf_from_bytes(b.build())
        assert "my_union" in meta.structs
        assert meta.structs["my_union"].is_union

    def test_bitfield_struct(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("unsigned int", BTF_KIND_INT, 0, 4, extra=int_enc)

        # kflag=1: offset encodes bitfield_size in bits 24-31
        m1_name = b.add_string("a")
        m2_name = b.add_string("b")
        # a: 3 bits at bit 0, b: 5 bits at bit 3
        m1_offset = (3 << 24) | 0  # bitfield_size=3, bit_offset=0
        m2_offset = (5 << 24) | 3  # bitfield_size=5, bit_offset=3
        members = struct.pack("<III", m1_name, 1, m1_offset)
        members += struct.pack("<III", m2_name, 1, m2_offset)
        b.add_type("flags", BTF_KIND_STRUCT, 2, 4, extra=members, kflag=1)

        meta = parse_btf_from_bytes(b.build())
        s = meta.structs["flags"]
        assert s.fields[0].bit_size == 3
        assert s.fields[1].bit_size == 5

    def test_anonymous_struct_skipped(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        # Struct with empty name (anonymous)
        b.add_type("", BTF_KIND_STRUCT, 0, 0, extra=b"")

        meta = parse_btf_from_bytes(b.build())
        assert len(meta.structs) == 0


# ---------------------------------------------------------------------------
# Full parse: enums
# ---------------------------------------------------------------------------


class TestBtfEnums:
    def test_simple_enum(self) -> None:
        b = BtfBuilder()
        e1_name = b.add_string("RED")
        e2_name = b.add_string("GREEN")
        e3_name = b.add_string("BLUE")
        entries = struct.pack("<Ii", e1_name, 0)
        entries += struct.pack("<Ii", e2_name, 1)
        entries += struct.pack("<Ii", e3_name, 2)
        b.add_type("color", BTF_KIND_ENUM, 3, 4, extra=entries)

        meta = parse_btf_from_bytes(b.build())
        assert "color" in meta.enums
        e = meta.enums["color"]
        assert e.underlying_byte_size == 4
        assert e.members == {"RED": 0, "GREEN": 1, "BLUE": 2}

    def test_enum64(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("BIG_VAL")
        # ENUM64: name_off(4) + val_lo(4) + val_hi(4)
        entries = struct.pack("<III", e_name, 0xDEADBEEF, 0x1)
        b.add_type("big_enum", BTF_KIND_ENUM64, 1, 8, extra=entries)

        meta = parse_btf_from_bytes(b.build())
        assert "big_enum" in meta.enums
        e = meta.enums["big_enum"]
        assert e.members["BIG_VAL"] == 0x1DEADBEEF

    def test_enum64_signed(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("NEG64")
        # -1 as unsigned 64-bit: lo=0xFFFFFFFF, hi=0xFFFFFFFF
        entries = struct.pack("<III", e_name, 0xFFFFFFFF, 0xFFFFFFFF)
        b.add_type("signed64", BTF_KIND_ENUM64, 1, 8, extra=entries, kflag=1)

        meta = parse_btf_from_bytes(b.build())
        assert meta.enums["signed64"].members["NEG64"] == -1

    def test_negative_enum_values(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("NEG")
        entries = struct.pack("<Ii", e_name, -1)
        # kflag=1 marks enumerators as signed
        b.add_type("signed_enum", BTF_KIND_ENUM, 1, 4, extra=entries, kflag=1)

        meta = parse_btf_from_bytes(b.build())
        assert meta.enums["signed_enum"].members["NEG"] == -1

    def test_unsigned_enum_values(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("BIG")
        entries = struct.pack("<II", e_name, 0xFFFFFFFF)
        # kflag=0 (default) → unsigned
        b.add_type("unsigned_enum", BTF_KIND_ENUM, 1, 4, extra=entries)

        meta = parse_btf_from_bytes(b.build())
        assert meta.enums["unsigned_enum"].members["BIG"] == 0xFFFFFFFF


# ---------------------------------------------------------------------------
# Full parse: function prototypes
# ---------------------------------------------------------------------------


class TestBtfFuncProtos:
    def test_function_proto(self) -> None:
        b = BtfBuilder()
        # Add int (id=1)
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)

        # Add FUNC_PROTO: int(int, int) (id=2)
        p1_name = b.add_string("a")
        p2_name = b.add_string("b")
        params = struct.pack("<II", p1_name, 1)  # param a: int
        params += struct.pack("<II", p2_name, 1)  # param b: int
        b.add_type(
            "", BTF_KIND_FUNC_PROTO, 2, 1, extra=params
        )  # returns int (type_id=1)

        # Add FUNC pointing to FUNC_PROTO (id=3)
        b.add_type("add", BTF_KIND_FUNC, 0, 2)  # size_or_type = proto type_id

        meta = parse_btf_from_bytes(b.build())
        assert "add" in meta.func_protos
        fp = meta.func_protos["add"]
        assert fp.return_type == "int"
        assert len(fp.params) == 2
        assert fp.params[0] == ("a", "int")
        assert fp.params[1] == ("b", "int")
        # Positive control (P2 review, fresh evidence, Codex): a fully
        # resolvable function must not be marked partial -- proving the
        # fix below doesn't disturb the ordinary resolvable-prototype path.
        assert meta.extraction_partial is False


# ---------------------------------------------------------------------------
# Full parse: typedefs
# ---------------------------------------------------------------------------


class TestBtfTypedefs:
    def test_typedef(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("myint", BTF_KIND_TYPEDEF, 0, 1)  # typedef int myint

        meta = parse_btf_from_bytes(b.build())
        assert meta.typedefs.get("myint") == "int"


# ---------------------------------------------------------------------------
# Type resolution
# ---------------------------------------------------------------------------


class TestTypeResolver:
    def test_pointer_name(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_PTR, 0, 1)  # ptr to int

        data = b.build()
        hdr = _parse_header(data)
        type_start = hdr.hdr_len + hdr.type_off
        str_start = hdr.hdr_len + hdr.str_off
        str_end = str_start + hdr.str_len
        types = _parse_types(data[type_start : type_start + hdr.type_len])
        resolver = _TypeResolver(types, data[str_start:str_end])
        assert resolver.name(2) == "int *"
        assert resolver.size(2) == 8

    def test_pointer_size_can_be_32_bit(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_PTR, 0, 1)

        data = b.build()
        hdr = _parse_header(data)
        type_start = hdr.hdr_len + hdr.type_off
        str_start = hdr.hdr_len + hdr.str_off
        str_end = str_start + hdr.str_len
        types = _parse_types(data[type_start : type_start + hdr.type_len])
        resolver = _TypeResolver(types, data[str_start:str_end], pointer_size=4)
        assert resolver.name(2) == "int *"
        assert resolver.size(2) == 4

    def test_const_name(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_CONST, 0, 1)  # const int

        data = b.build()
        hdr = _parse_header(data)
        type_start = hdr.hdr_len + hdr.type_off
        str_start = hdr.hdr_len + hdr.str_off
        str_end = str_start + hdr.str_len
        types = _parse_types(data[type_start : type_start + hdr.type_len])
        resolver = _TypeResolver(types, data[str_start:str_end])
        assert resolver.name(2) == "const int"

    def test_void(self) -> None:
        b = BtfBuilder()
        data = b.build()
        hdr = _parse_header(data)
        types = _parse_types(b"")
        resolver = _TypeResolver(types, data[hdr.hdr_len + hdr.str_off :])
        assert resolver.name(0) == "void"
        assert resolver.size(0) == 0


# ---------------------------------------------------------------------------
# to_dwarf_metadata conversion
# ---------------------------------------------------------------------------


class TestToDwarfMetadata:
    def test_conversion(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        m_name = b.add_string("val")
        members = struct.pack("<III", m_name, 1, 0)
        b.add_type("simple", BTF_KIND_STRUCT, 1, 4, extra=members)

        meta = parse_btf_from_bytes(b.build())
        dwarf = meta.to_dwarf_metadata()

        assert dwarf.has_dwarf  # maps to has_btf
        assert "simple" in dwarf.structs
        assert dwarf.structs["simple"].name == "simple"
        assert dwarf.evidence_state == "parsed"
        assert meta.extraction_partial is False

    def test_struct_extraction_failure_propagates_to_partial(self, monkeypatch) -> None:
        """P2 review: parse_btf_from_bytes catches a struct-extraction
        exception internally and returns a partially-populated object, but
        must not silently claim "parsed" basic layout evidence for it."""
        from abicheck import btf_metadata as btf_mod

        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)

        def boom(*_a, **_k):
            raise RuntimeError("bad struct record")

        monkeypatch.setattr(btf_mod, "_extract_structs", boom)

        meta = parse_btf_from_bytes(b.build())
        assert meta.has_btf is True
        assert meta.extraction_partial is True

        dwarf = meta.to_dwarf_metadata()
        assert dwarf.has_dwarf is True
        assert dwarf.evidence_state == "partial"

    def test_truncated_type_section_propagates_to_partial(self, monkeypatch) -> None:
        """P2 review, fresh evidence: `_parse_types()` silently truncating a
        type record (log + return, never raise) must still mark the
        receipt `partial`, not `parsed` -- the earlier fix only covered a
        raised-and-caught extraction-stage exception, not a truncation the
        type parser itself swallows before extraction even runs. Exercises
        the real `parse_btf_from_bytes` -> `_parse_types` wiring, with
        `_parse_types` itself only stubbed to report the same "truncated"
        signal the real function's own unit tests (TestParseTypes) already
        prove it produces for a genuinely cut-off buffer."""
        from abicheck import btf_metadata as btf_mod

        real_parse_types = btf_mod._parse_types

        def wrapped(type_data: bytes, truncated=None):
            result = real_parse_types(type_data)
            if truncated is not None:
                truncated.append(True)
            return result

        monkeypatch.setattr(btf_mod, "_parse_types", wrapped)

        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)

        meta = parse_btf_from_bytes(b.build())
        assert meta.has_btf is True
        assert meta.extraction_partial is True
        assert meta.to_dwarf_metadata().evidence_state == "partial"

    def test_enum_only_extraction_failure_still_marks_partial(
        self, monkeypatch
    ) -> None:
        """Even a failure in a stage to_dwarf_metadata() doesn't itself
        transfer (enums/func_protos/typedefs) must still downgrade the
        converted basic-layout receipt -- an enum/func_proto/typedef bug
        is just as much evidence the overall BTF parse was NOT fully
        trustworthy, even though only structs/enums cross into
        DwarfMetadata."""
        from abicheck import btf_metadata as btf_mod

        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)

        def boom(*_a, **_k):
            raise RuntimeError("bad func_proto record")

        monkeypatch.setattr(btf_mod, "_extract_func_protos", boom)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.to_dwarf_metadata().evidence_state == "partial"


class TestBtfStringTableSentinel:
    """P2 review, fresh evidence (Codex): offset 0 in the BTF string
    section is reserved by include/uapi/linux/btf.h -- it must always be
    a single NUL byte, the empty-string sentinel every anonymous
    (name_off=0) reference relies on. read_null_terminated_string() only
    flags an out-of-bounds offset or a missing terminator, so a string
    section that never actually stored that sentinel byte reads whatever
    bytes sit at offset 0 as a plausible-looking real name instead of the
    empty string it's supposed to mean -- fabricating or renaming a type
    while the parse still reports itself complete."""

    @staticmethod
    def _raw_btf(type_data: bytes, str_data: bytes) -> bytes:
        """Hand-assemble a BTF blob, bypassing BtfBuilder's own guaranteed-
        correct leading-NUL string section, to construct a deliberately
        corrupt one."""
        hdr_len = 24
        header = struct.pack(
            "<HBBIIIII",
            BTF_MAGIC,
            BTF_VERSION,
            0,
            hdr_len,
            0,
            len(type_data),
            len(type_data),
            len(str_data),
        )
        return header + type_data + str_data

    def test_missing_leading_nul_marks_partial(self) -> None:
        """Reproduces the exact fabricated-name scenario from the finding:
        a struct with name_off=0 against a string section that starts
        with a real character instead of NUL is accepted as a named
        struct "S" -- offset 0 is supposed to mean anonymous."""
        name_off = 0
        entry = struct.pack("<III", name_off, BTF_KIND_STRUCT << 24, 0)
        data = self._raw_btf(entry, b"S\x00")

        meta = parse_btf_from_bytes(data)
        assert meta.has_btf is True
        # The corrupted string table still lets a name be read at offset
        # 0 (proving the fabrication actually happens, not just that
        # parsing failed outright) -- fixing *that* misread is a separate,
        # larger change (this module has no way to know what the real
        # empty-string convention would have produced); this fix's whole
        # job is making sure it can no longer claim to be trustworthy.
        assert "S" in meta.structs
        assert meta.extraction_partial is True
        assert meta.to_dwarf_metadata().evidence_state == "partial"

    def test_empty_string_section_marks_partial(self) -> None:
        """Sibling corruption shape: no string section at all (not even
        the mandatory sentinel byte)."""
        entry = struct.pack("<III", 0, BTF_KIND_STRUCT << 24, 0)
        data = self._raw_btf(entry, b"")

        meta = parse_btf_from_bytes(data)
        assert meta.has_btf is True
        assert meta.extraction_partial is True

    def test_wellformed_leading_nul_is_not_flagged(self) -> None:
        """Positive control: a string section that begins with the
        mandatory NUL sentinel (BtfBuilder's own real output) must not be
        flagged -- proving the fix doesn't disturb the ordinary,
        well-formed parse path."""
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)

        meta = parse_btf_from_bytes(b.build())
        assert meta.has_btf is True
        assert meta.extraction_partial is False


# ---------------------------------------------------------------------------
# Error handling / graceful degradation
# ---------------------------------------------------------------------------


class TestBtfElfSection:
    def test_read_btf_section_returns_data_and_pointer_size(
        self, tmp_path, monkeypatch
    ) -> None:
        btf_data = b"btf"

        class FakeSection:
            def data(self) -> bytes:
                return btf_data

        class FakeElf:
            elfclass = 32

            def __init__(self, _file) -> None:
                pass

            def get_section_by_name(self, name: str):
                assert name == ".BTF"
                return FakeSection()

        import elftools.elf.elffile

        monkeypatch.setattr(elftools.elf.elffile, "ELFFile", FakeElf)
        elf_path = tmp_path / "lib.so"
        elf_path.write_bytes(b"not really elf")

        assert _read_btf_section(elf_path) == (btf_data, 4)

    def test_read_btf_section_returns_none_when_section_missing(
        self, tmp_path, monkeypatch
    ) -> None:
        class FakeElf:
            elfclass = 64

            def __init__(self, _file) -> None:
                pass

            def get_section_by_name(self, name: str):
                assert name == ".BTF"
                return None

        import elftools.elf.elffile

        monkeypatch.setattr(elftools.elf.elffile, "ELFFile", FakeElf)
        elf_path = tmp_path / "lib.so"
        elf_path.write_bytes(b"not really elf")

        assert _read_btf_section(elf_path) is None

    def test_parse_btf_metadata_returns_empty_when_section_missing(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            "abicheck.btf_metadata._read_btf_section", lambda _path: None
        )

        meta = parse_btf_metadata(tmp_path / "lib.so")

        assert not meta.has_btf

    def test_parse_btf_metadata_passes_pointer_size_from_elf(
        self, tmp_path, monkeypatch
    ) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_PTR, 0, 1)
        ptr_name = b.add_string("ptr")
        members = struct.pack("<III", ptr_name, 2, 0)
        b.add_type("box", BTF_KIND_STRUCT, 1, 4, extra=members)

        monkeypatch.setattr(
            "abicheck.btf_metadata._read_btf_section",
            lambda _path: (b.build(), 4),
        )

        meta = parse_btf_metadata(tmp_path / "lib32.so")

        assert meta.has_btf
        assert meta.structs["box"].fields[0].byte_size == 4


class TestBtfErrorHandling:
    def test_empty_data(self) -> None:
        meta = parse_btf_from_bytes(b"")
        assert not meta.has_btf

    def test_bad_magic(self) -> None:
        meta = parse_btf_from_bytes(b"\x00" * 100)
        assert not meta.has_btf

    def test_truncated_types(self) -> None:
        # Valid header but type section is truncated
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        data = b.build()
        # Truncate the data
        meta = parse_btf_from_bytes(data[:30])
        # Should degrade gracefully (empty or partial)
        assert isinstance(meta, BtfMetadata)


# ---------------------------------------------------------------------------
# TypeMetadataSource protocol
# ---------------------------------------------------------------------------


class TestTypeMetadataSourceProtocol:
    def test_protocol_methods(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        m_name = b.add_string("x")
        members = struct.pack("<III", m_name, 1, 0)
        b.add_type("point", BTF_KIND_STRUCT, 1, 4, extra=members)

        e_name = b.add_string("A")
        entries = struct.pack("<Ii", e_name, 0)
        b.add_type("my_enum", BTF_KIND_ENUM, 1, 4, extra=entries)

        meta = parse_btf_from_bytes(b.build())

        assert meta.has_data is True
        assert meta.get_struct_layout("point") is not None
        assert meta.get_struct_layout("nonexistent") is None
        assert meta.get_enum_info("my_enum") is not None
        assert meta.get_enum_info("nonexistent") is None

    def test_isinstance_check(self) -> None:
        from abicheck.type_metadata import TypeMetadataSource

        meta = BtfMetadata(has_btf=True)
        assert isinstance(meta, TypeMetadataSource)


# ---------------------------------------------------------------------------
# BtfMetadata accessor methods
# ---------------------------------------------------------------------------


class TestBtfMetadataAccessors:
    def test_get_function_proto(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        params = struct.pack("<II", 0, 1)  # unnamed param of type int
        b.add_type("", BTF_KIND_FUNC_PROTO, 1, 1, extra=params)
        b.add_type("myfunc", BTF_KIND_FUNC, 0, 2)

        meta = parse_btf_from_bytes(b.build())
        assert meta.get_function_proto("myfunc") is not None
        assert meta.get_function_proto("nonexistent") is None

    def test_get_typedef(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("myint", BTF_KIND_TYPEDEF, 0, 1)

        meta = parse_btf_from_bytes(b.build())
        assert meta.get_typedef("myint") == "int"
        assert meta.get_typedef("nonexistent") is None

    def test_empty_metadata(self) -> None:
        meta = BtfMetadata()
        assert meta.has_data is False
        assert meta.get_struct_layout("x") is None
        assert meta.get_enum_info("x") is None
        assert meta.get_function_proto("x") is None
        assert meta.get_typedef("x") is None


# ---------------------------------------------------------------------------
# _extra_data_size coverage
# ---------------------------------------------------------------------------


class TestExtraDataSize:
    def test_float(self) -> None:
        assert _extra_data_size(BTF_KIND_FLOAT, 0) == 4

    def test_array(self) -> None:
        assert _extra_data_size(BTF_KIND_ARRAY, 0) == 12

    def test_var(self) -> None:
        assert _extra_data_size(BTF_KIND_VAR, 0) == 4

    def test_datasec(self) -> None:
        assert _extra_data_size(BTF_KIND_DATASEC, 3) == 36

    def test_decl_tag(self) -> None:
        assert _extra_data_size(BTF_KIND_DECL_TAG, 0) == 4

    def test_fwd_no_extra(self) -> None:
        assert _extra_data_size(BTF_KIND_FWD, 0) == 0

    def test_typedef_no_extra(self) -> None:
        assert _extra_data_size(BTF_KIND_TYPEDEF, 0) == 0


# ---------------------------------------------------------------------------
# Extended type resolver coverage
# ---------------------------------------------------------------------------


class TestTypeResolverExtended:
    def _build_and_resolve(self, builder: BtfBuilder) -> _TypeResolver:
        data = builder.build()
        hdr = _parse_header(data)
        type_start = hdr.hdr_len + hdr.type_off
        str_start = hdr.hdr_len + hdr.str_off
        str_end = str_start + hdr.str_len
        types = _parse_types(data[type_start : type_start + hdr.type_len])
        return _TypeResolver(types, data[str_start:str_end])

    def test_float_name_and_size(self) -> None:
        b = BtfBuilder()
        enc = struct.pack("<I", 32)
        b.add_type("float", BTF_KIND_FLOAT, 0, 4, extra=enc)
        r = self._build_and_resolve(b)
        assert r.name(1) == "float"
        assert r.size(1) == 4

    def test_float_anonymous(self) -> None:
        b = BtfBuilder()
        enc = struct.pack("<I", 64)
        b.add_type("", BTF_KIND_FLOAT, 0, 8, extra=enc)
        r = self._build_and_resolve(b)
        assert r.name(1) == "float"

    def test_array_name_and_size(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        # array: elem_type=1(int), index_type=1, nelems=10
        array_extra = struct.pack("<III", 1, 1, 10)
        b.add_type("", BTF_KIND_ARRAY, 0, 0, extra=array_extra)
        r = self._build_and_resolve(b)
        assert r.name(2) == "int[10]"
        assert r.size(2) == 40

    def test_array_short_extra(self) -> None:
        """Array with short extra data (< 12 bytes) returns fallback values."""
        # Construct BtfType directly with truncated extra to bypass _parse_types
        void = BtfType(type_id=0, name_off=0, info=0, size_or_type=0, extra=b"")
        array_info = BTF_KIND_ARRAY << 24
        arr = BtfType(
            type_id=1, name_off=0, info=array_info, size_or_type=0, extra=b"\x00" * 8
        )  # only 8 bytes, need 12
        resolver = _TypeResolver([void, arr], b"\x00")
        assert resolver.name(1) == "[]"
        assert resolver.size(1) == 0

    def test_volatile_name(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_VOLATILE, 0, 1)
        r = self._build_and_resolve(b)
        assert r.name(2) == "volatile int"

    def test_restrict_name(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_RESTRICT, 0, 1)
        r = self._build_and_resolve(b)
        assert r.name(2) == "restrict int"

    def test_fwd_struct(self) -> None:
        b = BtfBuilder()
        b.add_type("mystruct", BTF_KIND_FWD, 0, 0, kflag=0)
        r = self._build_and_resolve(b)
        assert r.name(1) == "mystruct"

    def test_fwd_union(self) -> None:
        b = BtfBuilder()
        b.add_type("", BTF_KIND_FWD, 0, 0, kflag=1)
        r = self._build_and_resolve(b)
        assert r.name(1) == "<fwd union>"

    def test_func_proto_name(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_FUNC_PROTO, 0, 1, extra=b"")
        r = self._build_and_resolve(b)
        assert r.name(2) == "int(...)"

    def test_func_name(self) -> None:
        b = BtfBuilder()
        b.add_type("myfn", BTF_KIND_FUNC, 0, 0)
        r = self._build_and_resolve(b)
        assert r.name(1) == "myfn"

    def test_func_anonymous(self) -> None:
        b = BtfBuilder()
        b.add_type("", BTF_KIND_FUNC, 0, 0)
        r = self._build_and_resolve(b)
        assert r.name(1) == "<func>"

    def test_var_name(self) -> None:
        b = BtfBuilder()
        var_extra = struct.pack("<I", 0)  # linkage
        b.add_type("myvar", BTF_KIND_VAR, 0, 0, extra=var_extra)
        r = self._build_and_resolve(b)
        assert r.name(1) == "myvar"

    def test_var_anonymous(self) -> None:
        b = BtfBuilder()
        var_extra = struct.pack("<I", 0)
        b.add_type("", BTF_KIND_VAR, 0, 0, extra=var_extra)
        r = self._build_and_resolve(b)
        assert r.name(1) == "<var>"

    def test_type_tag_name(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_TYPE_TAG, 0, 1)
        r = self._build_and_resolve(b)
        assert r.name(2) == "int"

    def test_unknown_kind(self) -> None:
        """P2 review, fresh evidence (Codex): _parse_types() itself now
        stops at an unsupported kind (its real extra-data size is
        unknowable, so continuing would misalign every later record) --
        this resolver-level "unhandled kind" placeholder fallback is
        therefore unreachable via the real byte-parsing pipeline, but
        remains correct, defense-in-depth behavior for a type list built
        some other way (as here, constructed directly rather than through
        _parse_types). See TestCyclicQualifierChainMarksPartial and the
        table-level unsupported-kind tests in test_btf_metadata_evidence.py
        for the real _parse_types()-level contract."""
        unknown = BtfType(
            type_id=1, name_off=0, info=(30 << 24), size_or_type=0, extra=b""
        )
        r = _TypeResolver([BtfType(0, 0, 0, 0, b""), unknown], b"\x00")
        assert "<btf_kind_30:" in r.name(1)

    def test_invalid_type_id(self) -> None:
        b = BtfBuilder()
        r = self._build_and_resolve(b)
        assert "<btf:" in r.name(999)
        assert r.size(999) == 0

    def test_cycle_detection_name(self) -> None:
        b = BtfBuilder()
        # TYPEDEF pointing to itself: id=1 references id=1
        b.add_type("self_ref", BTF_KIND_TYPEDEF, 0, 1)
        r = self._build_and_resolve(b)
        # Should not infinite loop; returns "..." for cycle
        result = r.name(1)
        assert result == "self_ref"  # typedef with name returns name

    def test_cycle_detection_size(self) -> None:
        b = BtfBuilder()
        # TYPEDEF id=1 -> TYPEDEF id=2 -> TYPEDEF id=1
        b.add_type("a", BTF_KIND_TYPEDEF, 0, 2)
        b.add_type("b", BTF_KIND_TYPEDEF, 0, 1)
        r = self._build_and_resolve(b)
        # Should not infinite loop; cycle returns 0
        assert r.size(1) == 0

    def test_size_struct(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        m_name = b.add_string("x")
        members = struct.pack("<III", m_name, 1, 0)
        b.add_type("s", BTF_KIND_STRUCT, 1, 16, extra=members)
        r = self._build_and_resolve(b)
        assert r.size(2) == 16

    def test_size_enum(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("A")
        entries = struct.pack("<Ii", e_name, 0)
        b.add_type("e", BTF_KIND_ENUM, 1, 4, extra=entries)
        r = self._build_and_resolve(b)
        assert r.size(1) == 4

    def test_size_enum64(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("A")
        entries = struct.pack("<III", e_name, 0, 0)
        b.add_type("e64", BTF_KIND_ENUM64, 1, 8, extra=entries)
        r = self._build_and_resolve(b)
        assert r.size(1) == 8

    def test_size_int_from_extra(self) -> None:
        b = BtfBuilder()
        # INT with 16 bits
        int_enc = struct.pack("<I", 16)
        b.add_type("short", BTF_KIND_INT, 0, 2, extra=int_enc)
        r = self._build_and_resolve(b)
        assert r.size(1) == 2

    def test_size_int_from_size_or_type(self) -> None:
        """INT size falls back to size_or_type when extra is small."""
        b = BtfBuilder()
        # Build normally first, then we test INT with proper encoding
        int_enc = struct.pack("<I", 64)  # 64 bits = 8 bytes
        b.add_type("long", BTF_KIND_INT, 0, 8, extra=int_enc)
        r = self._build_and_resolve(b)
        assert r.size(1) == 8

    def test_size_float(self) -> None:
        b = BtfBuilder()
        float_enc = struct.pack("<I", 64)
        b.add_type("double", BTF_KIND_FLOAT, 0, 8, extra=float_enc)
        r = self._build_and_resolve(b)
        assert r.size(1) == 8

    def test_size_typedef(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("myint", BTF_KIND_TYPEDEF, 0, 1)
        r = self._build_and_resolve(b)
        assert r.size(2) == 4

    def test_size_volatile(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_VOLATILE, 0, 1)
        r = self._build_and_resolve(b)
        assert r.size(2) == 4

    def test_anon_struct_name(self) -> None:
        b = BtfBuilder()
        b.add_type("", BTF_KIND_STRUCT, 0, 0, extra=b"")
        r = self._build_and_resolve(b)
        assert r.name(1) == "<anon struct>"

    def test_anon_union_name(self) -> None:
        b = BtfBuilder()
        b.add_type("", BTF_KIND_UNION, 0, 0, extra=b"")
        r = self._build_and_resolve(b)
        assert r.name(1) == "<anon union>"

    def test_anon_enum_name(self) -> None:
        b = BtfBuilder()
        b.add_type("", BTF_KIND_ENUM, 0, 4, extra=b"")
        r = self._build_and_resolve(b)
        assert r.name(1) == "<anon enum>"

    def test_int_anonymous(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("", BTF_KIND_INT, 0, 4, extra=int_enc)
        r = self._build_and_resolve(b)
        assert r.name(1) == "int"

    def test_fwd_anon_struct(self) -> None:
        b = BtfBuilder()
        b.add_type("", BTF_KIND_FWD, 0, 0, kflag=0)
        r = self._build_and_resolve(b)
        assert r.name(1) == "<fwd struct>"


# ---------------------------------------------------------------------------
# Header edge cases
# ---------------------------------------------------------------------------


class TestParseHeaderExtended:
    def test_nonstandard_version_parses(self) -> None:
        """Non-standard BTF version should still parse."""
        hdr = struct.pack("<HBBIIIII", BTF_MAGIC, 99, 0, 24, 0, 0, 0, 1)
        str_data = b"\x00"
        data = hdr + str_data
        result = _parse_header(data)
        assert result.version == 99

    def test_string_no_null_terminator(self) -> None:
        """P2 review, round 2: a string with no NUL terminator before the
        end of the buffer is itself a truncation/corruption signal (both
        BTF and CTF specify every string-table entry as NUL-terminated) --
        valid=False, even though the remainder is still returned as a
        best-effort decode."""
        data = b"no_null"
        assert _read_string(data, 0) == ("no_null", False)


# ---------------------------------------------------------------------------
# Anonymous/unnamed enum and func proto edge cases
# ---------------------------------------------------------------------------


class TestBtfEdgeCases:
    def test_anonymous_enum_skipped(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("A")
        entries = struct.pack("<Ii", e_name, 0)
        b.add_type("", BTF_KIND_ENUM, 1, 4, extra=entries)
        meta = parse_btf_from_bytes(b.build())
        assert len(meta.enums) == 0

    def test_anonymous_enum64_skipped(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("A")
        entries = struct.pack("<III", e_name, 0, 0)
        b.add_type("", BTF_KIND_ENUM64, 1, 8, extra=entries)
        meta = parse_btf_from_bytes(b.build())
        assert len(meta.enums) == 0

    def test_anonymous_func_skipped(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_FUNC_PROTO, 0, 1, extra=b"")
        b.add_type("", BTF_KIND_FUNC, 0, 2)
        meta = parse_btf_from_bytes(b.build())
        assert len(meta.func_protos) == 0

    def test_anonymous_typedef_skipped(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("", BTF_KIND_TYPEDEF, 0, 1)
        meta = parse_btf_from_bytes(b.build())
        assert len(meta.typedefs) == 0

    def test_func_without_proto(self) -> None:
        """FUNC that references non-FUNC_PROTO type is skipped, and (P2
        review, fresh evidence, Codex) must mark the parse partial rather
        than silently reporting a clean, complete extraction with this
        function simply absent from func_protos."""
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("bad_func", BTF_KIND_FUNC, 0, 1)  # points to INT, not PROTO
        meta = parse_btf_from_bytes(b.build())
        assert len(meta.func_protos) == 0
        assert meta.extraction_partial is True

    def test_func_with_dangling_proto_reference(self) -> None:
        """Sibling of test_func_without_proto above: size_or_type names no
        type record at all (rather than one that exists but is the wrong
        kind) -- the same silent-skip path through proto_map.get()
        returning None, so it must be flagged the same way."""
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("dangling_func", BTF_KIND_FUNC, 0, 999)  # no such type_id
        meta = parse_btf_from_bytes(b.build())
        assert len(meta.func_protos) == 0
        assert meta.extraction_partial is True

    def test_truncated_type_section(self) -> None:
        """Type section that ends mid-entry."""
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        data = b.build()
        # Chop off last 2 bytes of type data to cause truncation
        # The header says type_len includes the full type, so we corrupt it
        hdr = _parse_header(data)
        type_start = hdr.hdr_len + hdr.type_off
        # Truncate to only have partial type data (just the 12-byte header, no extra)
        truncated_type_data = data[type_start : type_start + 12]
        # The int type needs 4 bytes of extra, so 12 bytes is truncated
        types = _parse_types(truncated_type_data)
        # Should get just the void sentinel since the int is truncated
        assert len(types) == 1

    def test_section_bounds_exceed_data(self) -> None:
        """Header claiming type/string sections beyond data size."""
        # Build valid header but with inflated lengths
        hdr = struct.pack(
            "<HBBIIIII", BTF_MAGIC, BTF_VERSION, 0, 24, 0, 99999, 0, 1
        )  # type_len way too large
        data = hdr + b"\x00"
        meta = parse_btf_from_bytes(data)
        assert not meta.has_btf

    def test_type_count(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        b.add_type("float", BTF_KIND_FLOAT, 0, 4, extra=int_enc)
        meta = parse_btf_from_bytes(b.build())
        assert meta.type_count == 2

    def test_duplicate_struct_name_first_wins(self) -> None:
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)

        m_name = b.add_string("x")
        members = struct.pack("<III", m_name, 1, 0)
        b.add_type("dup", BTF_KIND_STRUCT, 1, 4, extra=members)
        b.add_type("dup", BTF_KIND_STRUCT, 1, 8, extra=members)

        meta = parse_btf_from_bytes(b.build())
        assert meta.structs["dup"].byte_size == 4  # first wins

    def test_duplicate_enum_first_wins(self) -> None:
        b = BtfBuilder()
        e_name = b.add_string("A")
        entries = struct.pack("<Ii", e_name, 0)
        b.add_type("dup", BTF_KIND_ENUM, 1, 4, extra=entries)

        e2_name = b.add_string("B")
        entries2 = struct.pack("<Ii", e2_name, 1)
        b.add_type("dup", BTF_KIND_ENUM, 1, 4, extra=entries2)

        meta = parse_btf_from_bytes(b.build())
        assert "A" in meta.enums["dup"].members
