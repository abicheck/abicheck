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

"""DWARF from a Mach-O ``.dSYM`` bundle, read through pyelftools' ``DWARFInfo``.

``dsymutil`` writes a Mach-O (``MH_DSYM``) file whose ``__DWARF`` segment holds
the debug sections (``__debug_info``, ``__debug_abbrev``, ...). Those section
bytes are exactly the ELF ``.debug_*`` contents pyelftools already parses, so
this module only maps Mach-O sections onto pyelftools' ``DWARFInfo`` arguments
and checks that the bundle belongs to the dylib (UUID). It is not a second
DWARF reader.

A bundle that is missing, fat, malformed, or built from a different binary is
*absent* evidence (``None`` with a reason), never an empty DWARF surface.
"""

from __future__ import annotations

import inspect
import io
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from elftools.dwarf.dwarfinfo import DebugSectionDescriptor, DWARFInfo

__all__ = ["DsymDwarf", "DsymAbsent", "read_dsym_dwarf", "macho_uuid"]

_MH_MAGIC_64 = 0xFEEDFACF
_MH_CIGAM_64 = 0xCFFAEDFE
_LC_SEGMENT_64 = 0x19
_LC_UUID = 0x1B
_MH_HEADER_64_SIZE = 32
_SECTION_64_SIZE = 80
_DWARF_SEGMENT = b"__DWARF"

#: Mach-O ``__debug_*`` section names -> the ELF names pyelftools expects.
_SECTION_MAP = {
    "__debug_info": "debug_info",
    "__debug_aranges": "debug_aranges",
    "__debug_abbrev": "debug_abbrev",
    "__debug_frame": "debug_frame",
    "__debug_str": "debug_str",
    "__debug_loc": "debug_loc",
    "__debug_ranges": "debug_ranges",
    "__debug_line": "debug_line",
    "__debug_pubtypes": "debug_pubtypes",
    "__debug_pubnames": "debug_pubnames",
    "__debug_addr": "debug_addr",
    "__debug_str_offs": "debug_str_offsets",
    "__debug_line_str": "debug_line_str",
    "__debug_loclists": "debug_loclists",
    "__debug_rnglists": "debug_rnglists",
    "__debug_types": "debug_types",
}

_DWARFINFO_ARGS = frozenset(inspect.signature(DWARFInfo.__init__).parameters)


@dataclass(frozen=True)
class DsymDwarf:
    """A dSYM's DWARF, ready for a pyelftools session."""

    dwarf: DWARFInfo
    uuid: str
    path: Path


@dataclass(frozen=True)
class DsymAbsent:
    """Why no DWARF could be read from a bundle. Always absent, never empty."""

    reason: str


def _is_thin_64(data: bytes) -> bool:
    if len(data) < _MH_HEADER_64_SIZE:
        return False
    magic = struct.unpack_from("<I", data, 0)[0]
    return bool(magic == _MH_MAGIC_64)


def _commands(data: bytes) -> list[tuple[int, int, int]]:
    """``(cmd, offset, cmdsize)`` for every load command of a thin 64-bit file."""
    ncmds = struct.unpack_from("<I", data, 16)[0]
    out: list[tuple[int, int, int]] = []
    off = _MH_HEADER_64_SIZE
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", data, off)
        if cmdsize < 8 or off + cmdsize > len(data):
            raise ValueError("load command runs past the file")
        out.append((cmd, off, cmdsize))
        off += cmdsize
    return out


def _uuid_from_bytes(data: bytes) -> str | None:
    """The ``LC_UUID`` of thin 64-bit Mach-O bytes, hyphenated uppercase hex."""
    if not _is_thin_64(data):
        return None
    try:
        for cmd, off, cmdsize in _commands(data):
            if cmd == _LC_UUID and cmdsize >= 24:
                raw = data[off + 8 : off + 24].hex().upper()
                return "-".join(
                    (raw[0:8], raw[8:12], raw[12:16], raw[16:20], raw[20:32])
                )
    except (ValueError, struct.error):
        return None
    return None


