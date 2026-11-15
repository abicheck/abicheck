# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Hand-built 64-bit little-endian Mach-O dylibs for the fast lane.

No Mach-O toolchain is needed: the image is a mach_header_64, one __TEXT
LC_SEGMENT_64, an LC_SYMTAB whose nlist64 table names each requested symbol,
and optionally an LC_DYLD_EXPORTS_TRIE whose dyld export trie lists the
exports. ``macho_metadata`` reads exactly these load commands, so the parse
is the real one.

A non-``test_`` helper: import it, do not copy it.
"""

from __future__ import annotations

import struct
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: nlist ``n_type`` bits (mach-o/nlist.h).
N_EXT = 0x01
N_PEXT = 0x10
N_UNDF = 0x00
N_SECT = 0x0E
#: nlist ``n_desc`` bit: a weak definition (coalesced like an ELF WEAK).
N_WEAK_DEF = 0x0080

#: dyld export-trie flags (mach-o/loader.h, EXPORT_SYMBOL_FLAGS_*).
EXPORT_WEAK_DEFINITION = 0x04
EXPORT_REEXPORT = 0x08

_MH_MAGIC_64 = 0xFEEDFACF
_CPU_TYPE_X86_64 = 0x01000007
_MH_DYLIB = 6
_LC_SEGMENT_64 = 0x19
_LC_SYMTAB = 0x2
_LC_DYLD_EXPORTS_TRIE = 0x80000033
_LC_DYLD_INFO_ONLY = 0x80000022
_LC_DYLD_CHAINED_FIXUPS = 0x80000034


@dataclass(frozen=True)
class MachoSymbol:
    """One nlist64 entry: ``name`` is written with the C leading underscore.

    ``n_value`` is the symbol's address; the default 0 suits symbols whose
    address no test reads.
    """

    name: str
    n_type: int
    n_sect: int = 1
    n_desc: int = 0
    n_value: int = 0


#: One section of a synthetic image: ``(segname, sectname, addr, size)``.
#: Consecutive sections sharing a ``segname`` share one LC_SEGMENT_64, and
#: section ordinals run across the segments in load-command order (1-based),
#: as ``n_sect`` numbers them.
SectionSpec = tuple[str, str, int, int]


def _segment_commands(sections: Sequence[SectionSpec] | None) -> tuple[bytes, int]:
    """LC_SEGMENT_64 commands for *sections* and their count.

    Without *sections* this is one empty ``__TEXT`` segment.
    """
    if sections is None:
        return (
            struct.pack(
                "<II16sQQQQiiII",
                _LC_SEGMENT_64,
                72,
                b"__TEXT".ljust(16, b"\x00"),
                0,
                0,
                0,
                0,
                7,
                5,
                0,
                0,
            ),
            1,
        )
    out = bytearray()
    groups: list[tuple[str, list[SectionSpec]]] = []
    for spec in sections:
        if groups and groups[-1][0] == spec[0]:
            groups[-1][1].append(spec)
        else:
            groups.append((spec[0], [spec]))
    for segname, specs in groups:
        lo = min(addr for _s, _n, addr, _size in specs)
        hi = max(addr + size for _s, _n, addr, size in specs)
        body = b"".join(
            struct.pack(
                "<16s16sQQIIIIIIII",
                sectname.encode().ljust(16, b"\x00"),
                seg.encode().ljust(16, b"\x00"),
                addr,
                size,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            )
            for seg, sectname, addr, size in specs
        )
        out += (
            struct.pack(
                "<II16sQQQQiiII",
                _LC_SEGMENT_64,
                72 + len(body),
                segname.encode().ljust(16, b"\x00"),
                lo,
                hi - lo,
                0,
                0,
                7,
                5,
                len(specs),
                0,
            )
            + body
        )
    return bytes(out), len(groups)


def _uleb128(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def build_export_trie(exports: Sequence[tuple[str, int]]) -> bytes:
    """A dyld export trie listing *exports* as ``(name, flags)`` pairs.

    The root is non-terminal and has one terminal leaf per export, so the
    walk reads each name back exactly as written (leading underscore kept).
    Re-exports carry no ordinal/path payload here; the flag alone is enough
    for the export-set tests this helper serves.
    """
    leaves = []
    for _name, flags in exports:
        info = _uleb128(flags) + _uleb128(0)  # flags, address
        leaves.append(_uleb128(len(info)) + info + b"\x00")  # no children
    labels = [name.encode() + b"\x00" for name, _flags in exports]
    offsets = [0] * len(exports)
    # Child offsets are ULEB128 values inside the root, so the root's size
    # depends on them: iterate to the fixed point.
    for _ in range(16):
        root = (
            b"\x00"
            + bytes([len(exports)])
            + b"".join(lbl + _uleb128(off) for lbl, off in zip(labels, offsets))
        )
        pos = len(root)
        new_offsets = []
        for leaf in leaves:
            new_offsets.append(pos)
            pos += len(leaf)
        if new_offsets == offsets:
            return root + b"".join(leaves)
        offsets = new_offsets
    raise AssertionError("export trie offsets did not converge")


def write_dylib(
    path: Path,
    symbols: list[MachoSymbol],
    *,
    export_trie: Sequence[tuple[str, int]] | None = None,
    sections: Sequence[SectionSpec] | None = None,
    rebase_stream: bytes | None = None,
    bind_stream: bytes | None = None,
    chained_fixups: bool = False,
) -> Path:
    """Write a dylib whose symbol table holds *symbols* and return *path*.

    *rebase_stream* / *bind_stream* are classic dyld opcode streams; either one
    makes the image carry an LC_DYLD_INFO_ONLY command. *chained_fixups* adds
    an LC_DYLD_CHAINED_FIXUPS command with no data, which the reader treats as
    "fixups not decoded".

    With *export_trie* (``(name, flags)`` pairs, names with the leading
    underscore) the image also carries an LC_DYLD_EXPORTS_TRIE listing them;
    without it the image has no trie and its nlist is the only export table.
    With *sections* the image's segments carry those sections (see
    :data:`SectionSpec`); without it one empty ``__TEXT`` segment is written.
    """
    strtab = bytearray(b"\x00")
    entries = []
    for sym in symbols:
        offset = len(strtab)
        strtab += sym.name.encode() + b"\x00"
        entries.append(
            struct.pack(
                "<IBBHQ", offset, sym.n_type, sym.n_sect, sym.n_desc, sym.n_value
            )
        )
    segment, segment_count = _segment_commands(sections)
    trie_cmd_size = 16 if export_trie is not None else 0
    has_dyld_info = rebase_stream is not None or bind_stream is not None
    dyld_cmd_size = 48 if has_dyld_info else 0
    chained_cmd_size = 16 if chained_fixups else 0
    header_size = 32
    symtab_off = (
        header_size
        + len(segment)
        + 24
        + trie_cmd_size
        + dyld_cmd_size
        + chained_cmd_size
    )
    stroff = symtab_off + 16 * len(entries)
    symtab = struct.pack(
        "<IIIIII", _LC_SYMTAB, 24, symtab_off, len(entries), stroff, len(strtab)
    )
    trie = build_export_trie(export_trie) if export_trie is not None else b""
    trie_off = stroff + len(strtab)
    rebase_b = rebase_stream or b""
    bind_b = bind_stream or b""
    data_base = trie_off + len(trie)
    commands = segment + symtab
    ncmds = segment_count + 1
    if export_trie is not None:
        commands += struct.pack("<IIII", _LC_DYLD_EXPORTS_TRIE, 16, trie_off, len(trie))
        ncmds += 1
    if has_dyld_info:
        commands += struct.pack(
            "<12I",
            _LC_DYLD_INFO_ONLY,
            48,
            data_base if rebase_b else 0,
            len(rebase_b),
            data_base + len(rebase_b) if bind_b else 0,
            len(bind_b),
            0,
            0,
            0,
            0,
            0,
            0,  # weak/lazy bind and export: none
        )
        ncmds += 1
    if chained_fixups:
        commands += struct.pack("<IIII", _LC_DYLD_CHAINED_FIXUPS, 16, data_base, 0)
        ncmds += 1
    header = struct.pack(
        "<IIIIIIII",
        _MH_MAGIC_64,
        _CPU_TYPE_X86_64,
        3,  # cpusubtype: CPU_SUBTYPE_X86_64_ALL
        _MH_DYLIB,
        ncmds,
        len(commands),
        0,  # flags
        0,  # reserved
    )
    path.write_bytes(
        header + commands + b"".join(entries) + bytes(strtab) + trie + rebase_b + bind_b
    )
    return path


# Classic rebase opcodes (dyld): one SET_SEGMENT_AND_OFFSET per slot, then
# DO_REBASE_IMM_TIMES(1). Enough for a fixture; real images use compact forms.
_REBASE_SET_SEG_OFFSET = 0x20
_REBASE_DO_IMM_TIMES_1 = 0x51
_REBASE_DONE_OP = 0x00


def rebase_stream(
    segment_index: int, segment_vmaddr: int, addresses: Sequence[int]
) -> bytes:
    """A classic rebase stream rebasing each absolute *addresses* slot.

    *segment_index* is the segment's ordinal among the LC_SEGMENT commands and
    *segment_vmaddr* its virtual address; slots are encoded as segment offsets.
    """
    out = bytearray()
    for addr in addresses:
        out.append(_REBASE_SET_SEG_OFFSET | segment_index)
        out += _uleb128(addr - segment_vmaddr)
        out.append(_REBASE_DO_IMM_TIMES_1)
    out.append(_REBASE_DONE_OP)
    return bytes(out)
