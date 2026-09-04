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

"""P2 review finding, split out of ``test_ctf_metadata.py`` (``_extra``-style
sibling, matching e.g. ``test_analysis_assurance_depth_and_graph_overlap.
py``) purely to stay under that file's AI-readiness no-growth debt baseline.

Finding: ``parse_ctf_from_bytes`` catches a struct-extraction exception
internally and returns a partially-populated object, but the conversion to
``DwarfMetadata`` still labelled the basic channel ``parsed``. Fixed by
``CtfMetadata.extraction_partial``, set whenever any extraction stage raises,
and read by ``to_dwarf_metadata()`` to report ``partial`` instead.

Duplicates the small ``CtfBuilder`` fixture it needs rather than importing it
from the parent module -- every other ``_extra``-style sibling test file in
this suite is self-contained the same way.
"""

from __future__ import annotations

import struct

from abicheck.ctf_metadata import (
    _CTF_V2_LSTRUCT_THRESH,
    CTF_K_CONST,
    CTF_K_ENUM,
    CTF_K_FORWARD,
    CTF_K_INTEGER,
    CTF_K_POINTER,
    CTF_K_STRUCT,
    CTF_K_TYPEDEF,
    CTF_K_VOLATILE,
    CTF_MAGIC,
    CTF_VERSION_2,
    CTF_VERSION_3,
    _parse_types,
    parse_ctf_from_bytes,
)


class CtfBuilder:
    """Minimal CTF v3 blob builder -- just enough for one INTEGER type."""

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
        self, name: str, kind: int, vlen: int, size_or_type: int, extra: bytes = b""
    ) -> int:
        name_off = self.add_string(name) if name else 0
        info = (kind << 24) | (vlen & 0xFFFF)
        entry = struct.pack("<III", name_off, info, size_or_type) + extra
        self._type_entries.append(entry)
        return len(self._type_entries)

    def build(self) -> bytes:
        type_data = b"".join(self._type_entries)
        str_data = bytes(self._strings)
        str_off = len(type_data)
        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, 0)
        header += struct.pack("<IIIIIIII", 0, 0, 0, 0, 0, 0, str_off, len(str_data))
        return header + type_data + str_data


def test_struct_extraction_failure_propagates_to_partial(monkeypatch) -> None:
    """P2 review: parse_ctf_from_bytes catches a struct-extraction exception
    internally and returns a partially-populated object, but must not
    silently claim "parsed" basic layout evidence for it (mirrors the
    identical BTF fix)."""
    from abicheck import ctf_metadata as ctf_mod

    b = CtfBuilder()
    int_enc = struct.pack("<I", 32)
    b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

    def boom(*_a, **_k):
        raise RuntimeError("bad struct record")

    monkeypatch.setattr(ctf_mod, "_extract_structs", boom)

    meta = parse_ctf_from_bytes(b.build())
    assert meta.has_ctf is True
    assert meta.extraction_partial is True

    dwarf = meta.to_dwarf_metadata()
    assert dwarf.has_dwarf is True
    assert dwarf.evidence_state == "partial"


def test_truncated_type_section_propagates_to_partial(monkeypatch) -> None:
    """P2 review, fresh evidence: `_parse_types()` silently truncating a
    type record (log + return, never raise) must still mark the receipt
    `partial`, not `parsed` -- the earlier fix above only covered a
    raised-and-caught extraction-stage exception, not a truncation the type
    parser itself swallows before extraction even runs (mirrors the
    identical BTF fix). Exercises the real `parse_ctf_from_bytes` ->
    `_parse_types` wiring, with `_parse_types` itself only stubbed to
    report the same "truncated" signal its own unit tests
    (TestParseTypesTruncation in test_ctf_metadata.py) already prove it
    produces for a genuinely cut-off buffer."""
    from abicheck import ctf_metadata as ctf_mod

    real_parse_types = ctf_mod._parse_types

    def wrapped(type_data: bytes, version: int, truncated=None):
        result = real_parse_types(type_data, version)
        if truncated is not None:
            truncated.append(True)
        return result

    monkeypatch.setattr(ctf_mod, "_parse_types", wrapped)

    b = CtfBuilder()
    int_enc = struct.pack("<I", 32)
    b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

    meta = parse_ctf_from_bytes(b.build())
    assert meta.has_ctf is True
    assert meta.extraction_partial is True
    assert meta.to_dwarf_metadata().evidence_state == "partial"


