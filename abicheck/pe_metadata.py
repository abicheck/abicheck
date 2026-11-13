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
from pathlib import Path
from typing import Any

import pefile  # type: ignore[import-untyped]

# Fact dataclasses live in the model package (ADR-061 Phase 5): this module
# parses into them and re-exports them so the historical
# ``from abicheck.pe_metadata import PeExport`` spelling keeps resolving.
from .extract.parse_failures import note_export_read_failure
from .extract.pe_image import open_pe
from .model.fact import sync_present_facts
from .model.pe_facts import (
    PeExport as PeExport,
    PeMetadata as PeMetadata,
    PeSymbolType as PeSymbolType,
)

log = logging.getLogger(__name__)


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
                note_export_read_failure(f"not a regular file: {dll_path}")
                return PeMetadata()

        return _parse(dll_path)
    except Exception as exc:  # noqa: BLE001
        # Catch pefile.PEFormatError (only available when pefile is installed)
        log.warning("parse_pe_metadata: failed to parse %s: %s", dll_path, exc)
        note_export_read_failure(f"PE parse failed: {exc}")
        return PeMetadata()


def _parse_pe_header_fields(pe: Any, meta: PeMetadata) -> None:
    """Populate machine type, characteristics, and subsystem version from PE headers."""
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


def _parse_pe_exports(pe: Any, meta: PeMetadata) -> None:
    """Populate *meta.exports* from the PE export directory."""
    if not hasattr(pe, "DIRECTORY_ENTRY_EXPORT"):
        return
    for exp in pe.DIRECTORY_ENTRY_EXPORT.symbols:
        name = exp.name.decode("utf-8", errors="replace") if exp.name else ""
        forwarder = ""
        sym_type = PeSymbolType.EXPORTED

        if exp.forwarder:
            forwarder = exp.forwarder.decode("utf-8", errors="replace")
            sym_type = PeSymbolType.FORWARDED

        meta.exports.append(
            PeExport(
                name=name,
                ordinal=exp.ordinal,
                sym_type=sym_type,
                forwarder=forwarder,
            )
        )


def _import_entry_funcs(entry: Any) -> list[str]:
    """Decode the imported function names of one import-directory entry."""
    funcs: list[str] = []
    for imp in entry.imports:
        if imp.name:
            funcs.append(imp.name.decode("utf-8", errors="replace"))
        elif getattr(imp, "import_by_ordinal", False):
            # Ordinal-only import (name=None, import_by_ordinal=True)
            funcs.append(f"ordinal:{imp.ordinal}")
    return funcs


def _parse_pe_imports(pe: Any, meta: PeMetadata) -> None:
    """Populate *meta.imports* from the PE import directory."""
    if not hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
        return
    for entry in pe.DIRECTORY_ENTRY_IMPORT:
        dll_name = entry.dll.decode("utf-8", errors="replace") if entry.dll else ""
        meta.imports[dll_name] = _import_entry_funcs(entry)


def _parse_pe_delay_imports(pe: Any, meta: PeMetadata) -> None:
    """Populate *meta.delay_imports* from the PE delay-load import directory."""
    # Delay-load imports (resolved lazily at first call, not at load time)
    if not hasattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT"):
        return
    delay_imports: dict[str, list[str]] = {}
    for entry in pe.DIRECTORY_ENTRY_DELAY_IMPORT:
        dll_name = entry.dll.decode("utf-8", errors="replace") if entry.dll else ""
        delay_imports[dll_name] = _import_entry_funcs(entry)
    meta.delay_imports = delay_imports


def _parse_pe_version_resource(pe: Any, meta: PeMetadata) -> None:
    """Populate file/product version strings from the VS_FIXEDFILEINFO resource."""
    if not hasattr(pe, "VS_FIXEDFILEINFO"):
        return
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


def _rva_range_in_file(pe: Any, rva: int, size: int) -> bool:
    """Whether the *size* bytes at *rva* are held by the file's raw data."""
    if not size:
        return True
    offset = pe.get_offset_from_rva(rva)
    return offset is not None and 0 <= offset and offset + size <= len(pe.__data__)


