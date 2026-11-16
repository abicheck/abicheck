"""Every classic rebase/bind opcode, and every malformed-chained-blob refusal.

Platform-independent: the streams and blobs are hand-encoded here (classic
opcodes as dyld's ``mach-o/fixup-chains`` and ``mach-o/loader.h`` define them)
or built by the synthetic chained-fixups writer, so the decoder runs on Linux
against bytes whose truth does not depend on the decoder's own helpers.

Two adjacent 8-byte-pointer segments are used throughout: ``0x1000..0x2000``
(segment 0) and ``0x2000..0x3000`` (segment 1).
"""

from __future__ import annotations

import struct

import pytest

from abicheck.extract.macho_fixups import (
    FixupDecodeError,
    decode_bind_addresses,
    decode_chained_fixup_addresses,
    decode_rebase_addresses,
)
from tests._macho_synthetic import chained_image

SEGMENTS = [(0x1000, 0x1000), (0x2000, 0x1000)]
PTR = 8
LIMIT = 10

# Classic opcodes (high nibble), spelled out here rather than imported.
SET_SEG_OFF = 0x20  # SET_SEGMENT_AND_OFFSET_ULEB, segment in the low nibble
DO_TIMES_1 = 0x51  # REBASE_DO_IMM_TIMES, count 1
DONE = 0x00


def _rebase(stream: bytes) -> list[int]:
    return sorted(decode_rebase_addresses(stream, SEGMENTS, PTR, LIMIT))


def _bind(stream: bytes) -> list[int]:
    return sorted(decode_bind_addresses(stream, SEGMENTS, PTR, LIMIT))


# --- classic rebase opcodes -------------------------------------------------


def test_set_type_imm_is_a_no_op_between_fixups() -> None:
    # REBASE_SET_TYPE_IMM (0x11) names a pointer type; it moves no offset.
    stream = bytes([SET_SEG_OFF, 0x00, 0x11, DO_TIMES_1, DONE])
    assert _rebase(stream) == [0x1000]


def test_add_addr_imm_scaled_advances_by_imm_pointers() -> None:
    # ADD_ADDR_IMM_SCALED (0x42): offset += 2 * 8 = 16 before the rebase.
    stream = bytes([SET_SEG_OFF, 0x00, 0x42, DO_TIMES_1, DONE])
    assert _rebase(stream) == [0x1010]


def test_do_add_addr_uleb_rebases_then_skips_pointer_plus_addend() -> None:
    # DO_ADD_ADDR_ULEB (0x70, ULEB 0x10): rebase 0x1000, then offset is
    # 0x1000 + 8 + 0x10 = 0x1018 for the next rebase.
    stream = bytes([SET_SEG_OFF, 0x00, 0x70, 0x10, DO_TIMES_1, DONE])
    assert _rebase(stream) == [0x1000, 0x1018]


def test_do_uleb_times_skipping_uleb_steps_by_pointer_plus_skip() -> None:
    # DO_ULEB_TIMES_SKIPPING_ULEB (0x80, count 2, skip 8): 0x1000 and
    # 0x1000 + 8 + 8 = 0x1010.
    stream = bytes([SET_SEG_OFF, 0x00, 0x80, 0x02, 0x08, DONE])
    assert _rebase(stream) == [0x1000, 0x1010]


def test_unknown_rebase_opcode_is_undecodable() -> None:
    with pytest.raises(FixupDecodeError, match="unknown rebase opcode 0x90"):
        decode_rebase_addresses(bytes([0x90]), SEGMENTS, PTR, LIMIT)


def test_uleb_wider_than_64_bits_is_refused_by_width_not_value() -> None:
    # Ten continuation bytes carry no value bits: the operand runs past the
    # 64-bit width before it terminates, so it is refused on width alone.
    stream = bytes([SET_SEG_OFF]) + bytes([0x80] * 10) + bytes([0x00])
    with pytest.raises(FixupDecodeError, match="wider than 64 bits"):
        decode_rebase_addresses(stream, SEGMENTS, PTR, LIMIT)


# --- classic bind opcodes ---------------------------------------------------