def test_clean_parse_reports_parsed_and_not_partial() -> None:
    b = CtfBuilder()
    int_enc = struct.pack("<I", 32)
    b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

    meta = parse_ctf_from_bytes(b.build())
    assert meta.extraction_partial is False
    assert meta.to_dwarf_metadata().evidence_state == "parsed"


class TestParseTypesTruncationOutParam:
    """P2 review, fresh evidence: none of ``_parse_types()``'s ``break``
    sites (see ``TestParseTypesTruncation`` in ``test_ctf_metadata.py``)
    raise, so a caller only reading the returned list can't tell
    "truncated" from "genuinely nothing more to parse". The opt-in
    ``truncated`` out-param must fire for each ``break`` shape, and must
    NOT fire for a section the loop consumed in full. Split into this
    sibling file (mirroring ``test_struct_extraction_failure_propagates_
    to_partial`` above) purely to stay under ``test_ctf_metadata.py``'s own
    architecture debt-no-growth baseline.
    """

    def test_v3_truncated_header_sets_truncated(self) -> None:
        truncated: list[bool] = []
        _parse_types(b"\x00" * 8, CTF_VERSION_3, truncated)
        assert truncated == [True]

    def test_v2_truncated_header_sets_truncated(self) -> None:
        truncated: list[bool] = []
        _parse_types(b"\x00" * 4, CTF_VERSION_2, truncated)
        assert truncated == [True]

    def test_v2_truncated_size_field_sets_truncated(self) -> None:
        data = struct.pack("<I", 0) + struct.pack("<H", (CTF_K_POINTER << 11))
        truncated: list[bool] = []
        _parse_types(data, CTF_VERSION_2, truncated)
        assert truncated == [True]

    def test_v3_truncated_extra_sets_truncated(self) -> None:
        info = CTF_K_INTEGER << 24
        data = struct.pack("<III", 0, info, 4)
        truncated: list[bool] = []
        _parse_types(data, CTF_VERSION_3, truncated)
        assert truncated == [True]

    def test_empty_section_is_not_truncated(self) -> None:
        """A zero-length type section is a legitimately empty (not
        truncated) parse."""
        truncated: list[bool] = []
        _parse_types(b"", CTF_VERSION_3, truncated)
        assert truncated == []

    def test_fully_consumed_section_is_not_truncated(self) -> None:
        """Positive control: a v3 type section with no leftover bytes at
        all must not be flagged."""
        int_enc = struct.pack("<I", 32)
        info = CTF_K_INTEGER << 24
        one_type = struct.pack("<III", 0, info, 4) + int_enc
        truncated: list[bool] = []
        result = _parse_types(one_type, CTF_VERSION_3, truncated)
        assert len(result) == 2  # void + the one complete integer
        assert truncated == []

    def test_v2_truncated_large_struct_real_size_sets_truncated(self) -> None:
        """P2 review, fresh evidence: a v2 struct/union whose 16-bit size
        marker is >= ``_CTF_V2_LSTRUCT_THRESH`` ("large") must be followed
        by a mandatory 4-byte real size. Ending the section right after that
        marker (no real-size bytes at all) previously fell through silently
        -- neither appending to ``truncated`` nor ``break``ing -- keeping the
        raw 16-bit marker as ``size_or_type`` and (for ``vlen=0``) letting
        ``_extra_data_size`` read 0 bytes of extra data, so the malformed
        entry was accepted as a complete parse.
        """
        info = struct.pack("<H", CTF_K_STRUCT << 11)  # vlen=0, isroot=0
        marker = struct.pack("<H", _CTF_V2_LSTRUCT_THRESH)
        data = struct.pack("<I", 0) + info + marker  # no real-size bytes follow
        truncated: list[bool] = []
        result = _parse_types(data, CTF_VERSION_2, truncated)
        assert truncated == [True]
        # Only the synthetic void entry -- nothing else accepted.
        assert len(result) == 1

    def test_v2_truncated_large_struct_partial_real_size_sets_truncated(
        self,
    ) -> None:
        """Sibling case: 1-3 leftover bytes (still not the full 4-byte real
        size) must also be flagged, not just the exact 0-bytes-left shape
        above -- the bug class is "insufficient bytes for the mandatory
        field," not one specific byte count."""
        info = struct.pack("<H", CTF_K_STRUCT << 11)
        marker = struct.pack("<H", _CTF_V2_LSTRUCT_THRESH)
        for leftover in (1, 2, 3):
            data = struct.pack("<I", 0) + info + marker + (b"\x00" * leftover)
            truncated: list[bool] = []
            result = _parse_types(data, CTF_VERSION_2, truncated)
            assert truncated == [True], f"leftover={leftover}"
            assert len(result) == 1, f"leftover={leftover}"

    def test_v2_large_struct_with_full_real_size_is_not_truncated(self) -> None:
        """Positive control: the same "large" shape, but with the mandatory
        4-byte real size actually present (and vlen=0, so no member data
        follows) -- a legitimately complete entry, not truncated."""
        info = struct.pack("<H", CTF_K_STRUCT << 11)
        marker = struct.pack("<H", _CTF_V2_LSTRUCT_THRESH)
        real_size = struct.pack("<I", 0x20000)  # an actual size >= the marker
        data = struct.pack("<I", 0) + info + marker + real_size
        truncated: list[bool] = []
        result = _parse_types(data, CTF_VERSION_2, truncated)
        assert truncated == []
        assert len(result) == 2  # void + the one complete (empty) struct


