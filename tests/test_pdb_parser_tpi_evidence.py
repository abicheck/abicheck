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

"""P2 review finding, split out of ``test_pdb_parser.py`` (``_extra``-style
sibling, matching e.g. ``test_ctf_metadata_evidence.py``) purely to stay
under that file's AI-readiness no-growth debt baseline.

Finding: ``parse_tpi_stream()`` can stop before every promised type index
was consumed (a malformed ``rec_len``, a record whose declared length runs
past the stream's own bounds, or simply running out of bytes), and
``TypeDatabase.parse_all()`` catches per-record decode failures -- neither
condition was exposed to a caller, so a PDB's basic debug-evidence channel
could read ``parsed`` even though layouts/enums were silently dropped.
Fixed by ``TpiStream.truncated`` (stream-level) and ``TypeDatabase.
failed_record_count`` (per-record), both read by ``pdb_metadata.
parse_pdb_debug_info`` to downgrade to ``partial`` -- see
``tests/test_pdb_metadata.py``'s own sibling tests for that wiring.

Duplicates the small ``_build_tpi_stream``/helpers it needs rather than
importing them, matching every other ``_extra``-style sibling test file in
this suite (imports the actual production symbols from
``abicheck.pdb_parser`` normally, only the private test-fixture builders
are duplicated).
"""

from __future__ import annotations

import struct

import pytest

from abicheck.pdb_parser import (
    LF_ARRAY,
    LF_ENUM,
    LF_FIELDLIST,
    LF_MEMBER,
    LF_STMEMBER,
    LF_STRUCTURE,
    LF_UNION,
    LF_VFUNCTAB,
    TypeDatabase,
    parse_tpi_stream,
)


def _cv_cstring(s: str) -> bytes:
    return s.encode("utf-8") + b"\x00"


def _make_lf_structure(
    count: int, prop: int, field_ti: int, byte_size: int, name: str
) -> bytes:
    """Build LF_STRUCTURE payload (16-byte header + numeric leaf + name)."""
    return (
        struct.pack("<HHIII", count, prop, field_ti, 0, 0)
        + struct.pack("<H", byte_size)
        + _cv_cstring(name)
    )


def _make_lf_fieldlist(members: list[bytes]) -> bytes:
    return b"".join(members)


def _build_tpi_stream(records: list[tuple[int, bytes]]) -> bytes:
    """Build a TPI stream from a list of (leaf_type, payload) tuples."""
    ti_begin = 0x1000
    ti_end = ti_begin + len(records)

    rec_data = b""
    for leaf, payload in records:
        rec_len = 2 + len(payload)
        rec_bytes = struct.pack("<HH", rec_len, leaf) + payload
        pad = (4 - (len(rec_bytes) % 4)) % 4
        rec_bytes += b"\x00" * pad
        rec_data += rec_bytes

    version = 20040203
    header_size = 56
    header = struct.pack(
        "<IIIII", version, header_size, ti_begin, ti_end, len(rec_data)
    )
    header += b"\x00" * (header_size - len(header))
    return header + rec_data


class TestTpiStreamTruncated:
    def test_record_length_runs_past_stream_bounds_is_truncated(self) -> None:
        """A record whose declared ``rec_len`` claims more bytes than the
        stream actually has (a cut-off/corrupt final record) must also be
        flagged, not silently accepted with a shortened payload."""
        ti_begin = 0x1000
        ti_end = 0x1001
        # rec_len claims 100 bytes of payload, but only 4 bytes follow.
        rec_data = struct.pack("<HH", 100, LF_FIELDLIST) + b"\x00" * 2
        header = struct.pack("<IIIII", 20040203, 56, ti_begin, ti_end, len(rec_data))
        header += b"\x00" * (56 - len(header))
        tpi = parse_tpi_stream(header + rec_data)
        assert len(tpi.records) == 0
        assert tpi.truncated is True

    def test_stream_ends_before_promised_type_index_is_truncated(self) -> None:
        """The TPI header promises more type indices (``ti_end``) than the
        stream's byte range can actually hold -- the loop's own ``while``
        condition ends it early with no explicit break, so this must be
        detected via the ``current_ti < ti_end`` postcondition."""
        records = [(LF_FIELDLIST, _make_lf_fieldlist([]))]
        tpi_data = bytearray(_build_tpi_stream(records))
        # Header ti_end is at offset 12 (version, header_size, ti_begin are
        # 4 bytes each); bump it to promise one more type than exists.
        (ti_end,) = struct.unpack_from("<I", tpi_data, 12)
        struct.pack_into("<I", tpi_data, 12, ti_end + 1)
        tpi = parse_tpi_stream(bytes(tpi_data))
        assert len(tpi.records) == 1  # the one real record is still parsed
        assert tpi.truncated is True

    def test_record_crossing_declared_type_boundary_is_truncated(self) -> None:
        """P2 review, fresh evidence beyond the resolved record-length/
        stream-bounds threads: a record whose declared ``rec_len`` fits
        within the whole buffer (``len(data)``) but crosses the header's
        own declared type-data boundary (``end``) must still be rejected.
        A PDB TPI stream can carry trailing hash/index substream bytes past
        its own type section -- checking only ``len(data)`` let those
        bytes be consumed as if they belonged to this record's own
        payload, which could let the loop reach ``ti_end`` and report
        ``truncated=False`` for a type section that never actually held
        that many well-formed records."""
        ti_begin = 0x1000
        ti_end = 0x1001
        header_size = 56
        rec_len = 20  # 2-byte leaf + 18-byte payload
        rec_bytes = (
            struct.pack("<H", rec_len)
            + struct.pack("<H", LF_FIELDLIST)
            + b"\x00" * (rec_len - 2)
        )
        type_bytes = 4  # header declares the type section ends well short of rec_bytes
        trailing = (
            b"\xaa" * 32
        )  # simulates a hash/index substream past the type section
        header = struct.pack(
            "<IIIII", 20040203, header_size, ti_begin, ti_end, type_bytes
        )
        header += b"\x00" * (header_size - len(header))
        tpi = parse_tpi_stream(header + rec_bytes + trailing)
        assert len(tpi.records) == 0
        assert tpi.truncated is True

    def test_fully_consumed_stream_is_not_truncated(self) -> None:
        """Positive control: a stream where every promised type index was
        actually parsed must not be flagged."""
        records = [
            (LF_FIELDLIST, _make_lf_fieldlist([])),
            (LF_FIELDLIST, _make_lf_fieldlist([])),
        ]
        tpi = parse_tpi_stream(_build_tpi_stream(records))
        assert len(tpi.records) == 2
        assert tpi.truncated is False


