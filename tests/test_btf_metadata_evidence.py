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

"""P2 review finding, split out of ``test_btf_metadata.py`` (``_extra``-style
sibling, matching ``test_ctf_metadata_evidence.py``'s own identical split)
purely to stay under that file's AI-readiness test-size cap.

Finding: a BTF type/member with a string-table offset outside ``str_data``
fell back to ``read_null_terminated_string``'s empty-string return with no
completeness signal at all -- ``_extract_structs()`` would then either drop
the named layout (an empty struct name reads as anonymous and is skipped) or
emit a blank member, and ``parse_btf_from_bytes`` only set
``extraction_partial`` for type-table truncation or a raised exception,
neither of which this shape triggers. This let ``--require-complete-analysis``
pass for a truncated/corrupt BTF blob whose layout evidence was silently
incomplete. Fixed by widening ``read_null_terminated_string``'s return to
``(string, valid)`` and folding ``valid`` into ``extraction_partial`` through
every name-reading extractor (``_extract_structs``/``_extract_enums``/
``_extract_func_protos``/``_extract_typedefs``).

Covered through the public parser (``parse_btf_from_bytes``/
``extraction_partial``), not ``_read_string``/``read_null_terminated_string``
directly. Duplicates the small ``BtfBuilder`` fixture it needs rather than
importing it from the parent module -- every other ``_extra``-style sibling
test file in this suite is self-contained the same way.
"""

from __future__ import annotations

import struct

from abicheck.btf_metadata import (
    BTF_KIND_ENUM,
    BTF_KIND_FUNC,
    BTF_KIND_FUNC_PROTO,
    BTF_KIND_INT,
    BTF_KIND_STRUCT,
    BTF_KIND_TYPEDEF,
    BTF_MAGIC,
    BTF_VERSION,
    parse_btf_from_bytes,
)


class BtfBuilder:
    """Minimal BTF blob builder -- mirrors the parent test module's own."""

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
        hdr_len = 24
        type_len = len(type_data)
        str_off = type_len
        header = struct.pack(
            "<HBBIIIII",
            BTF_MAGIC,
            BTF_VERSION,
            0,
            hdr_len,
            0,
            type_len,
            str_off,
            len(str_data),
        )
        return header + type_data + str_data


def _corrupt_name_off_type(
    kind: int, vlen: int, size_or_type: int, extra: bytes = b""
) -> bytes:
    """A raw BTF type entry (bypassing BtfBuilder.add_type, which always
    allocates a real, in-bounds string-table offset) whose name_off is
    deliberately out of the string table's bounds."""
    info = (kind << 24) | (vlen & 0xFFFF)
    bad_name_off = 0xFFFFFF  # far past any real str_data this builds
    return struct.pack("<III", bad_name_off, info, size_or_type) + extra