def _corrupt_name_off_type(
    kind: int, vlen: int, size_or_type: int, extra: bytes = b""
) -> bytes:
    """A raw CTF v3 type entry (bypassing CtfBuilder.add_type, which always
    allocates a real, in-bounds string-table offset) whose name_off is
    deliberately out of the string table's bounds."""
    info = (kind << 24) | (vlen & 0xFFFF)
    bad_name_off = 0xFFFFFF  # far past any real str_data this builds
    return struct.pack("<III", bad_name_off, info, size_or_type) + extra


class TestInvalidStringOffsetPropagatesToPartial:
    """P2 review, fresh evidence beyond the resolved BTF thread (same root
    cause, CTF's own sibling): a CTF type/member with a string-table offset
    outside ``str_data`` fell back to ``read_null_terminated_string``'s
    empty-string return with no completeness signal -- ``_extract_structs()``
    would then drop the named layout (empty name reads as anonymous) or emit
    a blank member, and ``parse_ctf_from_bytes`` only set
    ``extraction_partial`` for type-table truncation or a raised exception,
    neither of which this shape triggers. Covered through the public parser
    (``parse_ctf_from_bytes``/``extraction_partial``), not ``_read_string``
    directly. Split into this sibling file for the same reason as the class
    above -- ``test_ctf_metadata.py`` is already at its debt baseline.
    """

    def test_struct_with_invalid_name_offset_marks_partial(self) -> None:
        b = CtfBuilder()
        b._type_entries.append(_corrupt_name_off_type(CTF_K_STRUCT, 0, 4))

        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf is True
        assert meta.extraction_partial is True
        assert meta.structs == {}

    def test_struct_member_with_invalid_name_offset_marks_partial(self) -> None:
        """The reviewer's other named shape: a member name offset outside
        str_data, on an otherwise well-named, well-formed struct."""
        b = CtfBuilder()
        info = (CTF_K_STRUCT << 24) | (1 & 0xFFFF)
        name_off = b.add_string("S")
        bad_member_off = 0xFFFFFF
        member = struct.pack("<II", bad_member_off, (1 << 16) | 0)
        entry = struct.pack("<III", name_off, info, 4) + member
        b._type_entries.append(entry)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs
        assert meta.structs["S"].fields[0].name == ""

    def test_enum_with_invalid_name_offset_marks_partial(self) -> None:
        b = CtfBuilder()
        b._type_entries.append(_corrupt_name_off_type(CTF_K_ENUM, 0, 4))

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.enums == {}

    def test_typedef_with_invalid_name_offset_marks_partial(self) -> None:
        b = CtfBuilder()
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=struct.pack("<I", 32))
        b._type_entries.append(_corrupt_name_off_type(CTF_K_TYPEDEF, 0, 1))

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.typedefs == {}

    def test_well_formed_blob_is_not_flagged(self) -> None:
        """Positive control: every real, in-bounds offset must not trip
        the new signal."""
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        m_name = b.add_string("val")
        members = struct.pack("<II", m_name, (1 << 16) | 0)
        b.add_type("simple", CTF_K_STRUCT, 1, 4, extra=members)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is False

    def test_referenced_type_with_invalid_name_offset_marks_partial(self) -> None:
        """P2 review, round 2: a validly-named struct whose member
        references a type (here an INTEGER) resolved only through
        ``_TypeResolver.name()``/``_str_at()`` -- not through any direct
        extractor's own accumulator -- must still mark extraction_partial.
        type_id 1 = INTEGER with an out-of-bounds name_off, type_id 2 =
        struct "S" with one member of type 1."""
        b = CtfBuilder()
        b._type_entries.append(  # type_id 1: INTEGER, corrupt name_off
            _corrupt_name_off_type(CTF_K_INTEGER, 0, 4, extra=struct.pack("<I", 32))
        )
        info = (CTF_K_STRUCT << 24) | (1 & 0xFFFF)
        s_name = b.add_string("S")
        m_name = b.add_string("field")
        member = struct.pack("<II", m_name, (1 << 16) | 0)  # references type_id 1
        entry = struct.pack("<III", s_name, info, 4) + member
        b._type_entries.append(entry)  # type_id 2

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs

    def test_out_of_range_member_type_reference_marks_partial(self) -> None:
        """P2 review, round 3: the CTF sibling of the identical BTF finding
        -- an otherwise well-formed struct member whose type index names a
        type past the parsed type table previously resolved silently, with
        no completeness signal at all."""
        b = CtfBuilder()
        info = (CTF_K_STRUCT << 24) | (1 & 0xFFFF)
        s_name = b.add_string("S")
        m_name = b.add_string("field")
        out_of_range_type = 99  # no type_id 99 exists -- only 0 (void), 1 (S)
        member = struct.pack("<II", m_name, (out_of_range_type << 16) | 0)
        entry = struct.pack("<III", s_name, info, 4) + member
        b._type_entries.append(entry)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs
        assert meta.structs["S"].fields[0].type_name == "<ctf:99>"
        assert meta.structs["S"].fields[0].byte_size == 0

    def test_unsupported_in_range_member_kind_marks_partial(self) -> None:
        """P2 review, round 4, superseded by a later round (Codex, mirrors
        the identical BTF fix): a real CTF type record whose own ``kind``
        value isn't recognized at all used to be caught only when something
        referenced it (this test originally built a struct member pointing
        at it). That per-reference check is no longer how this is caught at
        all -- ``_parse_types()`` itself now stops at an unsupported kind
        (its real extra-data size is unknowable, so continuing would
        misalign every later record's offset in the type table), so the
        struct placed after the unsupported record is never even
        reached/parsed. Still marks ``extraction_partial`` -- via the
        table-level truncation signal instead of a per-reference one -- but
        the struct that would have referenced it is correctly absent
        rather than published with a degraded field."""
        b = CtfBuilder()
        unsupported_kind = 31
        b._type_entries.append(  # type_id 1: a real record, unsupported kind
            struct.pack("<III", 0, unsupported_kind << 24, 0)
        )
        info = (CTF_K_STRUCT << 24) | (1 & 0xFFFF)
        s_name = b.add_string("S")
        m_name = b.add_string("field")
        member = struct.pack("<II", m_name, (1 << 16) | 0)  # references type_id 1
        entry = struct.pack("<III", s_name, info, 4) + member
        b._type_entries.append(entry)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.structs == {}

    def test_recognized_but_legitimately_sizeless_kind_is_not_flagged(self) -> None:
        """Positive control: CTF_K_FORWARD is a *recognized* kind with no
        meaningful byte size -- its legitimate size=0 must not be mistaken
        for the unsupported-kind shape above."""
        b = CtfBuilder()
        b.add_type("fwd", CTF_K_FORWARD, 0, 0)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is False


