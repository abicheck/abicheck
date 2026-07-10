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

"""PE/COFF metadata for Windows DLL/LIB files.

Uses ``pefile`` (pure Python) for parsing PE headers, export/import tables,
and version resources from Windows shared libraries (.dll / .lib).
"""
from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path

import pefile  # type: ignore[import-untyped]

log = logging.getLogger(__name__)


class PeSymbolType(str, Enum):
    EXPORTED = "exported"       # ordinal / name in export table
    FORWARDED = "forwarded"     # forwarded to another DLL
    OTHER = "other"


@dataclass
class PeExport:
    """A single exported symbol from a PE export directory."""
    name: str
    ordinal: int = 0
    sym_type: PeSymbolType = PeSymbolType.EXPORTED
    forwarder: str = ""         # e.g. "NTDLL.RtlAllocateHeap" for forwarded exports


@dataclass
class PeMetadata:
    """PE metadata from a Windows DLL.

    NOTE: Do NOT add ``frozen=True`` — ``@cached_property`` requires a
    writable ``__dict__``.
    """
    # DLL characteristics
    machine: str = ""                   # e.g. "IMAGE_FILE_MACHINE_AMD64"
    characteristics: int = 0            # IMAGE_FILE_HEADER.Characteristics
    dll_characteristics: int = 0        # IMAGE_OPTIONAL_HEADER.DllCharacteristics

    # Imports and exports
    exports: list[PeExport] = field(default_factory=list)
    imports: dict[str, list[str]] = field(default_factory=dict)  # dll_name → [func_names]
    # Delay-loaded imports (IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT): resolved on
    # first call rather than at load time, so a missing DLL fails late.
    delay_imports: dict[str, list[str]] = field(default_factory=dict)

    # Version resource (VS_FIXEDFILEINFO)
    file_version: str = ""      # e.g. "10.0.19041.1"
    product_version: str = ""   # e.g. "10.0.19041.1"

    # Minimum OS floor: OPTIONAL_HEADER.MajorSubsystemVersion.MinorSubsystemVersion
    # (e.g. "6.1" = Windows 7). "" = not captured (legacy snapshot).
    subsystem_version: str = ""

    @cached_property
    def export_map(self) -> dict[str, PeExport]:
        """Name → PeExport mapping (built once, cached on first access)."""
        return {e.name: e for e in self.exports if e.name}


# ---------------------------------------------------------------------------
# Magic detection
# ---------------------------------------------------------------------------

# PE files start with "MZ" (DOS stub), then at offset stored at 0x3C there is
# the PE signature "PE\0\0".
_MZ_MAGIC = b"MZ"