class TestInvalidStringOffsetPropagatesToPartial:
    def test_struct_with_invalid_name_offset_marks_partial(self) -> None:
        b = BtfBuilder()
        b._type_entries.append(_corrupt_name_off_type(BTF_KIND_STRUCT, 0, 4))

        meta = parse_btf_from_bytes(b.build())
        assert meta.has_btf is True
        assert meta.extraction_partial is True
        # The struct is dropped (empty name reads as anonymous), not
        # silently kept under a garbage key -- but the loss is now visible.
        assert meta.structs == {}

    def test_struct_member_with_invalid_name_offset_marks_partial(self) -> None:
        """The reviewer's other named shape: a *member* name offset
        outside str_data, on an otherwise well-named, well-formed struct."""
        b = BtfBuilder()
        info = (BTF_KIND_STRUCT << 24) | (1 & 0xFFFF)
        name_off = b.add_string("S")
        bad_member_off = 0xFFFFFF
        member = struct.pack("<III", bad_member_off, 1, 0)
        entry = struct.pack("<III", name_off, info, 4) + member
        b._type_entries.append(entry)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs
        assert meta.structs["S"].fields[0].name == ""  # best-effort, still surfaced

    def test_enum_with_invalid_name_offset_marks_partial(self) -> None:
        b = BtfBuilder()
        b._type_entries.append(_corrupt_name_off_type(BTF_KIND_ENUM, 0, 4))

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.enums == {}

    def test_typedef_with_invalid_name_offset_marks_partial(self) -> None:
        b = BtfBuilder()
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=struct.pack("<I", 32))
        b._type_entries.append(_corrupt_name_off_type(BTF_KIND_TYPEDEF, 0, 1))

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.typedefs == {}

    def test_func_with_invalid_name_offset_marks_partial(self) -> None:
        b = BtfBuilder()
        b._type_entries.append(_corrupt_name_off_type(BTF_KIND_FUNC_PROTO, 0, 0))
        b._type_entries.append(_corrupt_name_off_type(BTF_KIND_FUNC, 0, 1))

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.func_protos == {}

    def test_well_formed_blob_is_not_flagged(self) -> None:
        """Positive control: every real, in-bounds offset (BtfBuilder's own
        add_type/add_string path) must not trip the new signal."""
        b = BtfBuilder()
        b.add_type("int", BTF_KIND_INT, 0, 4, extra=struct.pack("<I", 32))
        m_name = b.add_string("val")
        members = struct.pack("<III", m_name, 1, 0)
        b.add_type("simple", BTF_KIND_STRUCT, 1, 4, extra=members)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is False

    def test_referenced_type_with_invalid_name_offset_marks_partial(self) -> None:
        """P2 review, round 2: a validly-named struct whose member
        references a type (here an INT) resolved only through
        ``_TypeResolver.name()``/``_str_at()`` -- not through any direct
        extractor's own accumulator -- must still mark extraction_partial.
        Type layout: type_id 1 = INT with an out-of-bounds name_off,
        type_id 2 = struct "S" with one member of type 1."""
        b = BtfBuilder()
        b._type_entries.append(  # type_id 1: INT, corrupt name_off
            _corrupt_name_off_type(BTF_KIND_INT, 0, 4, extra=struct.pack("<I", 32))
        )
        info = (BTF_KIND_STRUCT << 24) | (1 & 0xFFFF)
        s_name = b.add_string("S")
        m_name = b.add_string("field")
        member = struct.pack("<III", m_name, 1, 0)  # references type_id 1
        entry = struct.pack("<III", s_name, info, 4) + member
        b._type_entries.append(entry)  # type_id 2

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs
        # Best-effort resolution still substitutes the kind default.
        assert meta.structs["S"].fields[0].type_name == "int"

    def test_out_of_range_member_type_reference_marks_partial(self) -> None:
        """P2 review, round 3: an otherwise well-formed struct member whose
        ``m_type`` names a type index past the parsed type table previously
        resolved to a ``"<btf:N>"`` placeholder name and a size of 0 with
        no completeness signal at all -- neither the member's own name nor
        any direct extractor's accumulator ever observes this failure."""
        b = BtfBuilder()
        info = (BTF_KIND_STRUCT << 24) | (1 & 0xFFFF)
        s_name = b.add_string("S")
        m_name = b.add_string("field")
        out_of_range_type = 99  # no type_id 99 exists -- only 0 (void), 1 (S)
        member = struct.pack("<III", m_name, out_of_range_type, 0)
        entry = struct.pack("<III", s_name, info, 4) + member
        b._type_entries.append(entry)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs
        assert meta.structs["S"].fields[0].type_name == "<btf:99>"
        assert meta.structs["S"].fields[0].byte_size == 0

    def test_unsupported_in_range_member_kind_marks_partial(self) -> None:
        """P2 review, round 4: an otherwise well-formed struct member whose
        ``m_type`` names a real, in-range type record -- but one whose own
        ``kind`` value (31, the reviewer's exact repro; no real BTF_KIND_*
        constant reaches that high) neither name nor size resolution
        recognizes at all. Distinct from the out-of-range-type-id case
        above: the record genuinely exists, its *kind* is what's
        unsupported."""
        b = BtfBuilder()
        unsupported_kind = 31
        b._type_entries.append(  # type_id 1: a real record, unsupported kind
            struct.pack("<III", 0, unsupported_kind << 24, 0)
        )
        info = (BTF_KIND_STRUCT << 24) | (1 & 0xFFFF)
        s_name = b.add_string("S")
        m_name = b.add_string("field")
        member = struct.pack("<III", m_name, 1, 0)  # references type_id 1
        entry = struct.pack("<III", s_name, info, 4) + member
        b._type_entries.append(entry)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs
        assert (
            meta.structs["S"].fields[0].type_name == f"<btf_kind_{unsupported_kind}:1>"
        )
        assert meta.structs["S"].fields[0].byte_size == 0

    def test_recognized_but_legitimately_sizeless_kind_is_not_flagged(self) -> None:
        """Positive control: BTF_KIND_FUNC_PROTO is a *recognized* kind with
        no meaningful byte size (a function type has no size) -- its
        legitimate size=0 must not be mistaken for the unsupported-kind
        shape above."""
        b = BtfBuilder()
        b.add_type("", BTF_KIND_FUNC_PROTO, 0, 0)  # type_id 1: fn() -> void

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is False


class TestUnterminatedStringMarksPartial:
    """P2 review, round 2: a name offset in-bounds but with no NUL
    terminator before the end of the string table is itself a truncation
    signal (BTF specifies every string-table entry as NUL-terminated) --
    the first round only caught the out-of-range-offset shape."""

    def test_struct_name_missing_terminator_marks_partial(self) -> None:
        b = BtfBuilder()
        info = BTF_KIND_STRUCT << 24
        # Manually build str_data with a trailing, unterminated name,
        # bypassing BtfBuilder.add_string (which always NUL-terminates).
        entry = struct.pack("<III", len(b._strings), info, 4)
        b._type_entries.append(entry)
        b._strings.extend(b"S")  # no trailing NUL

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True

    def test_well_terminated_name_is_not_flagged(self) -> None:
        """Positive control: a properly NUL-terminated trailing name (the
        normal BtfBuilder.add_string shape) must not trip this signal."""
        b = BtfBuilder()
        b.add_type("S", BTF_KIND_STRUCT, 0, 0)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is False