class TestUnterminatedStringMarksPartial:
    """CTF sibling of the identical BTF finding (P2 review, round 2): an
    in-bounds name offset with no NUL terminator before the end of the
    string table is itself a truncation signal."""

    def test_struct_name_missing_terminator_marks_partial(self) -> None:
        b = CtfBuilder()
        info = CTF_K_STRUCT << 24
        entry = struct.pack("<III", len(b._strings), info, 4)
        b._type_entries.append(entry)
        b._strings.extend(b"S")  # no trailing NUL

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True

    def test_well_terminated_name_is_not_flagged(self) -> None:
        """Positive control: a normal, properly NUL-terminated name."""
        b = CtfBuilder()
        b.add_type("S", CTF_K_STRUCT, 0, 0)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is False


class TestTruncatedCompressedStreamIsRejected:
    """P2 review, fresh evidence (Codex): zlib.decompressobj().decompress()
    can return a complete-looking payload without raising even when the
    input was truncated -- cutting only the trailing checksum/end marker
    (as little as one byte) still yields every decompressed byte, since
    decompression itself finished before that marker is even consumed.
    Previously this collapsed onto the same "success" path as a genuine
    full stream, with ``has_ctf=True`` and no completeness signal at all.
    """

    @staticmethod
    def _compressed_ctf_blob(n_types: int) -> bytes:
        """Build a real zlib-compressed CTF v3 blob with *n_types* simple
        integer types -- large enough that a naive truncation still leaves
        the decompressed body looking complete (the reviewer's own repro
        scale)."""
        import zlib

        from abicheck.ctf_metadata import CTF_F_COMPRESS, CTF_MAGIC, CTF_VERSION_3

        strings = bytearray(b"\x00")
        str_offsets: dict[str, int] = {"": 0}
        type_entries: list[bytes] = []

        def add_string(s: str) -> int:
            if s in str_offsets:
                return str_offsets[s]
            off = len(strings)
            strings.extend(s.encode("utf-8") + b"\x00")
            str_offsets[s] = off
            return off

        for i in range(n_types):
            name_off = add_string(f"int{i}")
            info = CTF_K_INTEGER << 24
            int_enc = struct.pack("<I", 32)
            type_entries.append(struct.pack("<III", name_off, info, 4) + int_enc)

        type_data = b"".join(type_entries)
        str_data = bytes(strings)
        str_off = len(type_data)
        body_header = struct.pack("<IIIIIIII", 0, 0, 0, 0, 0, 0, str_off, len(str_data))
        body = body_header + type_data + str_data
        compressed = zlib.compress(body)
        preamble = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, CTF_F_COMPRESS)
        return preamble + compressed

    def test_truncated_trailer_is_rejected(self) -> None:
        full = self._compressed_ctf_blob(100)
        for cut in (1, 2, 3, 4):
            truncated = full[:-cut]
            meta = parse_ctf_from_bytes(truncated)
            # A rejected decompression returns the empty sentinel outright
            # (matches the existing zip-bomb-limit/zlib.error sibling
            # failure modes in _decompress_if_needed's own caller) -- never
            # a "successful" parse of the still-decompressed 100 types.
            assert meta.has_ctf is False, f"cut={cut} bytes was not rejected"

    def test_complete_compressed_stream_is_not_flagged(self) -> None:
        """Positive control: a genuinely complete compressed stream must
        still parse cleanly, with every type recovered."""
        full = self._compressed_ctf_blob(100)
        meta = parse_ctf_from_bytes(full)
        assert meta.has_ctf is True
        assert meta.extraction_partial is False


