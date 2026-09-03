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

from .model.dwarf_facts import (
    AdvancedDwarfMetadata,
    DwarfMetadata,
    EnumInfo,
    FieldInfo,
    StructLayout,
    ToolchainInfo,
)
from .model.qualified_name_split import split_top_level_scopes
from .pdb_parser import (
    CvEnumerator,
    CvMember,
    CvOneMethod,
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


#: MSVC/CodeView-synthesized namespace names positively known to be
#: compiler-internal, never a real (possibly vendor-customized) ABI-tag
#: inline namespace. See :func:`_is_user_visible`'s own docstring for why
#: this is a small, explicit denylist rather than an allowlist of
#: recognized ABI-tag shapes.
_KNOWN_COMPILER_INTERNAL_NAMESPACES: frozenset[str] = frozenset({"__vc_attributes"})


def _is_user_visible(name: str | None, is_forward_ref: bool) -> bool:
    """Return True if a PDB type should be included in metadata.

    Filters out forward references, unnamed types, and compiler-internal
    names. Checks every top-level ``"::"``-separated segment, not just the
    whole string: CodeView emits a fully-qualified name for a nested
    anonymous aggregate too (e.g. ``"N::O::<unnamed-tag>"`` for an unnamed
    struct/union nested inside ``N::O``), so a check against only the whole
    name's own prefix would admit that leaf as an ordinary named type
    (Codex review, PR #1025) — recorded under ``known_record_names`` and
    handed to `extract/pdb_scope.py` as a plain leaf, disagreeing with the
    ``Anonymous`` identity another producer would give the same
    declaration.

    A ``"<...>"``-prefixed NON-LEAF segment (an anonymous struct/union/enum
    embedded partway through an otherwise-named qualified spelling, e.g.
    ``"N::<unnamed-tag>::Inner"`` for a NAMED ``Inner`` nested inside an
    anonymous union that is itself nested inside ``N``) is admitted, not
    rejected (Codex review, PR #1025, fresh evidence): the leaf itself
    (``"Inner"``) is a real, user-visible declaration with real layout
    facts, and dropping it entirely because an ENCLOSING scope happens to
    be anonymous would lose those facts for no benefit — the identical
    "recoverable noise beats silent data loss" principle the ABI-tag
    admit-by-default rule below already applies. What is genuinely
    unrepresentable is not the leaf's inclusion but its *identity*:
    ``extract/pdb_scope.py`` builds no ``Anonymous`` scope segment (see
    that module's own docstring), so ``record_entity_id``/``enum_entity_id``
    leave ``entity_id`` unset (``None``) for such a qualified name instead
    of guessing a plain ``Namespace``/``Record`` segment for a scope
    CodeView never gave a real name — a type/enum with no ``entity_id``
    already contributes no ``SemanticIR`` occurrence but keeps its layout
    facts (``extract/semantic_normalizer.py``'s own documented contract),
    the same graceful degradation this case now uses. Only the LEAF segment
    itself being anonymous still means "this declaration has no real name
    to record" and is rejected outright, below.

    A ``__``-prefixed NON-LEAF segment is admitted by default, UNLESS it is
    positively known to be compiler-synthesized
    (:data:`_KNOWN_COMPILER_INTERNAL_NAMESPACES`, currently just MSVC's own
    ``__vc_attributes``). This used to be the reverse -- an allowlist gated
    on :func:`~abicheck.model.qualified_name_split.is_inline_abi_namespace_segment`
    (the recognizer ``diff_namespaces.py``/``diff_abi_tags.py`` treat as a
    legitimate, transparent scope elsewhere in the pipeline) -- but that
    closed enumeration (version-numbered tags, ``__cxxN``, ``__ndkN``) can
    never recognize a *customized* libc++ build: ``_LIBCPP_ABI_NAMESPACE``
    is a documented, build-configurable macro, so a vendor's own spelling
    (e.g. ``__vendor``) legitimately falls outside every known-standard tag
    shape (Codex review, PR #1025, fresh evidence). Rejecting an
    unrecognized-but-real ABI-tag namespace as "not on the allowlist" drops
    a real, user-visible declaration's layout facts, entity ID, and
    SemanticIR occurrence entirely -- the identical class of loss the
    original ``std::__1::vector<int>``/``std::__cxx11::basic_string<char>``
    fix (below) closed, just triggered by an unrecognized tag spelling
    rather than a missing exemption altogether. Admitting an unrecognized
    ``__``-prefixed non-leaf segment by default is the deliberately safer
    failure direction: an extra namespace scope in the model is recoverable
    noise, while silently dropping a real declaration is not. Without this
    admit-by-default rule, a per-segment ``__`` check would still drop a
    real user-visible type entirely (e.g.
    ``std::__1::vector<int>``, ``std::__cxx11::basic_string<char>``), losing
    its layout facts, entity ID, and SemanticIR occurrence rather than merely
    stripping the tag (Codex review, PR #1025).

    The admit-by-default rule applies ONLY to a non-leaf (enclosing-scope)
    segment, never to the final, declaration-naming segment itself (Codex
    review, second round, fresh evidence): a globally-named UDT literally
    called ``__1``, ``__v2`` or ``__cxx11`` is not an inline namespace at
    all -- it is the declaration's own leaf name, which happens to collide
    with an ABI-tag spelling -- and must still be rejected as
    compiler-internal the same way any other ``__``-prefixed leaf
    (``__vc_attributes``) is.
    """
    if is_forward_ref:
        return False
    if not name:
        return False
    segments = split_top_level_scopes(name)
    last_index = len(segments) - 1
    for index, segment in enumerate(segments):
        if segment.startswith("<"):
            if index == last_index:
                return False
            continue
        if not segment.startswith("__"):
            continue
        if index == last_index or segment in _KNOWN_COMPILER_INTERNAL_NAMESPACES:
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
    for _ti, cv_struct in types.all_structs().items():
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
            _extract_method_calling_conventions(types, cv_struct, adv)

        layout = StructLayout(
            name=cv_struct.name,
            byte_size=cv_struct.byte_size,
            alignment=0,  # PDB doesn't store explicit alignment
            fields=fields,
            is_union=cv_struct.is_union,
            decl_file=(src_files or {}).get(cv_struct.name),
        )

        meta.structs[cv_struct.name] = layout


def _extract_method_calling_conventions(
    types: TypeDatabase, cv_struct: CvStruct, adv: AdvancedDwarfMetadata
) -> None:
    """Record per-method calling conventions into *adv* (PDB → DWARF bridge).

    A PDB has no DWARF-style per-symbol linkage records in the streams we
    parse, but each class fieldlist names its non-overloaded methods
    (LF_ONEMETHOD) alongside their LF_MFUNCTION type — enough to feed
    ``AdvancedDwarfMetadata.calling_conventions`` keyed ``Class::method`` so
    ``CALLING_CONVENTION_CHANGED`` fires on MSVC builds too. Free functions
    (which would need the globals symbol stream) remain a documented gap.
    """
    if cv_struct.field_list_ti == 0:
        return
    for member in types.get_fieldlist(cv_struct.field_list_ti):
        if not isinstance(member, CvOneMethod):
            continue
        cc = types.function_calling_convention(member.type_ti)
        if cc is None:
            continue
        key = f"{cv_struct.name}::{member.name}"
        adv.calling_conventions.setdefault(key, types.calling_convention_name(cc))


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
    for _ti, cv_enum in types.all_enums().items():
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
