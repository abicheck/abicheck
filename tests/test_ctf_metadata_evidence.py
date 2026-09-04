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
    CTF_K_ENUM,
    CTF_K_INTEGER,
    CTF_K_POINTER,
    CTF_K_STRUCT,
    CTF_K_TYPEDEF,
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
