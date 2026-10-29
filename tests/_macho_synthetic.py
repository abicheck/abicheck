# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0

"""Hand-built 64-bit little-endian Mach-O dylibs for the fast lane.

No Mach-O toolchain is needed: the image is a mach_header_64, one __TEXT
LC_SEGMENT_64 and an LC_SYMTAB whose nlist64 table names each requested
symbol. ``macho_metadata`` reads exactly these load commands (the LC_SYMTAB
fallback path, with no LC_DYSYMTAB split), so the parse is the real one.

A non-``test_`` helper: import it, do not copy it.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path

#: nlist ``n_type`` bits (mach-o/nlist.h).
N_EXT = 0x01
N_PEXT = 0x10
N_UNDF = 0x00
N_SECT = 0x0E
#: nlist ``n_desc`` bit: a weak definition (coalesced like an ELF WEAK).
N_WEAK_DEF = 0x0080

_MH_MAGIC_64 = 0xFEEDFACF
_CPU_TYPE_X86_64 = 0x01000007
_MH_DYLIB = 6
_LC_SEGMENT_64 = 0x19
_LC_SYMTAB = 0x2


@dataclass(frozen=True)
class MachoSymbol:
    """One nlist64 entry: ``name`` is written with the C leading underscore."""

    name: str
    n_type: int
    n_sect: int = 1
    n_desc: int = 0


def write_dylib(path: Path, symbols: list[MachoSymbol]) -> Path:
    """Write a dylib whose symbol table holds *symbols* and return *path*."""
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
    header_size = 32
    symtab_off = header_size + len(segment) + 24
    stroff = symtab_off + 16 * len(entries)
    symtab = struct.pack(
        "<IIIIII", _LC_SYMTAB, 24, symtab_off, len(entries), stroff, len(strtab)
    )
    commands = segment + symtab
    header = struct.pack(
        "<IIIIIIII",
        _MH_MAGIC_64,
        _CPU_TYPE_X86_64,
        3,  # cpusubtype: CPU_SUBTYPE_X86_64_ALL
        _MH_DYLIB,
        2,  # ncmds
        len(commands),
        0,  # flags
        0,  # reserved
    )
    path.write_bytes(header + commands + b"".join(entries) + bytes(strtab))
    return path
