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

"""Pointer fixup locations of a Mach-O image.

A pointer that the loader must rewrite (a *rebase*), resolve (a *bind*), or
link through (a *chained* fixup) is a fixup. Only the *locations* of fixups
are decoded: each names one pointer-sized slot. The count of fixups inside an
object is independent evidence of its layout (see ``corroborate_object_sizes``).

Two encodings are decoded:

* classic: the rebase and bind opcode streams of ``LC_DYLD_INFO_ONLY``;
* chained: the starts tables of ``LC_DYLD_CHAINED_FIXUPS``.

Every decoder returns ``None`` for input it cannot fully decode: a stream or
table that runs past its bytes, an unknown opcode or pointer format, a chain
that leaves its page, or more fixups than the image's segments can hold. ``None``
is "unknown", never "no fixups".
"""

from __future__ import annotations

import struct
from collections.abc import Sequence

_REBASE_DONE = 0x00
_REBASE_SET_TYPE_IMM = 0x10
_REBASE_SET_SEGMENT_AND_OFFSET_ULEB = 0x20
_REBASE_ADD_ADDR_ULEB = 0x30
_REBASE_ADD_ADDR_IMM_SCALED = 0x40
_REBASE_DO_IMM_TIMES = 0x50
_REBASE_DO_ULEB_TIMES = 0x60
_REBASE_DO_ADD_ADDR_ULEB = 0x70
_REBASE_DO_ULEB_TIMES_SKIPPING_ULEB = 0x80

_BIND_DONE = 0x00
_BIND_SET_DYLIB_ORDINAL_IMM = 0x10
_BIND_SET_DYLIB_ORDINAL_ULEB = 0x20
_BIND_SET_DYLIB_SPECIAL_IMM = 0x30
_BIND_SET_SYMBOL_TRAILING_FLAGS_IMM = 0x40
_BIND_SET_TYPE_IMM = 0x50
_BIND_SET_ADDEND_SLEB = 0x60
_BIND_SET_SEGMENT_AND_OFFSET_ULEB = 0x70
_BIND_ADD_ADDR_ULEB = 0x80
_BIND_DO_BIND = 0x90
_BIND_DO_BIND_ADD_ADDR_ULEB = 0xA0
_BIND_DO_BIND_ADD_ADDR_IMM_SCALED = 0xB0
_BIND_DO_BIND_ULEB_TIMES_SKIPPING_ULEB = 0xC0


class FixupDecodeError(ValueError):
    """An opcode stream this decoder does not understand, or that runs off its end."""


_ULEB_WIDTH = 64  # every operand is a 64-bit value (dyld's uint64_t result)