def test_set_dylib_ordinal_uleb_reads_and_discards_its_operand() -> None:
    # SET_DYLIB_ORDINAL_ULEB (0x20, ULEB 1): the import ordinal is not a slot.
    stream = bytes([0x20, 0x01, SET_SEG_OFF, 0x00, 0x90, DONE])
    assert _bind(stream) == [0x1000]


def test_set_dylib_special_imm_is_a_no_op() -> None:
    stream = bytes([0x30, SET_SEG_OFF, 0x00, 0x90, DONE])
    assert _bind(stream) == [0x1000]


def test_unterminated_symbol_name_is_undecodable() -> None:
    # SET_SYMBOL_TRAILING_FLAGS (0x40) needs its NUL terminator.
    with pytest.raises(FixupDecodeError, match="symbol name runs past"):
        decode_bind_addresses(bytes([0x40, 0x61, 0x62]), SEGMENTS, PTR, LIMIT)


def test_terminated_symbol_name_is_skipped_to_its_nul() -> None:
    stream = bytes([0x40, 0x61, 0x00, SET_SEG_OFF, 0x00, 0x90, DONE])
    assert _bind(stream) == [0x1000]


def test_bind_add_addr_uleb_moves_the_offset_before_the_bind() -> None:
    # BIND_ADD_ADDR_ULEB (0x80, ULEB 8): offset 0 -> 8, bind at 0x1008.
    stream = bytes([SET_SEG_OFF, 0x00, 0x80, 0x08, 0x90, DONE])
    assert _bind(stream) == [0x1008]


def test_do_bind_add_addr_imm_scaled_steps_by_imm_pointers() -> None:
    # DO_BIND_ADD_ADDR_IMM_SCALED (0xB1): bind at 0x1000, then offset is
    # 0 + 8 + 1 * 8 = 16, so the next bind lands at 0x1010.
    stream = bytes([SET_SEG_OFF, 0x00, 0xB1, 0x90, DONE])
    assert _bind(stream) == [0x1000, 0x1010]


def test_do_bind_uleb_times_skipping_uleb_steps_by_pointer_plus_skip() -> None:
    # DO_BIND_ULEB_TIMES_SKIPPING_ULEB (0xC0, count 2, skip 8).
    stream = bytes([SET_SEG_OFF, 0x00, 0xC0, 0x02, 0x08, DONE])
    assert _bind(stream) == [0x1000, 0x1010]


def test_unknown_bind_opcode_is_undecodable() -> None:
    with pytest.raises(FixupDecodeError, match="unknown bind opcode 0xd0"):
        decode_bind_addresses(bytes([0xD0]), SEGMENTS, PTR, LIMIT)


def test_addend_sleb_terminated_is_skipped() -> None:
    # SET_ADDEND_SLEB (0x60) with a one-byte SLEB 0x7f (no continuation bit).
    stream = bytes([SET_SEG_OFF, 0x00, 0x60, 0x7F, 0x90, DONE])
    assert _bind(stream) == [0x1000]


def test_addend_sleb_running_past_the_stream_is_undecodable() -> None:
    # A continuation byte at the very end: the SLEB never terminates.
    with pytest.raises(FixupDecodeError, match="SLEB128 runs past"):
        decode_bind_addresses(bytes([0x60, 0x80]), SEGMENTS, PTR, LIMIT)


# --- chained-fixups blob refusals -------------------------------------------

# One data segment at 0x2000 whose file bytes follow the blob, one page.
GROUPS = [("__DATA", 0x2000, 0x3000)]
SLOTS = [0x2000, 0x2008]
# dyld_chained_fixups_header: starts_offset at byte 4.
STARTS_OFF_FIELD = 4


def _chained(mutate=None, *, image_cut: int | None = None, segments=None):
    """Decode a chained blob built for SLOTS, after an optional byte mutation.

    Returns ``(addresses, blob_size)``. *image_cut* truncates the image's
    segment bytes; *segments* overrides the image's segment table.
    """
    blob, memory = chained_image(GROUPS, SLOTS)
    blob = bytearray(blob)
    if mutate is not None:
        mutate(blob)
    fileoff = len(blob)
    seg_bytes = bytearray(0x1000)
    for addr, word in memory.items():
        seg_bytes[addr - 0x2000 : addr - 0x2000 + 8] = word.to_bytes(8, "little")
    if image_cut is not None:
        seg_bytes = seg_bytes[:image_cut]
    image = bytes(blob) + bytes(seg_bytes)
    if segments is None:
        segments = [(0x2000, fileoff, 0x1000)]
    got = decode_chained_fixup_addresses(
        image, 0, len(blob), segments, 0x2000, limit=LIMIT
    )
    return got, len(blob)