class TestCyclicQualifierChainMarksPartial:
    """P2 review, fresh evidence (Codex): the CTF sibling of the identical
    BTF fix -- a malformed qualifier chain that cycles (CONST -> VOLATILE
    -> CONST) reaches ``_TypeResolver.name()``/``.size()``'s own cycle
    guard, which previously returned "..."/0 without touching
    ``invalid_strings``. A struct member referencing such a cycle was
    therefore emitted with a degraded fact and ``extraction_partial=False``."""

    def test_cyclic_member_type_marks_partial(self) -> None:
        b = CtfBuilder()
        # CTF v3, non-large struct member encoding: m_off_val packs the
        # referenced type in the upper 16 bits, bit offset in the lower 16.
        const_id = b.add_type("", CTF_K_CONST, 0, 2)  # -> volatile (id 2)
        volatile_id = b.add_type("", CTF_K_VOLATILE, 0, 1)  # -> const (id 1)
        assert (const_id, volatile_id) == (1, 2)

        m_off_val = (const_id << 16) | 0
        member = struct.pack("<II", 0, m_off_val)  # name="", type=CONST, bitoff=0
        b.add_type("S", CTF_K_STRUCT, 1, 4, extra=member)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs
        assert meta.structs["S"].fields[0].type_name == "const volatile ..."

    def test_acyclic_qualifier_chain_is_not_flagged(self) -> None:
        """Positive control: a genuinely acyclic qualifier chain (const ->
        int, no self-reference) must not be flagged."""
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        int_id = b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)
        const_id = b.add_type("", CTF_K_CONST, 0, int_id)

        m_off_val = (const_id << 16) | 0
        member = struct.pack("<II", 0, m_off_val)
        b.add_type("S", CTF_K_STRUCT, 1, 4, extra=member)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is False
        assert meta.structs["S"].fields[0].type_name == "const int"


