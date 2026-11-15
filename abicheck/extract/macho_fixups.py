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

"""Pointer fixup locations of a Mach-O image, decoded from classic dyld info.

A pointer that the loader must rewrite (a *rebase*: an address inside the image)
or resolve (a *bind*: an imported symbol) is recorded by the rebase and bind
opcode streams of ``LC_DYLD_INFO`` / ``LC_DYLD_INFO_ONLY``. Each fixup records
the address of one pointer-sized slot, so the number of fixups inside an object
is independent evidence of its layout: an object whose span holds exactly as
many pointer slots as the span implies is one the linker wrote as pointers.

Only the classic encodings are decoded here. ``LC_DYLD_CHAINED_FIXUPS`` (the
chained format) is not decoded, so an image that carries it has no fixup list:
callers must treat that as "unknown", never as "no fixups".
"""

from __future__ import annotations

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


def _uleb(data: bytes, pos: int) -> tuple[int, int]:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise FixupDecodeError("ULEB128 runs past the end of the stream")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
        shift += 7


def _sleb(data: bytes, pos: int) -> int:
    value = shift = 0
    while True:
        if pos >= len(data):
            raise FixupDecodeError("SLEB128 runs past the end of the stream")
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return value
        # Only the position is needed here; the value is not used.


def _address(bases: Sequence[int], seg: int, offset: int) -> int:
    if not 0 <= seg < len(bases):
        raise FixupDecodeError(f"segment index {seg} names no segment")
    return bases[seg] + offset


