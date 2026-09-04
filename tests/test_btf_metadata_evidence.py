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
    BTF_KIND_CONST,
    BTF_KIND_ENUM,
    BTF_KIND_FUNC,
    BTF_KIND_FUNC_PROTO,
    BTF_KIND_INT,
    BTF_KIND_STRUCT,
    BTF_KIND_TYPEDEF,
    BTF_KIND_VOLATILE,
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
        """P2 review, round 4, superseded by a later round (Codex): a real
        BTF type record whose own ``kind`` value (31; no real BTF_KIND_*
        constant reaches that high) neither name nor size resolution
        recognizes at all used to be caught only when something referenced
        it (this test originally built a struct member pointing at it).
        That per-reference check is no longer how this is caught at all --
        ``_parse_types()`` itself now stops at an unsupported kind (its real
        extra-data size is unknowable, so continuing would misalign every
        later record's offset in the type table), so type_id 2 (struct S,
        placed after the unsupported record) is never even reached/parsed.
        Still marks ``extraction_partial`` -- via the table-level
        truncation signal instead of a per-reference one -- but the struct
        that would have referenced it is correctly absent rather than
        published with a degraded field. See
        TestUnsupportedKindStopsTypeTableParsing for the direct
        table-level contract this test now exercises indirectly."""
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
        assert meta.structs == {}

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


class TestUnsupportedVersionMarksPartial:
    """P2 review, fresh evidence (Codex): _parse_header() only warns on a
    BTF version other than the sole one this parser's record layout
    actually understands (BTF_VERSION == 1) and keeps parsing anyway -- a
    future/different-version blob can therefore be misdecoded (wrong field
    widths, misread type kinds) while never raising, so the receipt could
    otherwise read "parsed" despite the layout not being one this parser
    is known to handle correctly."""

    @staticmethod
    def _build_with_version(version: int) -> bytes:
        b = BtfBuilder()
        b.add_type("S", BTF_KIND_STRUCT, 0, 0)
        blob = bytearray(b.build())
        blob[2] = version  # header layout: magic(H) version(B) flags(B) ...
        return bytes(blob)

    def test_unsupported_version_marks_partial(self) -> None:
        meta = parse_btf_from_bytes(self._build_with_version(2))
        assert meta.has_btf is True
        assert meta.extraction_partial is True

    def test_supported_version_is_not_flagged(self) -> None:
        """Positive control: the one version this parser actually supports
        must not be flagged."""
        meta = parse_btf_from_bytes(self._build_with_version(BTF_VERSION))
        assert meta.extraction_partial is False


class TestCyclicQualifierChainMarksPartial:
    """P2 review, fresh evidence (Codex): a malformed BTF qualifier chain
    that cycles (e.g. CONST -> VOLATILE -> CONST) reaches
    ``_TypeResolver._resolve_cached``'s own cycle guard, which previously
    substituted its placeholder ("..."/0) without touching
    ``invalid_strings`` -- unlike an out-of-range type_id or an unhandled
    kind, both already covered above. A struct member referencing such a
    cycle was therefore emitted with a plausible-looking degraded name/size
    fact and ``extraction_partial=False``, so a channel-specific assurance
    consumer (e.g. ``--require-complete-analysis``) had no signal the
    layout it was trusting was actually degraded."""

    def test_cyclic_member_type_marks_partial(self) -> None:
        b = BtfBuilder()
        # Reserve ids 1 (CONST) and 2 (VOLATILE) up front, then forward-
        # reference each other -- BTF type ids are plain integers, so the
        # cycle doesn't need to exist yet when each add_type call runs.
        const_id = b.add_type("", BTF_KIND_CONST, 0, 2)  # -> volatile (id 2)
        volatile_id = b.add_type("", BTF_KIND_VOLATILE, 0, 1)  # -> const (id 1)
        assert (const_id, volatile_id) == (1, 2)

        member = struct.pack("<III", 0, const_id, 0)  # name="", type=CONST, offset=0
        b.add_type("S", BTF_KIND_STRUCT, 1, 4, extra=member)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        # The struct/field are still emitted (best-effort degraded output)
        # -- only the completeness signal was missing, not the struct.
        assert "S" in meta.structs
        assert meta.structs["S"].fields[0].type_name == "const volatile ..."

    def test_acyclic_qualifier_chain_is_not_flagged(self) -> None:
        """Positive control: a genuinely acyclic qualifier chain (const ->
        int, no self-reference) must not be flagged."""
        b = BtfBuilder()
        int_enc = struct.pack("<I", 32)
        int_id = b.add_type("int", BTF_KIND_INT, 0, 4, extra=int_enc)
        const_id = b.add_type("", BTF_KIND_CONST, 0, int_id)

        member = struct.pack("<III", 0, const_id, 0)
        b.add_type("S", BTF_KIND_STRUCT, 1, 4, extra=member)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is False
        assert meta.structs["S"].fields[0].type_name == "const int"


