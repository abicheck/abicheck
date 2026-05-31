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

"""PDB-based debug info extraction for Windows PE binaries.

Produces the **same** ``DwarfMetadata`` and ``AdvancedDwarfMetadata`` dataclasses
used by the DWARF pipeline so that the checker's ``_diff_dwarf()`` and
``_diff_advanced_dwarf()`` detectors work without modification.

Phases implemented:
  1. Struct/class/union sizes and field layouts (offsets, types) from TPI stream
  2. Enum underlying types and member values from TPI stream
  3. Calling convention extraction from LF_PROCEDURE / LF_MFUNCTION
  4. Toolchain info from DBI stream header (machine type, build number)

Public API
----------
parse_pdb_debug_info(pdb_path)
    → tuple[DwarfMetadata, AdvancedDwarfMetadata]

Requires a PDB file path.  Use ``pdb_utils.locate_pdb()`` to find the PDB
for a given PE binary.
"""
from __future__ import annotations

import logging
import re
import struct
from pathlib import Path

from .dwarf_advanced import AdvancedDwarfMetadata, ToolchainInfo
from .dwarf_metadata import DwarfMetadata, EnumInfo, FieldInfo, StructLayout
from .pdb_parser import (
    CvEnumerator,
    CvMember,
    CvStruct,
    PdbFile,
    TypeDatabase,
    parse_pdb,
)

log = logging.getLogger(__name__)


def _machine_name(machine_code: int) -> str:
    """Convert a PE machine type code to a short human-readable name.

    Uses ``pefile.MACHINE_TYPE`` when available, stripping the
    ``IMAGE_FILE_MACHINE_`` prefix.  Falls back to hex representation.
    """
    try:
        import pefile
        full_name: str | None = pefile.MACHINE_TYPE.get(machine_code)
        if full_name:
            return full_name.replace("IMAGE_FILE_MACHINE_", "")
    except ImportError:
        pass
    # Fallback for common machine types when pefile is not available
    _FALLBACK: dict[int, str] = {
        0x014C: "I386", 0x0200: "IA64", 0x8664: "AMD64",
        0xAA64: "ARM64", 0x01C0: "ARM", 0x01C4: "ARMNT",
    }
    return _FALLBACK.get(machine_code, f"0x{machine_code:04x}")