class TestTypeDatabaseFailedRecordCount:
    def test_starts_at_zero(self) -> None:
        tpi = parse_tpi_stream(
            _build_tpi_stream([(LF_STRUCTURE, _make_lf_structure(0, 0, 0, 4, "S"))])
        )
        db = TypeDatabase(tpi)
        db.parse_all()
        assert db.failed_record_count == 0

    def test_tracks_parse_record_exceptions(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``parse_all()``'s per-record ``except`` was previously only
        logged at debug level -- ``failed_record_count`` is the
        caller-visible signal. Exercises the counting logic directly via
        monkeypatching ``_parse_record`` (rather than hunting for a
        specific malformed byte pattern that happens to raise past every
        individual field parser's own length guards, which return early
        rather than raise -- see ``TestTypeDatabaseExtended``'s
        ``test_truncated_*_data`` tests in ``test_pdb_parser.py``), since
        the contract under test is "an exception here increments the
        counter," not any one parser's specific truncation behavior."""
        # A second record, so count == 1 not len(records).
        tpi_data = _build_tpi_stream(
            [
                (LF_STRUCTURE, _make_lf_structure(0, 0, 0, 4, "Good")),
                (LF_ENUM, b"\x00" * 4),
            ]
        )
        tpi = parse_tpi_stream(tpi_data)
        db = TypeDatabase(tpi)

        real_parse_record = db._parse_record

        def _flaky_parse_record(rec):  # noqa: ANN001, ANN202
            if rec.leaf == LF_ENUM:
                raise struct.error("simulated decode failure")
            return real_parse_record(rec)

        monkeypatch.setattr(db, "_parse_record", _flaky_parse_record)
        db.parse_all()
        assert db.failed_record_count == 1
        # The other, non-flaky record still parsed successfully.
        assert len(db.all_structs()) == 1

    def test_idempotent_across_parse_all_calls(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling parse_all() twice must not double-count (parse_all() is
        itself idempotent -- the second call is a no-op)."""
        tpi_data = _build_tpi_stream(
            [(LF_STRUCTURE, _make_lf_structure(0, 0, 0, 4, "S"))]
        )
        tpi = parse_tpi_stream(tpi_data)
        db = TypeDatabase(tpi)

        def _always_fails(rec):  # noqa: ANN001, ANN202
            raise struct.error("simulated")

        monkeypatch.setattr(db, "_parse_record", _always_fails)
        db.parse_all()
        assert db.failed_record_count == 1
        db.parse_all()  # second call, should be a no-op
        assert db.failed_record_count == 1


class TestFailedRecordCountNonExceptionTruncation:
    """P2 review, fresh evidence beyond the resolved TPI-truncation thread:
    a record with valid *outer* framing (a real ``TpiRecord`` the stream
    parsed fine) but a truncated *recognized* payload was still invisible
    to ``failed_record_count`` -- every individual ``_parse_*`` method
    returns early (``return``, ``break``) on a too-short payload without
    raising, so ``parse_all()``'s own ``except`` never fires for this
    shape either. Each ``_parse_*``/``_skip_subrecord`` now returns
    True/False instead of silently no-oping, propagated through
    ``_parse_record`` into the same ``failed_record_count``."""

    def _db(self, records: list[tuple[int, bytes]]) -> TypeDatabase:
        tpi = parse_tpi_stream(_build_tpi_stream(records))
        db = TypeDatabase(tpi)
        db.parse_all()
        return db

    def test_lf_structure_shorter_than_its_fixed_header(self) -> None:
        """The reviewer's own example: LF_STRUCTURE payload < 16 bytes."""
        db = self._db([(LF_STRUCTURE, b"\x00" * 4)])
        assert db.failed_record_count == 1
        assert len(db.all_structs()) == 0

    def test_lf_union_shorter_than_its_fixed_header(self) -> None:
        """LF_UNION shares _parse_struct's dispatch but its own, shorter
        (8-byte) header -- give it fewer than that."""
        db = self._db([(LF_UNION, b"\x00" * 4)])
        assert db.failed_record_count == 1

    def test_lf_enum_shorter_than_its_fixed_header(self) -> None:
        db = self._db([(LF_ENUM, b"\x00" * 4)])
        assert db.failed_record_count == 1
        assert len(db.all_enums()) == 0

    def test_fieldlist_with_truncated_sub_record(self) -> None:
        """The reviewer's other named example: ``_parse_fieldlist()``
        breaks normally on a truncated sub-record (LF_STMEMBER here, needs
        6 bytes, given only 2)."""
        data = struct.pack("<H", LF_STMEMBER) + b"\x00" * 2
        db = self._db([(LF_FIELDLIST, data)])
        assert db.failed_record_count == 1
        assert db.get_fieldlist(0x1000) == []

    def test_fieldlist_with_complete_data_is_not_counted(self) -> None:
        """Positive control: a fully consumed fieldlist (no sub-records at
        all -- 0 bytes is legitimately "empty," not truncated) must not be
        flagged."""
        db = self._db([(LF_FIELDLIST, b"")])
        assert db.failed_record_count == 0

    def test_lf_structure_with_unterminated_name(self) -> None:
        """P2 review, fresh evidence beyond the resolved non-exception-
        truncation thread: a fully-framed LF_STRUCTURE whose fixed header is
        complete but whose trailing name bytes have no NUL terminator (a
        truncated payload cut off mid-name) previously still reached
        ``return True`` -- ``_read_cstring`` silently returned an empty
        string with no signal a caller checked. The stored struct's name
        (still the best-effort decode) comes back empty, but the record is
        now correctly counted as failed."""
        payload = _make_lf_structure(0, 0, 0, 4, "S")[:-1]  # drop the NUL
        db = self._db([(LF_STRUCTURE, payload)])
        assert db.failed_record_count == 1
        structs = db.all_structs()
        assert len(structs) == 1
        assert structs[0x1000].name == ""

    def test_lf_enum_with_unterminated_name(self) -> None:
        """The reviewer's explicitly named sibling case: enum names."""
        payload = struct.pack("<HHII", 0, 0, 0, 0) + b"Color"  # no NUL
        db = self._db([(LF_ENUM, payload)])
        assert db.failed_record_count == 1
        assert len(db.all_enums()) == 1

    def test_lf_array_with_unterminated_name(self) -> None:
        """The reviewer's other explicitly named sibling case: array
        names."""
        payload = struct.pack("<II", 0, 0) + struct.pack("<H", 4) + b"Arr"
        db = self._db([(LF_ARRAY, payload)])
        assert db.failed_record_count == 1

    def test_lf_structure_with_terminated_name_is_not_counted(self) -> None:
        """Positive control: a properly NUL-terminated name (even the
        legitimately-empty-string case) must not be flagged."""
        db = self._db([(LF_STRUCTURE, _make_lf_structure(0, 0, 0, 4, "S"))])
        assert db.failed_record_count == 0
        assert db.all_structs()[0x1000].name == "S"

    def test_fieldlist_member_with_unterminated_name(self) -> None:
        """The unterminated-name gap also reaches fieldlist sub-records
        (LF_MEMBER), not just the four top-level name-bearing leaves."""
        member = (
            struct.pack("<H", LF_MEMBER)
            + struct.pack("<HI", 0, 0)  # attr, type_ti
            + struct.pack("<H", 0)  # numeric leaf (offset)
            + b"field"  # no NUL terminator
        )
        db = self._db([(LF_FIELDLIST, member)])
        assert db.failed_record_count == 1

    def test_lf_vfunctab_shorter_than_its_fixed_header(self) -> None:
        """Latent bug this same fix closed: LF_VFUNCTAB previously had no
        bounds check in ``_skip_subrecord`` at all -- it returned
        ``pos + 6`` unconditionally, which could exceed the fieldlist's own
        length and simply end the parse loop on the next iteration's
        ``while`` condition, silently truncating with no signal
        whatsoever (not even a False return, since the caller's ``new_pos
        is None`` check never saw a value past the fieldlist's bounds)."""
        # sub_leaf(2) consumed by the fieldlist loop before dispatch, then
        # _skip_subrecord needs 6 more bytes for LF_VFUNCTAB's own
        # padding(2)+type_ti(4) -- give it only 2.
        data = struct.pack("<H", LF_VFUNCTAB) + b"\x00" * 2
        db = self._db([(LF_FIELDLIST, data)])
        assert db.failed_record_count == 1
        assert db.get_fieldlist(0x1000) == []