class TestUnsupportedKindStopsTypeTableParsing:
    """P2 review, fresh evidence (Codex, mirrors the identical BTF fix): a
    CTF record whose own ``kind`` isn't one this parser recognizes has an
    unknowable real extra-data size, so ``_extra_data_size()``'s old "no
    extra data" fallback for it could misalign every subsequent record's
    own offset in the type table -- corrupting facts far beyond the one
    unsupported record, not just omitting it. Fixed by validating every
    kind table-level, inside ``_parse_types()`` itself."""

    def test_unreferenced_unsupported_kind_marks_partial(self) -> None:
        b = CtfBuilder()
        unsupported_kind = 30
        b._type_entries.append(struct.pack("<III", 0, unsupported_kind << 24, 0))

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.type_count == 0

    def test_type_after_unsupported_kind_is_dropped_not_misparsed(self) -> None:
        """A struct placed *after* an unsupported-kind record in the type
        table must not be reached at all (parsing stops at the
        unsupported record) -- never silently misparsed from a
        miscomputed offset."""
        b = CtfBuilder()
        unsupported_kind = 30
        b._type_entries.append(struct.pack("<III", 0, unsupported_kind << 24, 0))
        b.add_type("S", CTF_K_STRUCT, 0, 4)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.structs == {}

    def test_type_before_unsupported_kind_still_parses(self) -> None:
        """Positive control: a struct defined *before* the unsupported
        record in the type table is unaffected."""
        b = CtfBuilder()
        b.add_type("S", CTF_K_STRUCT, 0, 4)
        unsupported_kind = 30
        b._type_entries.append(struct.pack("<III", 0, unsupported_kind << 24, 0))

        meta = parse_ctf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs

    def test_every_kind_this_parser_names_is_not_flagged(self) -> None:
        """Positive control: every kind constant this module actually
        exports must not trip the unsupported-kind guard."""
        from abicheck.ctf_metadata import _KNOWN_CTF_KINDS, _extra_data_size

        for kind in sorted(_KNOWN_CTF_KINDS):
            b = CtfBuilder()
            b._type_entries.append(struct.pack("<III", 0, kind << 24, 0))
            extra_len = _extra_data_size(kind, 0, CTF_VERSION_3, 0)
            if extra_len:
                b._type_entries[-1] += b"\x00" * extra_len
            meta = parse_ctf_from_bytes(b.build())
            assert meta.extraction_partial is False, f"kind {kind} was flagged"


class TestReversedSectionOffsetsRejected:
    """P2 review, fresh evidence (Codex): a header with ``type_off >
    str_off`` (the type section reversed against the string section it's
    supposed to precede) computes ``type_end = hdr_size + header.str_off``
    below ``type_start`` -- ``data[type_start:type_end]`` is a plain Python
    slice, so start > end silently yields ``b""`` rather than raising,
    discarding every type record with no truncation signal at all. The
    existing ``type_end > len(data)``/``str_end > len(data)`` bounds check
    never catches this shape, since both computed endpoints can legitimately
    sit within the buffer."""

    def test_type_off_greater_than_str_off_is_rejected(self) -> None:
        """The reviewer's exact repro: type_off=4, str_off=0, a one-byte
        NUL string table -- both endpoints are in-bounds, only their
        relative order is wrong."""
        header = struct.pack("<HBB", CTF_MAGIC, CTF_VERSION_3, 0)
        header += struct.pack("<IIIIIIII", 0, 0, 0, 0, 0, 4, 0, 1)
        blob = header + b"\x00"

        meta = parse_ctf_from_bytes(blob)
        assert meta.has_ctf is False
        assert meta.extraction_partial is False  # rejected outright, not "partial"

    def test_normal_section_ordering_is_not_flagged(self) -> None:
        """Positive control: the ordinary type-then-string layout (the
        shape every other test in this suite already builds via
        CtfBuilder) must not be rejected."""
        b = CtfBuilder()
        int_enc = struct.pack("<I", 32)
        b.add_type("int", CTF_K_INTEGER, 0, 4, extra=int_enc)

        meta = parse_ctf_from_bytes(b.build())
        assert meta.has_ctf is True
        assert meta.extraction_partial is False
        assert meta.type_count == 1
