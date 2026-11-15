"""Classic dyld rebase/bind opcode decoding (abicheck/extract/macho_fixups.py).

Every oracle is hand-encoded opcode bytes and the addresses those opcodes name,
not the decoder's own helper output.
"""

from __future__ import annotations

import pytest

from abicheck.extract.macho_fixups import (
    FixupDecodeError,
    decode_bind_addresses,
    decode_rebase_addresses,
    fixup_addresses,
)

# (vmaddr, vmsize) per segment: segment 0 at 0x1000, segment 1 at 0x2000. The
# size is large enough that every offset these oracles use lies inside it.
BASES = [(0x1000, 0x100000), (0x2000, 0x100000)]


def test_rebase_set_offset_then_do_once() -> None:
    # SET_SEGMENT_AND_OFFSET(seg 0, offset 0x10); DO_REBASE_IMM_TIMES(1); DONE
    stream = bytes([0x20, 0x10, 0x51, 0x00])
    assert decode_rebase_addresses(stream, BASES, 8, 1000) == [0x1010]


def test_rebase_do_uleb_times_advances_by_pointer_size() -> None:
    # SET(seg 1, offset 0x0); DO_ULEB_TIMES with count ULEB 3; DONE -> three words
    stream = bytes([0x21, 0x00, 0x60, 0x03, 0x00])
    assert decode_rebase_addresses(stream, BASES, 8, 1000) == [0x2000, 0x2008, 0x2010]


def test_rebase_pointer_width_follows_the_slice() -> None:
    stream = bytes([0x20, 0x00, 0x52, 0x00])  # two slots
    assert decode_rebase_addresses(stream, BASES, 4, 1000) == [0x1000, 0x1004]


@pytest.mark.parametrize(
    "stream",
    [
        bytes([0x20, 0x00, 0x51]),  # no DONE, but the stream just ends: fine
    ],
)
def test_rebase_stream_without_done_is_still_read(stream: bytes) -> None:
    assert decode_rebase_addresses(stream, BASES, 8, 1000) == [0x1000]


def test_bind_do_bind_names_the_bound_slot() -> None:
    # SET_DYLIB_ORDINAL_IMM(1); SET_SYMBOL "_x"; SET_SEGMENT_AND_OFFSET(seg 0, 0x8);
    # DO_BIND; DONE
    stream = bytes([0x11, 0x40]) + b"_x\x00" + bytes([0x70 | 0x0, 0x08, 0x90, 0x00])
    assert decode_bind_addresses(stream, BASES, 8, 1000) == [0x1008]


def test_bind_add_addr_then_do_bind_skips_one_word() -> None:
    # SET(seg 0, 0x0); DO_BIND_ADD_ADDR_ULEB(0x8) -> bind at 0x0, next slot at 0x10
    stream = bytes([0x70, 0x00, 0xA0, 0x08, 0x90, 0x00])
    assert decode_bind_addresses(stream, BASES, 8, 1000) == [0x1000, 0x1010]


def test_unknown_opcode_yields_no_fixup_list() -> None:
    # 0xF0 is not a rebase opcode: the stream is undecodable -> None, never "no fixups".
    image = bytes([0xF0, 0x00])
    assert fixup_addresses(image, BASES, 8, rebase=(0, 2), binds=[], limit=1000) is None


def test_truncated_uleb_is_undecodable() -> None:
    image = bytes([0x20, 0x80])  # offset ULEB continues past the stream end
    assert fixup_addresses(image, BASES, 8, rebase=(0, 2), binds=[], limit=1000) is None


def test_segment_index_outside_the_image_is_undecodable() -> None:
    image = bytes([0x25, 0x00, 0x51, 0x00])  # segment 5 does not exist
    with pytest.raises(FixupDecodeError):
        decode_rebase_addresses(image, BASES, 8, 1000)
    assert fixup_addresses(image, BASES, 8, rebase=(0, 4), binds=[], limit=1000) is None


def test_fixup_addresses_combines_rebase_and_bind_streams() -> None:
    rebase = bytes([0x20, 0x00, 0x51, 0x00])  # rebase at 0x1000
    bind = bytes([0x70, 0x08, 0x90, 0x00])  # bind at 0x1008 (segment 0)
    image = rebase + bind
    got = fixup_addresses(
        image,
        BASES,
        8,
        rebase=(0, len(rebase)),
        binds=[(len(rebase), len(bind))],
        limit=1000,
    )
    assert got == frozenset({0x1000, 0x1008})


# ── chained fixups (LC_DYLD_CHAINED_FIXUPS) ────────────────────────────────

from abicheck.extract.macho_fixups import decode_chained_fixup_addresses  # noqa: E402
from tests._macho_synthetic import chained_image  # noqa: E402

_TEXT_BASE = 0x1000
_SEG_VM = 0x1000
_SEG_SIZE = 0x4000  # four 4 KiB pages