def macho_uuid(path: Path) -> str | None:
    """The ``LC_UUID`` of a thin 64-bit Mach-O file, hyphenated uppercase hex."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return _uuid_from_bytes(data)


def _dwarf_sections(data: bytes) -> dict[str, tuple[int, int, int]]:
    """``{elf_name: (address, file_offset, size)}`` for the ``__DWARF`` segment."""
    out: dict[str, tuple[int, int, int]] = {}
    for cmd, off, _cmdsize in _commands(data):
        if cmd != _LC_SEGMENT_64:
            continue
        segname = data[off + 8 : off + 24].rstrip(b"\0")
        if segname != _DWARF_SEGMENT:
            continue
        nsects = struct.unpack_from("<I", data, off + 64)[0]
        for i in range(nsects):
            s = off + 72 + i * _SECTION_64_SIZE
            sectname = data[s : s + 16].rstrip(b"\0").decode("ascii", "replace")
            addr, size = struct.unpack_from("<QQ", data, s + 32)
            (file_off,) = struct.unpack_from("<I", data, s + 48)
            elf = _SECTION_MAP.get(sectname)
            if elf is not None:
                out[elf] = (addr, file_off, size)
    return out


def _descriptor(
    data: bytes, name: str, sections: dict[str, tuple[int, int, int]]
) -> DebugSectionDescriptor | None:
    if name not in sections:
        return None
    addr, file_off, size = sections[name]
    body = data[file_off : file_off + size]
    if len(body) != size:
        raise ValueError(f"{name} runs past the bundle")
    return DebugSectionDescriptor(
        stream=io.BytesIO(body),
        name=f".{name}",
        global_offset=0,
        size=size,
        address=addr,
    )


def read_dsym_dwarf(dsym: Path, *, dylib_uuid: str | None) -> DsymDwarf | DsymAbsent:
    """DWARF from the bundle's ``__DWARF`` segment, or why there is none.

    ``dsym`` is the Mach-O file inside the bundle
    (``<lib>.dSYM/Contents/Resources/DWARF/<name>``). ``dylib_uuid`` is the
    dylib's ``LC_UUID``; a bundle whose UUID differs was built from another
    binary and is refused. ``None`` means the UUID could not be read at all.
    """
    if not dsym.is_file():
        return DsymAbsent("no dSYM file")
    try:
        data = dsym.read_bytes()
    except OSError as exc:
        return DsymAbsent(f"dSYM unreadable: {exc}")
    if not _is_thin_64(data):
        return DsymAbsent("dSYM is not a thin 64-bit Mach-O (fat or foreign)")
    uuid = _uuid_from_bytes(data)  # same bytes as the sections below
    if uuid is None:
        return DsymAbsent("dSYM carries no LC_UUID")
    if dylib_uuid is None:
        return DsymAbsent("dylib carries no LC_UUID; the dSYM cannot be matched")
    if uuid.upper() != dylib_uuid.upper():
        return DsymAbsent(f"dSYM UUID {uuid} does not match the dylib's {dylib_uuid}")
    try:
        sections = _dwarf_sections(data)
        if "debug_info" not in sections:
            return DsymAbsent("dSYM __DWARF segment has no __debug_info")
        wanted = {k: _descriptor(data, k, sections) for k in _SECTION_MAP.values()}
        config = _config(data)
        all_kw = {}
        all_kw["config"] = config
        all_kw["debug_info_sec"] = wanted.get("debug_info")
        all_kw["debug_aranges_sec"] = wanted.get("debug_aranges")
        all_kw["debug_abbrev_sec"] = wanted.get("debug_abbrev")
        all_kw["debug_frame_sec"] = wanted.get("debug_frame")
        all_kw["eh_frame_sec"] = None
        all_kw["debug_str_sec"] = wanted.get("debug_str")
        all_kw["debug_loc_sec"] = wanted.get("debug_loc")
        all_kw["debug_ranges_sec"] = wanted.get("debug_ranges")
        all_kw["debug_line_sec"] = wanted.get("debug_line")
        all_kw["debug_pubtypes_sec"] = wanted.get("debug_pubtypes")
        all_kw["debug_pubnames_sec"] = wanted.get("debug_pubnames")
        all_kw["debug_addr_sec"] = wanted.get("debug_addr")
        all_kw["debug_str_offsets_sec"] = wanted.get("debug_str_offsets")
        all_kw["debug_line_str_sec"] = wanted.get("debug_line_str")
        all_kw["debug_loclists_sec"] = wanted.get("debug_loclists")
        all_kw["debug_rnglists_sec"] = wanted.get("debug_rnglists")
        all_kw["debug_sup_sec"] = None
        all_kw["gnu_debugaltlink_sec"] = None
        all_kw["debug_types_sec"] = wanted.get("debug_types")
        # Keep only the arguments this pyelftools accepts (0.29 has no debug_types_sec).
        dwarf = DWARFInfo(**{k: v for k, v in all_kw.items() if k in _DWARFINFO_ARGS})

    except (ValueError, struct.error) as exc:
        return DsymAbsent(f"dSYM DWARF unreadable: {exc}")
    return DsymDwarf(dwarf=dwarf, uuid=uuid, path=dsym)


def _config(data: bytes) -> Any:
    from elftools.dwarf.dwarfinfo import DwarfConfig

    cputype = struct.unpack_from("<I", data, 4)[0]
    # CPU_TYPE_X86_64 = 0x01000007, CPU_TYPE_ARM64 = 0x0100000c.
    arch = "AArch64" if cputype == 0x0100000C else "x64"
    return DwarfConfig(little_endian=True, default_address_size=8, machine_arch=arch)
