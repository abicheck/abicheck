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

"""Read a Mach-O image's compiler producer from DWARF in a ``__DWARF`` segment.

A linked Mach-O image keeps no DWARF of its own: ``dsymutil`` gathers it into
a ``<image>.dSYM`` bundle, whose ``Contents/Resources/DWARF/<name>`` member is
itself a Mach-O file with a ``__DWARF`` segment. An object file (``.o``)
carries the same segment directly. This module finds those sections with a
minimal load-command walk and hands them to pyelftools' ``DWARFInfo`` -- which
has no Mach-O container reader of its own -- so the same CU-top-DIE ranking
:mod:`abicheck.extract.dwarf_producer` applies to ELF picks the producer.

Only little-endian thin images and the first slice of a universal (fat)
binary are read. Anything else yields ``(None, None)`` -- unknown, never a
guessed producer.
"""

from __future__ import annotations

import inspect
import io
import logging
import struct
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["macho_dwarf_sections", "read_macho_producer"]

_MH_MAGIC = 0xFEEDFACE
_MH_MAGIC_64 = 0xFEEDFACF
_FAT_MAGIC = 0xCAFEBABE
_FAT_MAGIC_64 = 0xCAFEBABF
_LC_SEGMENT = 0x1
_LC_SEGMENT_64 = 0x19

#: Mach-O ``__DWARF`` section name -> pyelftools ``DWARFInfo`` keyword.
#: (Mach-O section names are capped at 16 bytes: ``__debug_str_offs``.)
_SECTION_KEYWORDS = {
    "__debug_info": "debug_info_sec",
    "__debug_abbrev": "debug_abbrev_sec",
    "__debug_str": "debug_str_sec",
    "__debug_str_offs": "debug_str_offsets_sec",
    "__debug_line_str": "debug_line_str_sec",
    "__debug_addr": "debug_addr_sec",
}


def _cstr(raw: bytes) -> str:
    return raw.split(b"\0", 1)[0].decode("ascii", errors="replace")


def _thin_slice(data: bytes) -> tuple[int, bool] | None:
    """``(offset, is_64)`` of the little-endian thin image in *data*."""
    if len(data) < 8:
        return None
    (magic_be,) = struct.unpack_from(">I", data, 0)
    if magic_be in (_FAT_MAGIC, _FAT_MAGIC_64):
        (nfat,) = struct.unpack_from(">I", data, 4)
        if nfat < 1:
            return None
        offset = (
            struct.unpack_from(">Q", data, 16)[0]
            if magic_be == _FAT_MAGIC_64
            else struct.unpack_from(">I", data, 16)[0]
        )
        inner = _thin_slice(data[offset:])
        return None if inner is None else (offset + inner[0], inner[1])
    (magic,) = struct.unpack_from("<I", data, 0)
    if magic == _MH_MAGIC_64:
        return 0, True
    if magic == _MH_MAGIC:
        return 0, False
    return None


def macho_dwarf_sections(data: bytes) -> dict[str, tuple[int, int]]:
    """``{section name: (file offset, size)}`` for every ``__DWARF`` section
    of the Mach-O image in *data* (empty when there are none or the image
    is not a little-endian Mach-O)."""
    found = _thin_slice(data)
    if found is None:
        return {}
    base, is_64 = found
    ncmds = struct.unpack_from("<I", data, base + 16)[0]
    cursor = base + (32 if is_64 else 28)
    sections: dict[str, tuple[int, int]] = {}
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, cursor)
        if cmdsize < 8:
            break
        if cmd == (_LC_SEGMENT_64 if is_64 else _LC_SEGMENT):
            nsects = struct.unpack_from("<I", data, cursor + (64 if is_64 else 48))[0]
            sect = cursor + (72 if is_64 else 56)
            for _ in range(nsects):
                name = _cstr(data[sect : sect + 16])
                segname = _cstr(data[sect + 16 : sect + 32])
                if is_64:
                    size, offset = struct.unpack_from("<QI", data, sect + 40)
                else:
                    size, offset = struct.unpack_from("<II", data, sect + 36)
                if segname == "__DWARF" and size:
                    sections[name] = (base + offset, size)
                sect += 80 if is_64 else 68
        cursor += cmdsize
    return sections


def _dwarf_info(data: bytes) -> object | None:
    from elftools.dwarf.dwarfinfo import (
        DebugSectionDescriptor,
        DwarfConfig,
        DWARFInfo,
    )

    sections = macho_dwarf_sections(data)
    if "__debug_info" not in sections or "__debug_abbrev" not in sections:
        return None
    found = _thin_slice(data)
    is_64 = bool(found and found[1])
    # Every keyword this pyelftools version's DWARFInfo accepts defaults to
    # None; only the sections the image carries are filled in.
    kwargs: dict[str, object] = {
        name: None
        for name in inspect.signature(DWARFInfo.__init__).parameters
        if name not in ("self", "config")
    }
    for name, (offset, size) in sections.items():
        keyword = _SECTION_KEYWORDS.get(name)
        if keyword is None or keyword not in kwargs:
            continue
        kwargs[keyword] = DebugSectionDescriptor(
            stream=io.BytesIO(data[offset : offset + size]),
            name=name,
            global_offset=0,
            size=size,
            address=0,
        )
    config = DwarfConfig(
        little_endian=True,
        machine_arch="AArch64" if is_64 else "x86",
        default_address_size=8 if is_64 else 4,
    )
    return DWARFInfo(config=config, **kwargs)  # type: ignore[arg-type]


def _dsym_member(path: Path) -> Path:
    return (
        path.parent
        / f"{path.name}.dSYM"
        / "Contents"
        / "Resources"
        / "DWARF"
        / path.name
    )


def read_macho_producer(
    path: Path, *, debug_path: Path | None = None
) -> tuple[str | None, int | None]:
    """``(DW_AT_producer, DW_AT_language)`` for the Mach-O image at *path*.

    Read from *path*'s own ``__DWARF`` segment (an object file), else from
    *debug_path* (a resolved dSYM DWARF member), else from the adjacent
    ``<path>.dSYM`` bundle. ``(None, None)`` when none of them carries a
    readable CU.
    """
    from .dwarf_producer import best_cu_producer

    candidates = [path]
    if debug_path is not None:
        candidates.append(debug_path)
    candidates.append(_dsym_member(path))
    for candidate in candidates:
        try:
            if not candidate.is_file():
                continue
            dwarf = _dwarf_info(candidate.read_bytes())
            if dwarf is None:
                continue
            producer, lang = best_cu_producer(dwarf)
        except Exception as exc:  # noqa: BLE001 - DWARF is optional evidence
            log.debug("macho_build_signals: cannot read %s: %s", candidate, exc)
            continue
        if producer is not None or lang is not None:
            return producer, lang
    return None, None
