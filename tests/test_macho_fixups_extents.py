"""Fixup slots must lie inside the segment their opcode names (classic streams),
and chained-fixup headers must be complete (abicheck/extract/macho_fixups.py).

Oracles are hand-encoded opcode and header bytes, not the decoder's helpers.
"""

from __future__ import annotations

import struct

from abicheck.extract.macho_fixups import (
    decode_chained_fixup_addresses,
    fixup_addresses,
)

# Two adjacent 0x1000-byte segments, at 0x1000 and 0x2000.
ADJACENT = [(0x1000, 0x1000), (0x2000, 0x1000)]
_SET_SEGMENT_AND_OFFSET_ULEB = 0x20
_ADD_ADDR_ULEB = 0x30
_DO_REBASE_IMM_TIMES = 0x51
_DONE = 0x00


def test_slot_offset_past_its_segment_is_undecodable_not_the_next_segment() -> None:
    # SET(seg 0, offset 0x1000) puts the slot one page past segment 0's end.
    # It is the first byte of segment 1 in memory, but it is not a fixup of
    # segment 0: the stream is undecodable, not a count against segment 1.
    stream = bytes(
        [_SET_SEGMENT_AND_OFFSET_ULEB, 0x80, 0x20, _DO_REBASE_IMM_TIMES, _DONE]
    )
    image = stream
    assert (
        fixup_addresses(image, ADJACENT, 8, rebase=(0, len(image)), binds=[], limit=10)
        is None
    )


def test_slot_straddling_the_segment_end_is_undecodable() -> None:
    # An 8-byte slot at offset 0xFFC of a 0x1000-byte segment runs 4 bytes past
    # its end. The slot is not wholly inside the segment.
    # ULEB 0xFFC: low 7 bits 0x7C with a continuation, then 0x1F << 7.
    stream = bytes(
        [_SET_SEGMENT_AND_OFFSET_ULEB, 0xFC, 0x1F, _DO_REBASE_IMM_TIMES, _DONE]
    )
    assert (
        fixup_addresses(
            stream, ADJACENT, 8, rebase=(0, len(stream)), binds=[], limit=10
        )
        is None
    )


def test_slot_wholly_inside_its_segment_still_decodes() -> None:
    # Negative control: the last whole 8-byte slot of segment 0 is at 0xFF8.
    stream = bytes(
        [_SET_SEGMENT_AND_OFFSET_ULEB, 0xF8, 0x1F, _DO_REBASE_IMM_TIMES, _DONE]
    )
    got = fixup_addresses(
        stream, ADJACENT, 8, rebase=(0, len(stream)), binds=[], limit=10
    )
    assert got == frozenset({0x1000 + 0xFF8})


def test_chained_header_shorter_than_28_bytes_is_undecodable() -> None:
    # A 12-byte blob: the starts offset reads, but the header is incomplete.
    blob = (0).to_bytes(4, "little") + (8).to_bytes(4, "little") + bytes(4)
    got = decode_chained_fixup_addresses(
        blob, 0, len(blob), [(0x1000, 0, 0x1000)], 0x1000, limit=10
    )
    assert got is None


def test_chained_starts_table_inside_the_header_is_undecodable() -> None:
    # starts_offset 8 points into the header itself, not past it.
    blob = bytearray(bytes(40))
    struct.pack_into("<I", blob, 4, 8)
    got = decode_chained_fixup_addresses(
        bytes(blob), 0, len(blob), [(0x1000, 0, 0x1000)], 0x1000, limit=10
    )
    assert got is None


def test_chained_segment_count_past_the_images_segments_is_refused() -> None:
    # The starts table names 1000 segments; the image has one. Refused before
    # any page is iterated, even with no chain to walk.
    starts_off = 28
    blob = bytearray(28 + 4 + 4 * 1000)
    struct.pack_into("<I", blob, 4, starts_off)
    struct.pack_into("<I", blob, starts_off, 1000)
    got = decode_chained_fixup_addresses(
        bytes(blob), 0, len(blob), [(0x1000, 0, 0x1000)], 0x1000, limit=10
    )
    assert got is None