def _chained_case(groups, slots, *, pointer_format=2):
    """The blob and the image bytes it reads from, for one data segment.

    The blob sits at offset 0; the segment's bytes follow it, so a pointer at
    address A is read from ``fileoff + (A - vmaddr)``.
    """
    blob, memory = chained_image(groups, slots, pointer_format=pointer_format)
    fileoff = len(blob)
    image = bytearray(blob) + bytearray(_SEG_SIZE)
    for addr, word in memory.items():
        at = fileoff + (addr - _SEG_VM)
        image[at : at + 8] = word.to_bytes(8, "little")
    segments = [(_SEG_VM, fileoff, _SEG_SIZE)]
    return bytes(image), segments


def _decode(image, segments, blob_size, *, text_base=_TEXT_BASE):
    return decode_chained_fixup_addresses(
        image, 0, blob_size, segments, text_base, limit=1000
    )


_DATA = [("__DATA", _SEG_VM, _SEG_VM + _SEG_SIZE)]


def test_chained_multi_page_chains_and_a_page_with_no_starts() -> None:
    # Hand-written oracle. Page 0: a chain 0x1008 -> 0x1018 -> 0x1040.
    # Page 1 has no starts (none of its slots may appear). Page 2: 0x3100.
    slots = [0x1008, 0x1018, 0x1040, 0x3100]
    blob, _memory = chained_image(_DATA, slots)
    image, segments = _chained_case(_DATA, slots)
    got = _decode(image, segments, len(blob))
    assert got == frozenset({0x1008, 0x1018, 0x1040, 0x3100})


def test_chained_arm64e_uses_an_eight_byte_stride() -> None:
    # Format 1 (ARM64E): a link is next * 8 bytes. 0x1010 -> 0x1028 is two units.
    slots = [0x1010, 0x1028]
    image, segments = _chained_case(_DATA, slots, pointer_format=1)
    blob, _ = chained_image(_DATA, slots, pointer_format=1)
    assert _decode(image, segments, len(blob)) == frozenset({0x1010, 0x1028})