def is_pe(path: Path) -> bool:
    """Check if file is a PE binary (MZ magic + PE signature)."""
    try:
        with open(path, "rb") as f:
            mz = f.read(2)
            if mz != _MZ_MAGIC:
                return False
            f.seek(0x3C)
            pe_offset_bytes = f.read(4)
            if len(pe_offset_bytes) < 4:
                return False
            pe_offset = int.from_bytes(pe_offset_bytes, "little")
            f.seek(pe_offset)
            return f.read(4) == b"PE\x00\x00"
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pe_metadata(dll_path: Path) -> PeMetadata:
    """Extract PE export/import metadata from *dll_path* using pefile.

    Returns an empty ``PeMetadata`` on any parse error (logged as WARNING).
    """
    try:
        with open(dll_path, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("parse_pe_metadata: not a regular file: %s", dll_path)
                return PeMetadata()

        return _parse(dll_path)
    except Exception as exc:  # noqa: BLE001
        # Catch pefile.PEFormatError (only available when pefile is installed)
        log.warning("parse_pe_metadata: failed to parse %s: %s", dll_path, exc)
        return PeMetadata()


def _parse(dll_path: Path) -> PeMetadata:
    pe = pefile.PE(str(dll_path), fast_load=True)
    try:
        pe.parse_data_directories(
            directories=[
                pefile.DIRECTORY_ENTRY[key]
                for key in (
                    "IMAGE_DIRECTORY_ENTRY_EXPORT",
                    "IMAGE_DIRECTORY_ENTRY_IMPORT",
                    "IMAGE_DIRECTORY_ENTRY_RESOURCE",
                    "IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT",
                )
                if key in pefile.DIRECTORY_ENTRY
            ]
        )

        meta = PeMetadata()

        # Machine type
        meta.machine = pefile.MACHINE_TYPE.get(
            pe.FILE_HEADER.Machine, f"0x{pe.FILE_HEADER.Machine:04x}"
        )
        meta.characteristics = pe.FILE_HEADER.Characteristics
        if hasattr(pe, "OPTIONAL_HEADER"):
            meta.dll_characteristics = getattr(pe.OPTIONAL_HEADER, "DllCharacteristics", 0)
            major = getattr(pe.OPTIONAL_HEADER, "MajorSubsystemVersion", None)
            minor = getattr(pe.OPTIONAL_HEADER, "MinorSubsystemVersion", None)
            if major is not None and minor is not None:
                meta.subsystem_version = f"{major}.{minor}"

        # Exports
        if hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
            for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
                name = exp.name.decode("utf-8", errors="replace") if exp.name else ""
                forwarder = ""
                sym_type = PeSymbolType.EXPORTED

                if exp.forwarder:
                    forwarder = exp.forwarder.decode("utf-8", errors="replace")
                    sym_type = PeSymbolType.FORWARDED

                meta.exports.append(PeExport(
                    name=name,
                    ordinal=exp.ordinal,
                    sym_type=sym_type,
                    forwarder=forwarder,
                ))

        # Imports
        if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
            for entry in pe.DIRECTORY_ENTRY_IMPORT:
                dll_name = entry.dll.decode("utf-8", errors="replace") if entry.dll else ""
                funcs: list[str] = []
                for imp in entry.imports:
                    if imp.name:
                        funcs.append(imp.name.decode("utf-8", errors="replace"))
                    elif getattr(imp, "import_by_ordinal", False):
                        # Ordinal-only import (name=None, import_by_ordinal=True)
                        funcs.append(f"ordinal:{imp.ordinal}")
                meta.imports[dll_name] = funcs

        # Delay-load imports (resolved lazily at first call, not at load time)
        if hasattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT"):
            for entry in pe.DIRECTORY_ENTRY_DELAY_IMPORT:
                dll_name = entry.dll.decode("utf-8", errors="replace") if entry.dll else ""
                funcs = []
                for imp in entry.imports:
                    if imp.name:
                        funcs.append(imp.name.decode("utf-8", errors="replace"))
                    elif getattr(imp, "import_by_ordinal", False):
                        funcs.append(f"ordinal:{imp.ordinal}")
                meta.delay_imports[dll_name] = funcs

        # Version resource
        if hasattr(pe, "VS_FIXEDFILEINFO"):
            for finfo in pe.VS_FIXEDFILEINFO:
                ms_ver = finfo.FileVersionMS
                ls_ver = finfo.FileVersionLS
                meta.file_version = (
                    f"{(ms_ver >> 16) & 0xFFFF}."
                    f"{ms_ver & 0xFFFF}."
                    f"{(ls_ver >> 16) & 0xFFFF}."
                    f"{ls_ver & 0xFFFF}"
                )
                ms_prod = finfo.ProductVersionMS
                ls_prod = finfo.ProductVersionLS
                meta.product_version = (
                    f"{(ms_prod >> 16) & 0xFFFF}."
                    f"{ms_prod & 0xFFFF}."
                    f"{(ls_prod >> 16) & 0xFFFF}."
                    f"{ls_prod & 0xFFFF}"
                )
                break  # only first entry

        return meta
    finally:
        pe.close()