def _uleb(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise FixupDecodeError("ULEB128 runs past the end of the stream")
        if shift >= _ULEB_WIDTH:
            raise FixupDecodeError("ULEB128 operand is wider than 64 bits")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if value >> _ULEB_WIDTH:
            raise FixupDecodeError("ULEB128 operand is wider than 64 bits")
        if not byte & 0x80:
            return value, pos
        shift += 7


def _skip_sleb(data: bytes, pos: int) -> int:
    while True:
        if pos >= len(data):
            raise FixupDecodeError("SLEB128 runs past the end of the stream")
        byte = data[pos]
        pos += 1
        if not byte & 0x80:
            return pos


def _address(
    segments: Sequence[tuple[int, int]], seg: int, offset: int, pointer_size: int
) -> int:
    """The address of the pointer slot at *offset* in segment *seg*.

    The whole slot must lie inside that segment: an offset past its end (or a
    slot that straddles it) is not a fixup of this segment, and is undecodable
    rather than a count against some other segment's bytes.
    """
    if not 0 <= seg < len(segments):
        raise FixupDecodeError(f"segment index {seg} names no segment")
    base, size = segments[seg]
    if offset < 0 or offset + pointer_size > size:
        raise FixupDecodeError(
            f"fixup slot at offset 0x{offset:x} lies outside segment {seg}"
        )
    return base + offset


def _emit(out: list[int], addr: int, count: int, limit: int) -> None:
    """Append *count* copies stepping by nothing: one fixup per call, bounded."""
    if len(out) + count > limit:
        raise FixupDecodeError("more fixups than the image's segments can hold")
    out.extend([addr] * count)


def decode_rebase_addresses(
    data: bytes,
    segments: Sequence[tuple[int, int]],
    pointer_size: int,
    limit: int,
) -> list[int]:
    """Addresses of every rebased pointer slot in a classic rebase stream."""
    out: list[int] = []
    seg = 0
    offset = 0
    pos = 0
    while pos < len(data):
        op = data[pos]
        pos += 1
        code, imm = op & 0xF0, op & 0x0F
        if code == _REBASE_DONE:
            break
        if code == _REBASE_SET_TYPE_IMM:
            continue
        if code == _REBASE_SET_SEGMENT_AND_OFFSET_ULEB:
            seg = imm
            offset, pos = _uleb(data, pos)
        elif code == _REBASE_ADD_ADDR_ULEB:
            value, pos = _uleb(data, pos)
            offset += value
        elif code == _REBASE_ADD_ADDR_IMM_SCALED:
            offset += imm * pointer_size
        elif code == _REBASE_DO_IMM_TIMES:
            for _ in range(imm):
                _emit(out, _address(segments, seg, offset, pointer_size), 1, limit)
                offset += pointer_size
        elif code == _REBASE_DO_ULEB_TIMES:
            count, pos = _uleb(data, pos)
            for _ in range(count):
                _emit(out, _address(segments, seg, offset, pointer_size), 1, limit)
                offset += pointer_size
        elif code == _REBASE_DO_ADD_ADDR_ULEB:
            _emit(out, _address(segments, seg, offset, pointer_size), 1, limit)
            value, pos = _uleb(data, pos)
            offset += pointer_size + value
        elif code == _REBASE_DO_ULEB_TIMES_SKIPPING_ULEB:
            count, pos = _uleb(data, pos)
            skip, pos = _uleb(data, pos)
            for _ in range(count):
                _emit(out, _address(segments, seg, offset, pointer_size), 1, limit)
                offset += pointer_size + skip
        else:
            raise FixupDecodeError(f"unknown rebase opcode 0x{op:02x}")
    return out


def decode_bind_addresses(
    data: bytes,
    segments: Sequence[tuple[int, int]],
    pointer_size: int,
    limit: int,
) -> list[int]:
    """Addresses of every bound pointer slot in a classic (or weak) bind stream."""
    out: list[int] = []
    seg = 0
    offset = 0
    pos = 0
    while pos < len(data):
        op = data[pos]
        pos += 1
        code, imm = op & 0xF0, op & 0x0F
        if code == _BIND_DONE:
            break
        if code in (_BIND_SET_DYLIB_ORDINAL_IMM, _BIND_SET_TYPE_IMM):
            continue
        if code == _BIND_SET_DYLIB_ORDINAL_ULEB:
            _, pos = _uleb(data, pos)
        elif code == _BIND_SET_DYLIB_SPECIAL_IMM:
            continue
        elif code == _BIND_SET_SYMBOL_TRAILING_FLAGS_IMM:
            end = data.find(b"\x00", pos)
            if end < 0:
                raise FixupDecodeError("symbol name runs past the end of the stream")
            pos = end + 1
        elif code == _BIND_SET_ADDEND_SLEB:
            pos = _skip_sleb(data, pos)
        elif code == _BIND_SET_SEGMENT_AND_OFFSET_ULEB:
            seg = imm
            offset, pos = _uleb(data, pos)
        elif code == _BIND_ADD_ADDR_ULEB:
            value, pos = _uleb(data, pos)
            offset += value
        elif code == _BIND_DO_BIND:
            _emit(out, _address(segments, seg, offset, pointer_size), 1, limit)
            offset += pointer_size
        elif code == _BIND_DO_BIND_ADD_ADDR_ULEB:
            _emit(out, _address(segments, seg, offset, pointer_size), 1, limit)
            value, pos = _uleb(data, pos)
            offset += pointer_size + value
        elif code == _BIND_DO_BIND_ADD_ADDR_IMM_SCALED:
            _emit(out, _address(segments, seg, offset, pointer_size), 1, limit)
            offset += pointer_size + imm * pointer_size
        elif code == _BIND_DO_BIND_ULEB_TIMES_SKIPPING_ULEB:
            count, pos = _uleb(data, pos)
            skip, pos = _uleb(data, pos)
            for _ in range(count):
                _emit(out, _address(segments, seg, offset, pointer_size), 1, limit)
                offset += pointer_size + skip
        else:
            raise FixupDecodeError(f"unknown bind opcode 0x{op:02x}")
    return out


def fixup_addresses(
    image: bytes,
    segments: Sequence[tuple[int, int]],
    pointer_size: int,
    *,
    rebase: tuple[int, int] | None,
    binds: Sequence[tuple[int, int]],
    limit: int,
) -> frozenset[int] | None:
    """Every rebase and bind slot address, or ``None`` if any stream is undecodable.

    *segments* is each segment's ``(vmaddr, vmsize)``, in load-command order:
    a slot must lie wholly inside the segment its opcode names.
    *rebase* and *binds* are ``(file_offset, size)`` pairs relative to the start
    of the image slice. A range that runs past the image's bytes is undecodable,
    not a shorter stream. *limit* bounds the fixup count (the image's segment
    bytes divided by the pointer size): a stream that claims more is undecodable.
    """
    ranges = [r for r in ([rebase] if rebase else []) + list(binds) if r[1]]
    if any(off + size > len(image) for off, size in ranges):
        return None
    addresses: list[int] = []
    try:
        if rebase is not None and rebase[1]:
            stream = image[rebase[0] : rebase[0] + rebase[1]]
            addresses += decode_rebase_addresses(stream, segments, pointer_size, limit)
        for off, size in binds:
            if size:
                stream = image[off : off + size]
                addresses += decode_bind_addresses(
                    stream, segments, pointer_size, limit - len(addresses)
                )
    except FixupDecodeError:
        return None
    return frozenset(addresses)


#: Bytes in dyld_chained_fixups_header (seven 32-bit fields).
_CHAINED_HEADER_SIZE = 28

#: DYLD_CHAINED_PTR_* formats this decoder walks, with the pointer stride and the
#: (shift, width) of the ``next`` field in an 8-byte chained pointer. A chain
#: link is ``next * stride`` bytes forward; ``next == 0`` ends the chain. The bit
#: layouts follow dyld's ``fixup-chains.h``: PTR_64 and PTR_64_OFFSET carry a
#: 12-bit next at bit 51 with a 4-byte stride; the ARM64E family carries an
#: 11-bit next at bit 51 with an 8-byte stride. Any other format is unknown.
_CHAINED_FORMATS: dict[int, tuple[int, int, int]] = {
    1: (8, 51, 11),  # DYLD_CHAINED_PTR_ARM64E
    2: (4, 51, 12),  # DYLD_CHAINED_PTR_64
    6: (4, 51, 12),  # DYLD_CHAINED_PTR_64_OFFSET
    9: (8, 51, 11),  # DYLD_CHAINED_PTR_ARM64E_USERLAND
    12: (8, 51, 11),  # DYLD_CHAINED_PTR_ARM64E_USERLAND24
}
_CHAINED_START_NONE = 0xFFFF
_CHAINED_START_MULTI = 0x8000
_CHAINED_START_LAST = 0x8000
_CHAINED_OFFSET_MASK = 0x7FFF
_STARTS_IN_SEGMENT_HEADER = (
    22  # size u32, page_size u16, format u16, offset u64, max u32, count u16
)


def _read_vm(
    image: bytes, segments: Sequence[tuple[int, int, int]], addr: int, size: int
) -> int | None:
    """Little-endian value of *size* bytes at virtual address *addr*, or None.

    *segments* are ``(vmaddr, fileoff, filesize)``; an address the file does
    not hold (zero-fill, or outside every segment) reads as None.
    """
    for vmaddr, fileoff, filesize in segments:
        if vmaddr <= addr and addr + size <= vmaddr + filesize:
            start = fileoff + (addr - vmaddr)
            raw = image[start : start + size]
            if len(raw) != size:
                return None
            return int.from_bytes(raw, "little")
    return None


def decode_chained_fixup_addresses(
    image: bytes,
    blob_off: int,
    blob_size: int,
    segments: Sequence[tuple[int, int, int]],
    text_base: int,
    *,
    limit: int,
) -> frozenset[int] | None:
    """Every fixup location of an ``LC_DYLD_CHAINED_FIXUPS`` blob, or None.

    The blob is ``dyld_chained_fixups_header`` followed by the starts tables.
    Each chain is walked from its page start through the ``next`` links, and
    every visited pointer slot is a fixup. Targets and imports are not needed.
    ``None`` means the blob could not be fully decoded (absent, truncated, a
    pointer format this decoder does not know, a chain that leaves its page or
    is not in the file, or more fixups than the segments can hold); it is never
    "no fixups".
    """
    try:
        return _decode_chained(image, blob_off, blob_size, segments, text_base, limit)
    except (FixupDecodeError, IndexError, ValueError, struct.error):
        return None


def _decode_chained(
    image: bytes,
    blob_off: int,
    blob_size: int,
    segments: Sequence[tuple[int, int, int]],
    text_base: int,
    limit: int,
) -> frozenset[int] | None:
    blob = image[blob_off : blob_off + blob_size]
    # dyld_chained_fixups_header is seven 32-bit fields: 28 bytes. A shorter
    # blob is truncated, and the starts table cannot begin inside the header.
    if blob_size == 0 or len(blob) != blob_size or len(blob) < _CHAINED_HEADER_SIZE:
        return None
    starts_off = struct.unpack_from("<I", blob, 4)[0]
    if starts_off < _CHAINED_HEADER_SIZE or starts_off + 4 > len(blob):
        return None
    seg_count = struct.unpack_from("<I", blob, starts_off)[0]
    # The starts table has one entry per segment of the image: a count past the
    # image's own segments names segments that do not exist.
    if seg_count > len(segments):
        return None
    if starts_off + 4 + 4 * seg_count > len(blob):
        return None
    out: set[int] = set()
    visits = [0]  # slots read across every chain of this image
    for index in range(seg_count):
        seg_info = struct.unpack_from("<I", blob, starts_off + 4 + 4 * index)[0]
        if seg_info == 0:
            continue
        base = starts_off + seg_info
        if base + _STARTS_IN_SEGMENT_HEADER > len(blob):
            return None
        size, page_size, pointer_format, segment_offset = struct.unpack_from(
            "<IHHQ", blob, base
        )
        page_count = struct.unpack_from("<H", blob, base + 20)[0]
        if pointer_format not in _CHAINED_FORMATS or page_size == 0:
            return None
        if base + size > len(blob) or size < _STARTS_IN_SEGMENT_HEADER:
            return None
        stride, shift, width = _CHAINED_FORMATS[pointer_format]
        starts_array = base + _STARTS_IN_SEGMENT_HEADER
        array_len = (size - _STARTS_IN_SEGMENT_HEADER) // 2
        if page_count > array_len:
            return None
        starts = struct.unpack_from(f"<{array_len}H", blob, starts_array)
        seg_base = text_base + segment_offset
        for page in range(page_count):
            start = starts[page]
            if start == _CHAINED_START_NONE:
                continue
            page_vm = seg_base + page * page_size
            if start & _CHAINED_START_MULTI:
                # Several chains on this page: the overflow list holds one start
                # per chain, the last one flagged with the LAST bit.
                index_ = start & _CHAINED_OFFSET_MASK
                while True:
                    if index_ >= array_len:
                        return None
                    entry = starts[index_]
                    _walk_chain(
                        image,
                        segments,
                        page_vm,
                        page_size,
                        page_vm + (entry & _CHAINED_OFFSET_MASK),
                        stride,
                        shift,
                        width,
                        out,
                        limit,
                        visits,
                    )
                    if entry & _CHAINED_START_LAST:
                        break
                    index_ += 1
            else:
                _walk_chain(
                    image,
                    segments,
                    page_vm,
                    page_size,
                    page_vm + start,
                    stride,
                    shift,
                    width,
                    out,
                    limit,
                    visits,
                )
    return frozenset(out)


def _walk_chain(
    image: bytes,
    segments: Sequence[tuple[int, int, int]],
    page_vm: int,
    page_size: int,
    loc: int,
    stride: int,
    shift: int,
    width: int,
    out: set[int],
    limit: int,
    visits: list[int],
) -> None:
    """Follow one chain from *loc*, adding every slot to *out*.

    *visits* counts every slot read across all chains of the image. A chain's
    slots are distinct, so a legitimate decode reads at most *limit* of them;
    a repeated MULTI overflow entry re-walks a chain and is refused here.
    """
    page_end = page_vm + page_size
    while True:
        if not page_vm <= loc or loc + 8 > page_end:
            raise FixupDecodeError("chain leaves its page")
        visits[0] += 1
        if visits[0] > limit:
            raise FixupDecodeError("chain visits exceed the image's slot budget")
        value = _read_vm(image, segments, loc, 8)
        if value is None:
            raise FixupDecodeError("chain slot is not in the file")
        out.add(loc)
        if len(out) > limit:
            raise FixupDecodeError("more fixups than the image's segments can hold")
        nxt = (value >> shift) & ((1 << width) - 1)
        if nxt == 0:
            return
        loc += nxt * stride