class TestUnsupportedKindStopsTypeTableParsing:
    """P2 review, fresh evidence (Codex): a BTF record whose own ``kind``
    isn't one this parser recognizes has an unknowable real extra-data
    size, so ``_extra_data_size()``'s old "no extra data" fallback for it
    could misalign every subsequent record's own offset in the type
    table -- corrupting facts far beyond the one unsupported record, not
    just omitting it. Fixed by validating every kind table-level, inside
    ``_parse_types()`` itself, rather than relying on some later
    extractor/resolver to reach the unsupported record on demand (which
    an *unreferenced* unsupported-kind record -- one no struct/enum/
    func_proto/typedef points at -- would never do at all)."""

    def test_unreferenced_unsupported_kind_marks_partial(self) -> None:
        b = BtfBuilder()
        unsupported_kind = 30
        # A standalone record nothing else references.
        b._type_entries.append(struct.pack("<III", 0, unsupported_kind << 24, 0))

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.type_count == 0

    def test_type_after_unsupported_kind_is_dropped_not_misparsed(self) -> None:
        """A struct placed *after* an unsupported-kind record in the type
        table must not be reached at all (parsing stops at the
        unsupported record) -- never silently misparsed from a
        miscomputed offset."""
        b = BtfBuilder()
        unsupported_kind = 30
        b._type_entries.append(struct.pack("<III", 0, unsupported_kind << 24, 0))
        b.add_type("S", BTF_KIND_STRUCT, 0, 4)

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert meta.structs == {}

    def test_type_before_unsupported_kind_still_parses(self) -> None:
        """Positive control: a struct defined *before* the unsupported
        record in the type table is unaffected."""
        b = BtfBuilder()
        b.add_type("S", BTF_KIND_STRUCT, 0, 4)
        unsupported_kind = 30
        b._type_entries.append(struct.pack("<III", 0, unsupported_kind << 24, 0))

        meta = parse_btf_from_bytes(b.build())
        assert meta.extraction_partial is True
        assert "S" in meta.structs

    def test_every_kind_this_parser_names_is_not_flagged(self) -> None:
        """Positive control: every kind constant this module actually
        exports must not trip the unsupported-kind guard -- an exhaustive
        sibling to the one-kind-at-a-time positive controls elsewhere in
        this suite, guarding against a future kind constant added to
        _KNOWN_BTF_KINDS's *definition* silently drifting from the set
        _extra_data_size() actually has a branch for."""
        from abicheck.btf_metadata import _KNOWN_BTF_KINDS, _extra_data_size

        for kind in sorted(_KNOWN_BTF_KINDS):
            b = BtfBuilder()
            b._type_entries.append(struct.pack("<III", 0, kind << 24, 0))
            extra_len = _extra_data_size(kind, 0)
            if extra_len:
                b._type_entries[-1] += b"\x00" * extra_len
            meta = parse_btf_from_bytes(b.build())
            assert meta.extraction_partial is False, f"kind {kind} was flagged"