def _note_truncated_image(pe: Any) -> None:
    """Record a failure for a PE image whose bytes end before its tables do.

    pefile reads past-the-end data as absent rather than raising: an export
    directory whose name strings are cut reads as fewer names, one whose RVA
    lies past the end of the file as no export directory at all, and a
    section whose raw data is cut as a shorter one. Each is a truncated
    image (the Windows loader refuses to map it), never a smaller export table.
    """
    size = len(pe.__data__)
    header_end = (
        pe.DOS_HEADER.e_lfanew
        + 24
        + pe.FILE_HEADER.SizeOfOptionalHeader
        + 40 * pe.FILE_HEADER.NumberOfSections
    )
    if header_end > size or len(pe.sections) < pe.FILE_HEADER.NumberOfSections:
        note_export_read_failure(
            f"PE headers end at byte {header_end} of a {size}-byte file and "
            f"{len(pe.sections)} of {pe.FILE_HEADER.NumberOfSections} section "
            "headers were readable (truncated image)"
        )
    for section in pe.sections:
        end = section.PointerToRawData + section.SizeOfRawData
        if section.SizeOfRawData and end > size:
            name = section.Name.rstrip(b"\0").decode("ascii", errors="replace")
            note_export_read_failure(
                f"PE section {name!r} ends at byte {end} of a {size}-byte file "
                "(truncated image)"
            )
    directories = getattr(pe.OPTIONAL_HEADER, "DATA_DIRECTORY", [])
    if getattr(pe.OPTIONAL_HEADER, "Magic", None) not in (0x10B, 0x20B) or not (
        directories
    ):
        # No PE32/PE32+ optional header: the data directories (the export
        # directory among them) were never located.
        note_export_read_failure("PE optional header unreadable")
        return
    entry = directories[0]
    if entry is None or not entry.VirtualAddress:
        return  # no export directory declared: a DLL exporting nothing
    if not _rva_range_in_file(pe, entry.VirtualAddress, entry.Size):
        note_export_read_failure(
            f"PE export directory (RVA 0x{entry.VirtualAddress:x}, "
            f"{entry.Size} bytes) is not held by the {size}-byte file"
        )
    export = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
    if export is None:
        note_export_read_failure("PE export directory declared but unreadable")
        return
    hdr = export.struct
    for what, rva, length in (
        ("function table", hdr.AddressOfFunctions, 4 * hdr.NumberOfFunctions),
        ("name table", hdr.AddressOfNames, 4 * hdr.NumberOfNames),
        ("name-ordinal table", hdr.AddressOfNameOrdinals, 2 * hdr.NumberOfNames),
    ):
        if not _rva_range_in_file(pe, rva, length):
            note_export_read_failure(f"PE export {what} runs past the end of the file")
    named = sum(1 for sym in export.symbols if sym.name)
    if named < hdr.NumberOfNames:
        note_export_read_failure(
            f"PE export directory names {hdr.NumberOfNames} exports but "
            f"{named} names were readable"
        )


def _parse(dll_path: Path) -> PeMetadata:
    """Parse PE metadata from *dll_path* using pefile."""
    with open_pe(dll_path) as pe:
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

        _note_truncated_image(pe)
        meta = PeMetadata()
        # A successful parse captures the delay-load surface even when the
        # directory is absent ({} = "verified none"), keeping None reserved
        # for legacy snapshots where it was never looked at.
        meta.delay_imports = {}

        _parse_pe_header_fields(pe, meta)
        _parse_pe_exports(pe, meta)
        # The one decode of each export's calling-convention decoration
        # (design-hardening Phase 3); needs the machine read just above.
        meta.decode_export_names()
        _parse_pe_imports(pe, meta)
        _parse_pe_delay_imports(pe, meta)
        _parse_pe_version_resource(pe, meta)

        # Plain attribute assignment never re-runs __post_init__ (ADR-063
        # Phase 5 -- see elf_metadata.py's identical fix).
        sync_present_facts(meta, "delay_imports")

        return meta