def decode_rebase_addresses(
    data: bytes, segment_bases: Sequence[int], pointer_size: int
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
                out.append(_address(segment_bases, seg, offset))
                offset += pointer_size
        elif code == _REBASE_DO_ULEB_TIMES:
            count, pos = _uleb(data, pos)
            for _ in range(count):
                out.append(_address(segment_bases, seg, offset))
                offset += pointer_size
        elif code == _REBASE_DO_ADD_ADDR_ULEB:
            out.append(_address(segment_bases, seg, offset))
            value, pos = _uleb(data, pos)
            offset += pointer_size + value
        elif code == _REBASE_DO_ULEB_TIMES_SKIPPING_ULEB:
            count, pos = _uleb(data, pos)
            skip, pos = _uleb(data, pos)
            for _ in range(count):
                out.append(_address(segment_bases, seg, offset))
                offset += pointer_size + skip
        else:
            raise FixupDecodeError(f"unknown rebase opcode 0x{op:02x}")
    return out


def decode_bind_addresses(
    data: bytes, segment_bases: Sequence[int], pointer_size: int
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
        if code == _BIND_SET_DYLIB_ORDINAL_IMM or code == _BIND_SET_TYPE_IMM:
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
            _sleb(data, pos)
            pos = _skip_sleb(data, pos)
        elif code == _BIND_SET_SEGMENT_AND_OFFSET_ULEB:
            seg = imm
            offset, pos = _uleb(data, pos)
        elif code == _BIND_ADD_ADDR_ULEB:
            value, pos = _uleb(data, pos)
            offset += value
        elif code == _BIND_DO_BIND:
            out.append(_address(segment_bases, seg, offset))
            offset += pointer_size
        elif code == _BIND_DO_BIND_ADD_ADDR_ULEB:
            out.append(_address(segment_bases, seg, offset))
            value, pos = _uleb(data, pos)
            offset += pointer_size + value
        elif code == _BIND_DO_BIND_ADD_ADDR_IMM_SCALED:
            out.append(_address(segment_bases, seg, offset))
            offset += pointer_size + imm * pointer_size
        elif code == _BIND_DO_BIND_ULEB_TIMES_SKIPPING_ULEB:
            count, pos = _uleb(data, pos)
            skip, pos = _uleb(data, pos)
            for _ in range(count):
                out.append(_address(segment_bases, seg, offset))
                offset += pointer_size + skip
        else:
            raise FixupDecodeError(f"unknown bind opcode 0x{op:02x}")
    return out


def _skip_sleb(data: bytes, pos: int) -> int:
    while True:
        if pos >= len(data):
            raise FixupDecodeError("SLEB128 runs past the end of the stream")
        byte = data[pos]
        pos += 1
        if not byte & 0x80:
            return pos


def fixup_addresses(
    image: bytes,
    segment_bases: Sequence[int],
    pointer_size: int,
    *,
    rebase: tuple[int, int] | None,
    binds: Sequence[tuple[int, int]],
) -> frozenset[int] | None:
    """Every rebase and bind slot address, or ``None`` if any stream is undecodable.

    *rebase* and *binds* are ``(file_offset, size)`` pairs relative to the start
    of the image slice; *segment_bases* are the segments' virtual addresses in
    load-command order. ``None`` is "unknown", never "no fixups".
    """
    addresses: set[int] = set()
    try:
        if rebase is not None and rebase[1]:
            stream = image[rebase[0] : rebase[0] + rebase[1]]
            addresses.update(
                decode_rebase_addresses(stream, segment_bases, pointer_size)
            )
        for off, size in binds:
            if size:
                stream = image[off : off + size]
                addresses.update(
                    decode_bind_addresses(stream, segment_bases, pointer_size)
                )
    except FixupDecodeError:
        return None
    return frozenset(addresses)


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
) -> frozenset[int] | None:
    """Every fixup location of an ``LC_DYLD_CHAINED_FIXUPS`` blob, or None.

    The blob is ``dyld_chained_fixups_header`` followed by the starts tables.
    Only the *locations* of fixups are decoded: each chain is walked from its
    page start through the ``next`` links, and every visited pointer slot is a
    fixup. Targets and imports are not needed. ``None`` means the blob could
    not be decoded (absent, truncated, a pointer format this decoder does not
    know, a chain that leaves its page, or a broken link); it is never "no
    fixups".
    """
    try:
        return _decode_chained(image, blob_off, blob_size, segments, text_base)
    except (FixupDecodeError, IndexError, ValueError):
        return None


def _decode_chained(
    image: bytes,
    blob_off: int,
    blob_size: int,
    segments: Sequence[tuple[int, int, int]],
    text_base: int,
) -> frozenset[int] | None:
    blob = image[blob_off : blob_off + blob_size]
    if len(blob) < 28 or blob_size == 0:
        return None
    starts_off = int.from_bytes(blob[4:8], "little")
    seg_count = int.from_bytes(blob[starts_off : starts_off + 4], "little")
    out: set[int] = set()
    for index in range(seg_count):
        entry = starts_off + 4 + 4 * index
        seg_info = int.from_bytes(blob[entry : entry + 4], "little")
        if seg_info == 0:
            continue
        base = starts_off + seg_info
        size = int.from_bytes(blob[base : base + 4], "little")
        page_size = int.from_bytes(blob[base + 4 : base + 6], "little")
        pointer_format = int.from_bytes(blob[base + 6 : base + 8], "little")
        segment_offset = int.from_bytes(blob[base + 8 : base + 16], "little")
        page_count = int.from_bytes(blob[base + 20 : base + 22], "little")
        if pointer_format not in _CHAINED_FORMATS or page_size == 0:
            return None
        stride, shift, width = _CHAINED_FORMATS[pointer_format]
        starts_array = base + 22
        array_len = (size - 22) // 2
        if page_count > array_len:
            return None
        starts = [
            int.from_bytes(
                blob[starts_array + 2 * i : starts_array + 2 * i + 2], "little"
            )
            for i in range(array_len)
        ]
        seg_base = text_base + segment_offset
        for page in range(page_count):
            start = starts[page]
            if start == _CHAINED_START_NONE:
                continue
            page_vm = seg_base + page * page_size
            if start & _CHAINED_START_MULTI:
                # Several chains on this page: the overflow list holds one start
                # per chain, the last one flagged with the LAST bit.
                index = start & _CHAINED_OFFSET_MASK
                while True:
                    if index >= array_len:
                        return None
                    entry_val = starts[index]
                    _walk_chain(
                        image,
                        segments,
                        page_vm,
                        page_size,
                        page_vm + (entry_val & _CHAINED_OFFSET_MASK),
                        stride,
                        shift,
                        width,
                        out,
                    )
                    if entry_val & _CHAINED_START_LAST:
                        break
                    index += 1
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
) -> None:
    """Follow one chain from *loc*, adding every slot to *out*."""
    page_end = page_vm + page_size
    while True:
        if not page_vm <= loc or loc + 8 > page_end:
            raise FixupDecodeError("chain leaves its page")
        value = _read_vm(image, segments, loc, 8)
        if value is None:
            raise FixupDecodeError("chain slot is not in the file")
        out.add(loc)
        nxt = (value >> shift) & ((1 << width) - 1)
        if nxt == 0:
            return
        loc += nxt * stride
