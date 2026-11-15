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

BASES = [0x1000, 0x2000]  # segment 0 at 0x1000, segment 1 at 0x2000


def test_rebase_set_offset_then_do_once() -> None:
    # SET_SEGMENT_AND_OFFSET(seg 0, offset 0x10); DO_REBASE_IMM_TIMES(1); DONE
    stream = bytes([0x20, 0x10, 0x51, 0x00])
    assert decode_rebase_addresses(stream, BASES, 8) == [0x1010]


def test_rebase_do_uleb_times_advances_by_pointer_size() -> None:
    # SET(seg 1, offset 0x0); DO_ULEB_TIMES with count ULEB 3; DONE -> three words
    stream = bytes([0x21, 0x00, 0x60, 0x03, 0x00])
    assert decode_rebase_addresses(stream, BASES, 8) == [0x2000, 0x2008, 0x2010]


def test_rebase_pointer_width_follows_the_slice() -> None:
    stream = bytes([0x20, 0x00, 0x52, 0x00])  # two slots
    assert decode_rebase_addresses(stream, BASES, 4) == [0x1000, 0x1004]


@pytest.mark.parametrize(
    "stream",
    [
        bytes([0x20, 0x00, 0x51]),  # no DONE, but the stream just ends: fine
    ],
)
def test_rebase_stream_without_done_is_still_read(stream: bytes) -> None:
    assert decode_rebase_addresses(stream, BASES, 8) == [0x1000]


def test_bind_do_bind_names_the_bound_slot() -> None:
    # SET_DYLIB_ORDINAL_IMM(1); SET_SYMBOL "_x"; SET_SEGMENT_AND_OFFSET(seg 0, 0x8);
    # DO_BIND; DONE
    stream = bytes([0x11, 0x40]) + b"_x\x00" + bytes([0x70 | 0x0, 0x08, 0x90, 0x00])
    assert decode_bind_addresses(stream, BASES, 8) == [0x1008]


def test_bind_add_addr_then_do_bind_skips_one_word() -> None:
    # SET(seg 0, 0x0); DO_BIND_ADD_ADDR_ULEB(0x8) -> bind at 0x0, next slot at 0x10
    stream = bytes([0x70, 0x00, 0xA0, 0x08, 0x90, 0x00])
    assert decode_bind_addresses(stream, BASES, 8) == [0x1000, 0x1010]


def test_unknown_opcode_yields_no_fixup_list() -> None:
    # 0xF0 is not a rebase opcode: the stream is undecodable -> None, never "no fixups".
    image = bytes([0xF0, 0x00])
    assert fixup_addresses(image, BASES, 8, rebase=(0, 2), binds=[]) is None


def test_truncated_uleb_is_undecodable() -> None:
    image = bytes([0x20, 0x80])  # offset ULEB continues past the stream end
    assert fixup_addresses(image, BASES, 8, rebase=(0, 2), binds=[]) is None


def test_segment_index_outside_the_image_is_undecodable() -> None:
    image = bytes([0x25, 0x00, 0x51, 0x00])  # segment 5 does not exist
    with pytest.raises(FixupDecodeError):
        decode_rebase_addresses(image, BASES, 8)
    assert fixup_addresses(image, BASES, 8, rebase=(0, 4), binds=[]) is None


def test_fixup_addresses_combines_rebase_and_bind_streams() -> None:
    rebase = bytes([0x20, 0x00, 0x51, 0x00])  # rebase at 0x1000
    bind = bytes([0x70, 0x08, 0x90, 0x00])  # bind at 0x1008 (segment 0)
    image = rebase + bind
    got = fixup_addresses(
        image, BASES, 8, rebase=(0, len(rebase)), binds=[(len(rebase), len(bind))]
    )
    assert got == frozenset({0x1000, 0x1008})