def test_chained_happy_path_decodes_both_slots() -> None:
    # Negative control: the same builder, unmutated, decodes exactly the slots.
    got, _ = _chained()
    assert got == frozenset({0x2000, 0x2008})


def test_starts_table_extending_past_the_blob_is_undecodable() -> None:
    # Cut the blob right after the starts count: its one entry is missing.
    blob, _ = chained_image(GROUPS, SLOTS)
    starts_off = struct.unpack_from("<I", blob, STARTS_OFF_FIELD)[0]
    got = decode_chained_fixup_addresses(
        blob[: starts_off + 4],
        0,
        starts_off + 4,
        [(0x2000, 0, 0x1000)],
        0x2000,
        limit=LIMIT,
    )
    assert got is None


def test_segment_info_offset_past_the_blob_is_undecodable() -> None:
    # The first per-segment offset (after the header and count) points far past
    # the blob, so the segment's starts header cannot be read.
    def mutate(blob: bytearray) -> None:
        starts_off = struct.unpack_from("<I", blob, STARTS_OFF_FIELD)[0]
        struct.pack_into("<I", blob, starts_off + 4, 0xFFFF)

    got, _ = _chained(mutate)
    assert got is None


def test_segment_size_past_the_blob_is_undecodable() -> None:
    def mutate(blob: bytearray) -> None:
        starts_off = struct.unpack_from("<I", blob, STARTS_OFF_FIELD)[0]
        base = starts_off + struct.unpack_from("<I", blob, starts_off + 4)[0]
        struct.pack_into("<I", blob, base, 0x1000)  # size field

    got, _ = _chained(mutate)
    assert got is None


def test_segment_size_smaller_than_its_header_is_undecodable() -> None:
    def mutate(blob: bytearray) -> None:
        starts_off = struct.unpack_from("<I", blob, STARTS_OFF_FIELD)[0]
        base = starts_off + struct.unpack_from("<I", blob, starts_off + 4)[0]
        struct.pack_into("<I", blob, base, 4)  # shorter than the 22-byte header

    got, _ = _chained(mutate)
    assert got is None


def test_page_count_past_the_starts_array_is_undecodable() -> None:
    def mutate(blob: bytearray) -> None:
        starts_off = struct.unpack_from("<I", blob, STARTS_OFF_FIELD)[0]
        base = starts_off + struct.unpack_from("<I", blob, starts_off + 4)[0]
        struct.pack_into("<H", blob, base + 20, 0xFFFF)  # page_count

    got, _ = _chained(mutate)
    assert got is None


def test_multi_overflow_index_past_the_array_is_undecodable() -> None:
    # The page start carries MULTI with an overflow index no array holds.
    def mutate(blob: bytearray) -> None:
        starts_off = struct.unpack_from("<I", blob, STARTS_OFF_FIELD)[0]
        base = starts_off + struct.unpack_from("<I", blob, starts_off + 4)[0]
        # 0x8050: MULTI with overflow index 0x50, far past the one-entry array.
        # (0xFFFF would be the NONE sentinel, not an overflow reference.)
        struct.pack_into("<H", blob, base + 22, 0x8050)

    got, _ = _chained(mutate)
    assert got is None


def test_chain_slot_cut_short_in_the_file_is_undecodable() -> None:
    # The segment's file bytes end 4 bytes into the second slot (0x2008): the
    # slot is not wholly in the file, so the chain cannot be read.
    got, _ = _chained(image_cut=8 + 4)
    assert got is None


def test_chain_slot_outside_every_segment_is_undecodable() -> None:
    # The chain's page is at 0x2000, but the only segment maps 0x9000.
    got, _ = _chained(segments=[(0x9000, 0, 0x1000)])
    assert got is None
