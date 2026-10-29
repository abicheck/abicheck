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


@dataclass(frozen=True)
class MachoSymbol:
    """One nlist64 entry: ``name`` is written with the C leading underscore."""

    name: str
    n_type: int
    n_sect: int = 1
    n_desc: int = 0


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
) -> Path:
    """Write a dylib whose symbol table holds *symbols* and return *path*.

    With *export_trie* (``(name, flags)`` pairs, names with the leading
    underscore) the image also carries an LC_DYLD_EXPORTS_TRIE listing them;
    without it the image has no trie and its nlist is the only export table.
    """
    strtab = bytearray(b"\x00")
    entries = []
    for sym in symbols:
        offset = len(strtab)
        strtab += sym.name.encode() + b"\x00"
        entries.append(
            struct.pack("<IBBHQ", offset, sym.n_type, sym.n_sect, sym.n_desc, 0)
        )
    segment = struct.pack(
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
    )
    trie_cmd_size = 16 if export_trie is not None else 0
    header_size = 32
    symtab_off = header_size + len(segment) + 24 + trie_cmd_size
    stroff = symtab_off + 16 * len(entries)
    symtab = struct.pack(
        "<IIIIII", _LC_SYMTAB, 24, symtab_off, len(entries), stroff, len(strtab)
    )
    trie = build_export_trie(export_trie) if export_trie is not None else b""
    trie_off = stroff + len(strtab)
    commands = segment + symtab
    ncmds = 2
    if export_trie is not None:
        commands += struct.pack("<IIII", _LC_DYLD_EXPORTS_TRIE, 16, trie_off, len(trie))
        ncmds = 3
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
    path.write_bytes(header + commands + b"".join(entries) + bytes(strtab) + trie)
    return path
