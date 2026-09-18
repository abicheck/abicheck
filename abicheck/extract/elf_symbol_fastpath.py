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

"""Decode a symbol table's fixed-layout entries with one bulk read.

``Elf_Sym`` is a **fixed-width, fixed-order** record -- 24 bytes on
ELF64, 16 on ELF32 -- but pyelftools parses each one through ``construct``:
``SymbolTableSection.get_symbol`` calls ``struct_parse`` per symbol, which
builds a parse tree, walks sub-constructs, and allocates a ``Container``
per field. Profiling a real oneDAL library makes the cost concrete --
``libonedal_core.so.3`` carries 13,536 dynamic and 161,008 static symbols,
and of the 13.58s a profiled ``_pyelftools_exported_symbols`` spends,
**12.87s is ``get_symbol`` and 11.9s of that is construct**, against
essentially nothing for name resolution once
:mod:`~abicheck.extract.elf_string_table` buffers it. Entry decoding is
what remains.

:func:`iter_symbol_fields` reads the whole table once and unpacks it with
``struct.Struct.iter_unpack``, yielding the four fields a *filtering*
caller needs as plain integers. It deliberately does **not** rebuild
pyelftools' ``Symbol``/``Container`` objects: reconstructing the thing
whose construction is the cost would defeat the point, so this serves the
callers that ask questions of the entry rather than pass it on.

**Scope, stated as a refusal rather than an assumption.** The function
returns ``None`` -- meaning "use the ordinary reader" -- for anything it
has not been shown to decode identically:

* an ELF class other than 32/64-bit, or an unknown endianness;
* an ``sh_entsize`` that disagrees with the class's fixed record size (a
  future ELF variant, or a malformed header, would silently mis-stride);
* a table whose byte length is not a whole multiple of that record size;
* a short or failed read -- a truncated table must keep whatever
  pyelftools does with it, not be silently shortened here;
* a table larger than :data:`MAX_BULK_TABLE_BYTES`, so a corrupt
  ``sh_size`` cannot turn a parse into an arbitrary allocation.

**Why raw integers and not pyelftools' string enums.** ``get_symbol``
maps ``st_info``/``st_other``/``st_shndx`` through ``ENUM_ST_INFO_BIND``
and friends into strings (``"STB_GLOBAL"``, ``"SHN_UNDEF"``). Producing
those strings would put a dict lookup and an interning decision back in
the per-symbol path. Callers that need to *compare* against them can use
the integer constants below, which this module verifies against
pyelftools' own enums at import time (:func:`_assert_enums_agree`) rather
than hard-coding values the ABI spec happens to give today -- if a future
pyelftools renumbered one, the import fails loudly instead of this
module quietly filtering on a stale number.
"""

from __future__ import annotations

import struct
from collections.abc import Iterator
from typing import Any

__all__ = [
    "MAX_BULK_TABLE_BYTES",
    "SHN_ABS",
    "SHN_UNDEF",
    "STB_GLOBAL",
    "STB_WEAK",
    "STV_HIDDEN",
    "STV_INTERNAL",
    "iter_symbol_fields",
]

#: Largest symbol table decoded in one bulk read. oneDAL's own 161k-symbol
#: ``.symtab`` is ~3.9 MB, far below this.
MAX_BULK_TABLE_BYTES = 256 * 1024 * 1024

# `st_name` (uint32), `st_info` (uchar), `st_other` (uchar), `st_shndx`
# (uint16), `st_value`, `st_size`. ELF32 orders the record differently --
# value/size precede info/other -- which is exactly why the two formats are
# written out separately rather than parameterized by width.
_FORMATS: dict[tuple[int, bool], str] = {
    (64, True): "<IBBHQQ",
    (64, False): ">IBBHQQ",
    (32, True): "<IIIBBH",
    (32, False): ">IIIBBH",
}
_ENTRY_SIZES = {64: 24, 32: 16}

#: ``st_other``'s visibility field is **three** bits wide, not the two the
#: gABI describes: pyelftools parses it as ``BitField('visibility', 3)`` so
#: that Solaris' ``STV_EXPORTED``/``STV_SINGLETON``/``STV_ELIMINATE`` (4/5/6)
#: decode at all. Masking with ``0x3`` instead looks right against the
#: generic ABI and is wrong against the parser this module must reproduce:
#: it turns ``st_other == 5`` (``STV_SINGLETON``, which the caller keeps)
#: into ``1`` (``STV_INTERNAL``, which the caller drops), silently removing
#: a real export. The layout is ``[local:3][padding:2][visibility:3]``.
_VISIBILITY_MASK = 0x7