def _is_user_visible(name: str | None, is_forward_ref: bool) -> bool:
    """Return True if a PDB type should be included in metadata.

    Filters out forward references, unnamed types, and compiler-internal names.
    """
    if is_forward_ref:
        return False
    if not name:
        return False
    if name.startswith("<") or name.startswith("__"):
        return False
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_pdb_debug_info(
    pdb_path: Path,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Parse a PDB file and return (DwarfMetadata, AdvancedDwarfMetadata).

    Returns ``(DwarfMetadata(), AdvancedDwarfMetadata())`` on any error.
    Never raises.
    """
    empty = DwarfMetadata(), AdvancedDwarfMetadata()

    try:
        pdb = parse_pdb(pdb_path)
    except (ValueError, OSError, struct.error) as exc:
        log.warning("parse_pdb_debug_info: failed to parse %s: %s", pdb_path, exc)
        return empty

    if pdb.types is None:
        log.debug("parse_pdb_debug_info: no TPI stream in %s", pdb_path)
        return empty

    meta = DwarfMetadata(has_dwarf=True)
    adv = AdvancedDwarfMetadata(has_dwarf=True)

    # UDT name → defining source file (ADR-024 Phase 1 provenance), parsed from
    # the IPI stream. Empty when the PDB carries no IPI / source-line records.
    src_files = pdb.udt_source_files

    try:
        _extract_struct_layouts(pdb.types, meta, adv, src_files)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_pdb_debug_info: struct extraction failed: %s", exc)

    try:
        _extract_enums(pdb.types, meta, src_files)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_pdb_debug_info: enum extraction failed: %s", exc)

    try:
        _extract_toolchain_info(pdb, adv)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_pdb_debug_info: toolchain info extraction failed: %s", exc)

    return meta, adv


# ---------------------------------------------------------------------------
# Phase 1: Struct/class/union layouts
# ---------------------------------------------------------------------------

def _extract_struct_layouts(
    types: TypeDatabase,
    meta: DwarfMetadata,
    adv: AdvancedDwarfMetadata | None = None,
    src_files: dict[str, str] | None = None,
) -> None:
    """Extract struct/class/union layouts from TPI into DwarfMetadata.structs.

    Also populates ``adv.all_struct_names`` and ``adv.packed_structs`` in a
    single pass (previously done in a separate ``_extract_calling_conventions``).
    """
    for ti, cv_struct in types.all_structs().items():
        if not _is_user_visible(cv_struct.name, cv_struct.is_forward_ref):
            continue

        # ODR: first complete definition wins for all outputs.
        # Skip if we already have a canonical definition for this name.
        if cv_struct.name in meta.structs:
            continue

        try:
            fields = _extract_fields(types, cv_struct)
        except Exception as exc:  # noqa: BLE001
            # Don't record an empty layout — a later duplicate with the
            # same name may succeed and should become the canonical def.
            log.debug("_extract_struct_layouts: bad fields for %s: %s",
                      cv_struct.name, exc)
            continue

        # Track struct names and packed status in advanced metadata
        # only after successful field extraction.
        if adv is not None:
            adv.all_struct_names.add(cv_struct.name)
            if cv_struct.is_packed:
                adv.packed_structs.add(cv_struct.name)

        layout = StructLayout(
            name=cv_struct.name,
            byte_size=cv_struct.byte_size,
            alignment=0,  # PDB doesn't store explicit alignment
            fields=fields,
            is_union=cv_struct.is_union,
            decl_file=(src_files or {}).get(cv_struct.name),
        )

        meta.structs[cv_struct.name] = layout


def _extract_fields(types: TypeDatabase, cv_struct: CvStruct) -> list[FieldInfo]:
    """Extract field information from a struct's fieldlist."""
    if cv_struct.field_list_ti == 0:
        return []

    members = types.get_fieldlist(cv_struct.field_list_ti)
    fields: list[FieldInfo] = []

    for member in members:
        if not isinstance(member, CvMember):
            continue
        if not member.name:
            continue

        type_name = types.type_name(member.type_ti)
        byte_size = types.type_size(member.type_ti)
        bit_offset = 0
        bit_size = 0

        # Check if the member type is a bitfield
        bf = types.get_bitfield(member.type_ti)
        if bf is not None:
            bit_size = bf.length
            bit_offset = bf.position
            # For bitfields, resolve the underlying type name and size
            type_name = types.type_name(bf.underlying_ti)
            byte_size = types.type_size(bf.underlying_ti)

        fields.append(FieldInfo(
            name=member.name,
            type_name=type_name,
            byte_offset=member.offset,
            byte_size=byte_size,
            bit_offset=bit_offset,
            bit_size=bit_size,
        ))

    return fields


# ---------------------------------------------------------------------------
# Phase 2: Enum types
# ---------------------------------------------------------------------------

def _extract_enums(
    types: TypeDatabase,
    meta: DwarfMetadata,
    src_files: dict[str, str] | None = None,
) -> None:
    """Extract enum types from TPI into DwarfMetadata.enums."""
    for ti, cv_enum in types.all_enums().items():
        if not _is_user_visible(cv_enum.name, cv_enum.is_forward_ref):
            continue

        underlying_size = types.type_size(cv_enum.underlying_type_ti)

        members: dict[str, int] = {}
        field_members = types.get_fieldlist(cv_enum.field_list_ti)
        for m in field_members:
            if isinstance(m, CvEnumerator) and m.name:
                members[m.name] = m.value

        enum_info = EnumInfo(
            name=cv_enum.name,
            underlying_byte_size=underlying_size,
            members=members,
            decl_file=(src_files or {}).get(cv_enum.name),
        )

        if cv_enum.name not in meta.enums:
            meta.enums[cv_enum.name] = enum_info


# ---------------------------------------------------------------------------
# Phase 4: Toolchain / compiler info from DBI
# ---------------------------------------------------------------------------

def _extract_toolchain_info(pdb: PdbFile, adv: AdvancedDwarfMetadata) -> None:
    """Extract compiler/toolchain info from DBI stream header."""
    if pdb.dbi is None:
        return

    h = pdb.dbi.header
    machine = _machine_name(h.machine)

    # BuildNumber: bits 0-7 = minor, bits 8-14 = major, bit 15 = new format
    major = (h.build_number >> 8) & 0x7F
    minor = h.build_number & 0xFF
    # Construct a producer-like string from DBI metadata
    producer = f"MSVC {major}.{minor}"
    if machine:
        producer += f" ({machine})"

    abi_flags: set[str] = set()
    # Machine type implies ABI
    if h.machine == 0x014C:
        abi_flags.add("-m32")
    elif h.machine == 0x8664:
        abi_flags.add("-m64")
    elif h.machine == 0xAA64:
        abi_flags.add("-marm64")

    # Check for incremental linking
    if h.flags & 0x01:
        abi_flags.add("/INCREMENTAL")

    adv.toolchain = ToolchainInfo(
        producer_string=producer,
        compiler="MSVC",
        version=f"{major}.{minor}",
        abi_flags=abi_flags,
    )

    # Try to extract more detailed info from module names
    for mod in pdb.dbi.modules:
        obj = mod.obj_file_name
        if not obj:
            continue
        # Look for MSVC version patterns in obj paths
        # e.g. "C:\Program Files\...\VC\Tools\MSVC\14.36.32532\..."
        m = re.search(r"MSVC[\\/](\d+\.\d+\.\d+)", obj)
        if m:
            adv.toolchain.version = m.group(1)
            adv.toolchain.producer_string = f"MSVC {m.group(1)} ({machine})"
            break