def test_chained_multi_start_page_is_walked_from_every_start() -> None:
    # Hand-encoded, independent of the builder. One page with two chains. The
    # page_start entry 0x8001 is MULTI and points at overflow index 1; the
    # overflow list is [0x0010, LAST|0x0200]. Chain A: 0x1010 -> 0x1018 (next 2
    # units of 4 bytes, the last slot holds 0). Chain B: 0x1200 alone.
    seg_vm = 0x1000
    page_starts = [0x8001, 0x0010, 0x8200]
    body = b"".join(v.to_bytes(2, "little") for v in page_starts)
    starts_in_segment = (
        (22 + len(body)).to_bytes(4, "little")
        + (0x1000).to_bytes(2, "little")
        + (2).to_bytes(2, "little")  # DYLD_CHAINED_PTR_64
        + (seg_vm - _TEXT_BASE).to_bytes(8, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(2, "little")  # one page
        + body
    )
    header = (0).to_bytes(4, "little") + (28).to_bytes(4, "little") + bytes(20)
    # starts_in_image: one segment, its table entry at 28 + 4 holds offset 8
    # (relative to the starts_in_image table at 28).
    starts_image = (1).to_bytes(4, "little") + (8).to_bytes(4, "little")
    blob = header + starts_image + starts_in_segment
    fileoff = len(blob)
    image = bytearray(blob) + bytearray(0x1000)
    image[fileoff + 0x010 : fileoff + 0x018] = (2 << 51).to_bytes(8, "little")
    image[fileoff + 0x018 : fileoff + 0x020] = bytes(8)
    image[fileoff + 0x200 : fileoff + 0x208] = bytes(8)
    got = decode_chained_fixup_addresses(
        bytes(image), 0, len(blob), [(seg_vm, fileoff, 0x1000)], _TEXT_BASE, limit=1000
    )
    assert got == frozenset({0x1010, 0x1018, 0x1200})


def test_unknown_pointer_format_is_undecodable_not_empty() -> None:
    image, segments = _chained_case(_DATA, [0x1008], pointer_format=2)
    blob, _ = chained_image(_DATA, [0x1008], pointer_format=2)
    corrupt = bytearray(image)
    # The format is the u16 at starts_in_segment + 6; the table starts at 28+8.
    fmt_at = 28 + 8 + 6
    corrupt[fmt_at : fmt_at + 2] = (5).to_bytes(
        2, "little"
    )  # DYLD_CHAINED_PTR_32_FIRMWARE
    assert _decode(bytes(corrupt), segments, len(blob)) is None


def test_chain_that_leaves_its_page_is_undecodable() -> None:
    blob, _ = chained_image(_DATA, [0x1008])
    fileoff = len(blob)
    image = bytearray(blob) + bytearray(_SEG_SIZE)
    # 0xFFF units * 4 bytes = 16 KiB ahead: far past the 4 KiB page.
    image[fileoff + 0x08 : fileoff + 0x10] = (0xFFF << 51).to_bytes(8, "little")
    assert _decode(bytes(image), [(_SEG_VM, fileoff, _SEG_SIZE)], len(blob)) is None


def test_truncated_blob_is_undecodable() -> None:
    image, segments = _chained_case(_DATA, [0x1008])
    assert (
        decode_chained_fixup_addresses(image, 0, 10, segments, _TEXT_BASE, limit=1000)
        is None
    )


def test_classic_range_past_the_image_is_undecodable_not_a_short_stream() -> None:
    # Declared 100 bytes, image holds 3: a short prefix must not decode.
    image = bytes([0x20, 0x00, 0x51])
    assert (
        fixup_addresses(image, BASES, 8, rebase=(0, 100), binds=[], limit=1000) is None
    )


def test_classic_stream_claiming_more_fixups_than_the_segments_hold_is_undecodable() -> (
    None
):
    # A 5-byte ULEB count of 2**32 rebases: refused before any address is built.
    stream = bytes([0x20, 0x00, 0x60]) + _uleb(1 << 32) + bytes([0x00])
    assert (
        fixup_addresses(stream, BASES, 8, rebase=(0, len(stream)), binds=[], limit=16)
        is None
    )


def _uleb(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def test_truncated_chained_starts_table_is_undecodable_not_no_fixups() -> None:
    # A 28-byte header claiming a starts table at 28 with a segment count the
    # blob cannot hold: unknown, never an empty fixup set.
    blob = bytes(4) + (28).to_bytes(4, "little") + bytes(20)
    assert (
        decode_chained_fixup_addresses(
            blob, 0, len(blob), [(0x1000, 0, 0x1000)], 0x1000, limit=1000
        )
        is None
    )


def test_rebase_uleb_wider_than_64_bits_is_undecodable_even_with_no_fixups() -> None:
    # REBASE_OPCODE_ADD_ADDR_ULEB whose operand runs ten 7-bit groups past 64
    # bits. dyld's result is a uint64_t, so the stream is refused before any
    # integer grows past it, and the refusal holds with a zero fixup limit too.
    operand = bytes([0x80] * 9) + bytes([0x7F])
    image = bytes([0x30]) + operand + bytes([0x00])
    assert (
        fixup_addresses(image, BASES, 8, rebase=(0, len(image)), binds=[], limit=0)
        is None
    )


def test_rebase_uleb_of_exactly_64_bits_still_decodes() -> None:
    # The sibling of the refusal above: the widest legal operand is read in full.
    value = (1 << 64) - 1
    encoded = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            encoded.append(byte | 0x80)
        else:
            encoded.append(byte)
            break
    assert len(encoded) == 10
    image = bytes([0x30]) + bytes(encoded) + bytes([0x00])
    # The operand is far past the segments, so the offset itself is not a
    # fixup; the point is that the decoder accepts the width and returns.
    result = fixup_addresses(
        image, BASES, 8, rebase=(0, len(image)), binds=[], limit=1000
    )
    assert result is not None and not result


def _multi_start_blob(overflow: list[int]) -> tuple[bytes, list[tuple[int, int, int]]]:
    """One page at 0x1000 whose MULTI start indexes an overflow list of chains.

    Chain offsets are page-relative; each chain is one slot at 0x1010 whose
    value is 0 (no next link), so it is walked once per overflow entry.
    """
    seg_vm = 0x1000
    page_starts = [0x8001, *overflow]
    body = b"".join(v.to_bytes(2, "little") for v in page_starts)
    starts_in_segment = (
        (22 + len(body)).to_bytes(4, "little")
        + (0x1000).to_bytes(2, "little")
        + (2).to_bytes(2, "little")  # DYLD_CHAINED_PTR_64
        + (seg_vm - _TEXT_BASE).to_bytes(8, "little")
        + (0).to_bytes(4, "little")
        + (1).to_bytes(2, "little")  # one page
        + body
    )
    header = (0).to_bytes(4, "little") + (28).to_bytes(4, "little") + bytes(20)
    starts_image = (1).to_bytes(4, "little") + (8).to_bytes(4, "little")
    blob = header + starts_image + starts_in_segment
    fileoff = len(blob)
    image = bytes(bytearray(blob) + bytearray(0x1000))
    return image, [(seg_vm, fileoff, 0x1000)]


def test_chain_named_by_repeated_overflow_entries_is_refused_not_rewalked() -> None:
    # Two overflow entries name the same chain (page offset 0x10). The chain has
    # one slot, so a legitimate decode reads one slot; the second entry would
    # read it again. With a slot budget of one the decoder refuses the image.
    image, segments = _multi_start_blob([0x0010, 0x0010 | 0x8000])
    got = decode_chained_fixup_addresses(
        image, 0, len(image) - 0x1000, segments, _TEXT_BASE, limit=1
    )
    assert got is None


def test_the_same_chain_named_once_decodes_under_the_same_budget() -> None:
    # Negative control: one overflow entry, one slot read, within a budget of one.
    image, segments = _multi_start_blob([0x0010 | 0x8000])
    got = decode_chained_fixup_addresses(
        image, 0, len(image) - 0x1000, segments, _TEXT_BASE, limit=1
    )
    assert got == frozenset({0x1010})