SHN_UNDEF = 0
SHN_ABS = 0xFFF1
STB_GLOBAL = 1
STB_WEAK = 2
STV_INTERNAL = 1
STV_HIDDEN = 2


def _assert_enums_agree() -> None:
    """Fail at import if pyelftools' own numbering moved out from under us.

    The integers above are the ELF spec's, but what matters is that they
    still mean what *pyelftools* means by the strings existing callers
    compare against -- that is the behaviour this fast path has to
    reproduce. Checking it here turns a silent mis-filter (the whole class
    of bug this module could introduce) into an import-time failure.
    """
    from elftools.elf.enums import (
        ENUM_ST_INFO_BIND,
        ENUM_ST_SHNDX,
        ENUM_ST_VISIBILITY,
    )

    expected = [
        (ENUM_ST_INFO_BIND, "STB_GLOBAL", STB_GLOBAL),
        (ENUM_ST_INFO_BIND, "STB_WEAK", STB_WEAK),
        (ENUM_ST_VISIBILITY, "STV_INTERNAL", STV_INTERNAL),
        (ENUM_ST_VISIBILITY, "STV_HIDDEN", STV_HIDDEN),
        (ENUM_ST_SHNDX, "SHN_UNDEF", SHN_UNDEF),
        (ENUM_ST_SHNDX, "SHN_ABS", SHN_ABS),
    ]
    for enum, name, value in expected:
        if enum.get(name) != value:
            raise RuntimeError(
                f"pyelftools maps {name} to {enum.get(name)!r}, not {value!r}; "
                "abicheck.extract.elf_symbol_fastpath would filter on a stale "
                "value. Update the constants together with this check."
            )


_assert_enums_agree()


def iter_symbol_fields(
    section: Any, elffile: Any
) -> Iterator[tuple[int, int, int, int]] | None:
    """``(st_name, bind, visibility, st_shndx)`` per symbol, or ``None``.

    ``None`` means "this table is outside the supported fixed-layout set,
    use the ordinary reader" -- never "this table is empty". An empty
    table yields an empty iterator instead, so a caller cannot confuse
    *unsupported* with *nothing there*.
    """
    try:
        elfclass = int(elffile.elfclass)
        little_endian = bool(elffile.little_endian)
        entsize = int(section["sh_entsize"])
        offset = int(section["sh_offset"])
        size = int(section["sh_size"])
    except Exception:  # noqa: BLE001 - a malformed header stays on the slow path
        return None

    fmt = _FORMATS.get((elfclass, little_endian))
    expected_entsize = _ENTRY_SIZES.get(elfclass)
    if fmt is None or expected_entsize is None:
        return None
    if entsize != expected_entsize:
        # A table striding by something else is not the record this module
        # knows how to read, whatever its contents.
        return None
    if size < 0 or size > MAX_BULK_TABLE_BYTES or size % expected_entsize:
        return None

    stream = getattr(section, "stream", None) or getattr(elffile, "stream", None)
    if stream is None:
        return None
    try:
        stream.seek(offset)
        blob = stream.read(size)
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(blob, bytes) or len(blob) != size:
        # A short read would silently drop trailing symbols; pyelftools
        # reads the real stream and is correct there.
        return None

    return _iter_fields(struct.Struct(fmt), blob, elfclass)


def _iter_fields(
    unpacker: struct.Struct, blob: bytes, elfclass: int
) -> Iterator[tuple[int, int, int, int]]:
    if elfclass == 64:
        for st_name, st_info, st_other, st_shndx, _value, _size in unpacker.iter_unpack(
            blob
        ):
            yield st_name, st_info >> 4, st_other & _VISIBILITY_MASK, st_shndx
    else:
        for st_name, _value, _size, st_info, st_other, st_shndx in unpacker.iter_unpack(
            blob
        ):
            yield st_name, st_info >> 4, st_other & _VISIBILITY_MASK, st_shndx
