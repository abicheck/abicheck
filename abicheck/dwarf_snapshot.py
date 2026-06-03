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

"""DwarfSnapshotBuilder — build a complete AbiSnapshot from DWARF alone.

ADR-003: When no headers are provided but DWARF debug info is present,
this module builds a full AbiSnapshot from DWARF .debug_info, enabling
24/30 detectors (vs 6 in symbol-only mode).

DWARF provides: function signatures, struct/class layouts, enum definitions,
variables, typedefs, inheritance, vtable entries, templates.
DWARF does NOT provide: #define constants, default parameter values.

Visibility filtering: only types/functions reachable from ELF exported
symbols are included (DWARF × ELF intersection).
"""
from __future__ import annotations

import collections
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .dwarf_utils import attr_bool as _attr_bool
from .dwarf_utils import attr_int as _attr_int
from .dwarf_utils import attr_str as _attr_str
from .dwarf_utils import decode_member_location as _decode_member_location
from .dwarf_utils import has_real_dwarf_info
from .dwarf_utils import resolve_die_ref as _resolve_ref
from .dwarf_utils import resolve_type_die as _resolve_type_die
from .elf_symbol_filter import is_abi_relevant_elf_symbol
from .model import (
    AbiSnapshot,
    AccessLevel,
    EnumMember,
    EnumType,
    Function,
    Param,
    ParamKind,
    RecordType,
    TypeField,
    Variable,
    Visibility,
    is_cxx_runtime_library,
)
from .model import (
    is_compiler_internal_type as _is_compiler_internal,
)

if TYPE_CHECKING:
    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .elf_metadata import ElfMetadata

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build_snapshot_from_dwarf(
    elf_path: Path,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    *,
    version: str = "unknown",
    language_profile: str | None = None,
) -> AbiSnapshot:
    """Build a complete AbiSnapshot from DWARF, no headers required.

    Args:
        elf_path: Path to the ELF binary.
        elf_meta: Pre-parsed ELF metadata (for exported symbol set).
        dwarf_meta: Pre-parsed DWARF basic metadata (structs, enums).
        dwarf_adv: Pre-parsed DWARF advanced metadata.
        version: Version label for the snapshot.
        language_profile: "c" | "cpp" | None.

    Returns:
        AbiSnapshot with functions, variables, types, enums, and typedefs
        populated from DWARF. elf_only_mode=False (full type info available).
    """
    builder = _DwarfSnapshotBuilder(elf_path, elf_meta)
    builder.extract()

    snapshot = AbiSnapshot(
        library=elf_path.name,
        version=version,
        functions=builder.functions,
        variables=builder.variables,
        types=builder.types,
        enums=builder.enums,
        typedefs=builder.typedefs,
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        elf_only_mode=False,
        platform="elf",
        language_profile=language_profile,
    )
    return snapshot


def show_data_sources(
    elf_path: Path,
    elf_meta: ElfMetadata | None,
    dwarf_meta: DwarfMetadata | None,
    has_headers: bool,
) -> str:
    """Generate human-readable data source diagnostic output.

    Returns a multi-line string describing which data layers are available.
    """
    lines: list[str] = [f"Data sources for {elf_path.name}:"]

    # L0: Binary metadata
    if elf_meta is not None:
        soname = elf_meta.soname or "none"
        n_syms = len(elf_meta.symbols) if elf_meta.symbols else 0
        lines.append(
            f"  L0 Binary metadata: ELF (SONAME={soname}, "
            f"{n_syms} exported symbols)"
        )
    else:
        lines.append("  L0 Binary metadata: not available")

    # L1: Debug info
    if dwarf_meta is not None and dwarf_meta.has_dwarf:
        n_types = len(dwarf_meta.structs)
        n_enums = len(dwarf_meta.enums)
        lines.append(
            f"  L1 Debug info:      DWARF ({n_types} types, {n_enums} enums)"
        )
    else:
        lines.append("  L1 Debug info:      not available (no DWARF)")

    # L2: Header AST
    if has_headers:
        lines.append("  L2 Header AST:      available (castxml)")
    else:
        lines.append("  L2 Header AST:      not available (no -H provided)")

    lines.append("")

    # Mode determination
    if has_headers:
        lines.append("Using: Headers mode (30/30 detectors active)")
    elif dwarf_meta is not None and dwarf_meta.has_dwarf:
        lines.append("Using: DWARF-only mode (24/30 detectors active)")
        lines.append("Missing: #define constants, default parameter values")
    else:
        lines.append("Using: Symbols-only mode (6/30 detectors active)")
        lines.append("Missing: type information, function signatures")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internal builder
# ---------------------------------------------------------------------------

class _DwarfSnapshotBuilder:
    """Extract ABI snapshot fields from DWARF .debug_info.

    Walks all CUs, extracting functions, variables, types, enums, and
    typedefs. Filters to ABI-relevant items via ELF exported symbol
    intersection.
    """

    def __init__(self, elf_path: Path, elf_meta: ElfMetadata) -> None:
        elf_path = Path(elf_path)
        self._elf_path = elf_path
        self._elf_meta = elf_meta
        self._filter_transitive_runtime_symbols = not (
            is_cxx_runtime_library(elf_path.name)
            or is_cxx_runtime_library(elf_meta.soname)
        )

        # Build exported symbol sets from ELF metadata, excluding
        # hidden/internal visibility symbols (not part of the public ABI)
        _HIDDEN_VIS = {"hidden", "internal"}
        self._exported_names: set[str] = set()
        if elf_meta.symbols:
            for sym in elf_meta.symbols:
                if (
                    sym.name
                    and sym.visibility not in _HIDDEN_VIS
                    and self._is_abi_relevant_export(sym.name)
                ):
                    self._exported_names.add(sym.name)

        # Build demangled export index for C++ fallback matching (FIX-B).
        # This allows matching DWARF functions against ELF exports even when
        # the mangled names differ slightly (e.g. const qualifier variance).
        self._demangled_exports: set[str] = set()
        from .demangle import demangle_batch
        cpp_names = [s for s in self._exported_names if s.startswith("_Z")]
        if cpp_names:
            demangled_map = demangle_batch(cpp_names)
            self._demangled_exports = set(demangled_map.values())

        # Results
        self.functions: list[Function] = []
        self.variables: list[Variable] = []
        self.types: list[RecordType] = []
        self.enums: list[EnumType] = []
        self.typedefs: dict[str, str] = {}

        # Type resolution cache: (cu_offset, die_offset) -> (name, byte_size)
        self._type_cache: dict[tuple[int, int], tuple[str, int]] = {}

        # Track types referenced by exported functions/variables for
        # transitive reachability filtering
        self._referenced_type_names: set[str] = set()

        # Dedup: prevent double-registration of types/enums across CUs
        self._seen_type_names: set[str] = set()
        self._seen_enum_names: set[str] = set()
        self._seen_func_mangles: set[str] = set()
        self._seen_var_mangles: set[str] = set()

        # FIX-I: throttle duplicate-type/enum debug messages to first occurrence
        self._logged_type_dups: set[str] = set()
        self._logged_enum_dups: set[str] = set()

    def extract(self) -> None:
        """Open the ELF, walk DWARF, and populate result lists."""
        try:
            with open(self._elf_path, "rb") as f:
                elf = ELFFile(f)  # type: ignore[no-untyped-call]
                if not has_real_dwarf_info(elf):
                    return
                dwarf = elf.get_dwarf_info()  # type: ignore[no-untyped-call]

                # First pass: extract all functions, variables, types, enums, typedefs
                for CU in dwarf.iter_CUs():
                    try:
                        self._process_cu(CU)
                    except Exception as exc:  # noqa: BLE001
                        log.warning(
                            "dwarf_snapshot: skipping CU in %s: %s",
                            self._elf_path, exc,
                        )

                # Second pass: filter types to only those reachable from
                # exported symbols (transitive closure)
                self._filter_types_by_reachability()

        except (ELFError, OSError, ValueError) as exc:
            log.warning(
                "dwarf_snapshot: failed to parse %s: %s",
                self._elf_path, exc,
            )

    def _process_cu(self, CU: Any) -> None:
        """Walk all DIEs in one Compilation Unit."""
        top_die = CU.get_top_DIE()
        stack: collections.deque[tuple[Any, str]] = collections.deque(
            [(top_die, "")]
        )

        while stack:
            die, scope = stack.pop()
            tag = die.tag

            # Skip function bodies / inlined frames — we handle
            # DW_TAG_subprogram at the top level only
            if tag in ("DW_TAG_inlined_subroutine", "DW_TAG_lexical_block",
                        "DW_TAG_GNU_call_site"):
                continue

            die_name = _attr_str(die, "DW_AT_name")
            next_scope = scope

            if tag == "DW_TAG_namespace" and die_name:
                next_scope = f"{scope}::{die_name}" if scope else die_name
            elif tag == "DW_TAG_subprogram":
                self._process_subprogram(die, CU, scope)
                continue  # don't descend into function body
            elif tag == "DW_TAG_variable":
                self._process_variable(die, CU, scope)
            elif tag in ("DW_TAG_structure_type", "DW_TAG_class_type",
                         "DW_TAG_union_type"):
                qualified = (
                    f"{scope}::{die_name}" if (scope and die_name) else die_name
                )
                self._process_record_type(die, CU, scope)
                if die_name:
                    next_scope = qualified or scope
            elif tag == "DW_TAG_enumeration_type":
                self._process_enum(die, CU, scope)
            elif tag == "DW_TAG_typedef":
                self._process_typedef(die, CU, scope)

            for child in reversed(list(die.iter_children())):
                stack.append((child, next_scope))

    # -------------------------------------------------------------------
    # Subprogram (function) extraction
    # -------------------------------------------------------------------

    def _process_subprogram(self, die: Any, CU: Any, scope: str) -> None:
        """Extract a function from DW_TAG_subprogram."""
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return

        # Get linkage name (mangled name for C++)
        linkage_name = _attr_str(die, "DW_AT_linkage_name")
        if not linkage_name:
            linkage_name = _attr_str(die, "DW_AT_MIPS_linkage_name")
        mangled = linkage_name or name
        qualified_name = f"{scope}::{name}" if scope else name

        # Deleted functions intentionally bypass the exported-symbol check below
        # so a public API that becomes ``= delete`` is still observable. Do not
        # let that bypass re-admit transitive stdlib/runtime subprograms into a
        # non-runtime library's public surface.
        if (
            not self._is_abi_relevant_export(mangled)
            or not self._is_abi_relevant_export(qualified_name)
        ):
            return

        # DWARF5 DW_AT_deleted: function marked as = delete by the compiler.
        # Currently only emitted by very recent compilers (not yet in GCC/Clang mainline).
        # Future-proofing: check for the attribute and set is_deleted on the Function.
        is_deleted = _attr_bool(die, "DW_AT_deleted")

        # Skip declarations without definitions (DW_AT_declaration=True)
        # BUT: if it's marked deleted, we still want to record it for cross-reference.
        if _attr_bool(die, "DW_AT_declaration") and not is_deleted:
            return

        # Visibility: must be in ELF exported symbols.
        # BUT: deleted functions won't have symbols in the new binary, so bypass
        # the export check — we need them in the snapshot for cross-reference.
        if not is_deleted and not self._is_exported(mangled, name):
            return

        # Dedup
        if mangled in self._seen_func_mangles:
            return
        self._seen_func_mangles.add(mangled)

        # Return type
        ret_type = "void"
        ret_ptr_depth = 0
        if "DW_AT_type" in die.attributes:
            ret_type, _ = self._resolve_type(die, CU)
            ret_ptr_depth = self._count_pointer_depth(die, CU)
        self._referenced_type_names.add(ret_type)

        # Parameters
        params: list[Param] = []
        for child in die.iter_children():
            if child.tag == "DW_TAG_formal_parameter":
                p = self._process_param(child, CU)
                if p is not None:
                    params.append(p)

        # Qualifiers
        is_virtual = _attr_bool(die, "DW_AT_virtuality") or (
            _attr_int(die, "DW_AT_virtuality") > 0
        )
        is_pure_virtual = _attr_int(die, "DW_AT_virtuality") == 2  # DW_VIRTUALITY_pure_virtual
        is_extern_c = not mangled.startswith("_Z")
        is_static = not _attr_bool(die, "DW_AT_external")
        # DW_AT_explicit is emitted by g++/clang++ on ctors and conversion ops
        # whose source-level declaration carries the `explicit` specifier
        # (C++20 conditional `explicit(bool)` collapses to its resolved bool).
        # Tri-state: True/False are authoritative readings from DWARF; the
        # snapshot loader defaults to None for older snapshots that predate
        # this field, so the diff can distinguish "unknown" from "implicit".
        is_explicit: bool | None = _attr_bool(die, "DW_AT_explicit")

        # Access level
        access_val = _attr_int(die, "DW_AT_accessibility")
        access = self._access_from_dwarf(access_val)

        # Vtable index
        vtable_index: int | None = None
        if "DW_AT_vtable_elem_location" in die.attributes:
            vtable_index = _attr_int(die, "DW_AT_vtable_elem_location")

        self.functions.append(Function(
            name=qualified_name if scope else name,
            mangled=mangled,
            return_type=ret_type,
            params=params,
            visibility=Visibility.PUBLIC,
            is_virtual=is_virtual,
            is_extern_c=is_extern_c,
            vtable_index=vtable_index,
            is_static=is_static,
            is_pure_virtual=is_pure_virtual,
            access=access,
            return_pointer_depth=ret_ptr_depth,
            is_deleted=is_deleted,
            deleted_from_dwarf=is_deleted,
            is_explicit=is_explicit,
        ))

    def _is_abi_relevant_export(self, name: str) -> bool:
        """Return True when *name* belongs to this DSO's own ABI surface."""
        if not self._filter_transitive_runtime_symbols:
            return bool(name)
        return is_abi_relevant_elf_symbol(name)

    def _process_param(self, die: Any, CU: Any) -> Param | None:
        """Extract a parameter from DW_TAG_formal_parameter."""
        name = _attr_str(die, "DW_AT_name")
        if "DW_AT_type" not in die.attributes:
            return Param(name=name, type="?")

        type_name, _ = self._resolve_type(die, CU)
        self._referenced_type_names.add(type_name)

        ptr_depth = self._count_pointer_depth(die, CU)
        kind = ParamKind.VALUE
        if ptr_depth > 0:
            kind = ParamKind.POINTER

        # Detect reference types
        type_die = _resolve_type_die(die, CU)
        if type_die is not None:
            if type_die.tag == "DW_TAG_reference_type":
                kind = ParamKind.REFERENCE
            elif type_die.tag == "DW_TAG_rvalue_reference_type":
                kind = ParamKind.RVALUE_REF

        return Param(
            name=name,
            type=type_name,
            kind=kind,
            pointer_depth=ptr_depth,
        )

    # -------------------------------------------------------------------
    # Variable extraction
    # -------------------------------------------------------------------

    def _process_variable(self, die: Any, CU: Any, scope: str) -> None:
        """Extract a variable from DW_TAG_variable."""
        # Only externally visible variables
        if not _attr_bool(die, "DW_AT_external"):
            return

        name = _attr_str(die, "DW_AT_name")
        if not name:
            return

        linkage_name = _attr_str(die, "DW_AT_linkage_name")
        if not linkage_name:
            linkage_name = _attr_str(die, "DW_AT_MIPS_linkage_name")
        mangled = linkage_name or name

        if not self._is_exported(mangled, name):
            return

        if mangled in self._seen_var_mangles:
            return
        self._seen_var_mangles.add(mangled)

        type_name = "?"
        is_const = False
        if "DW_AT_type" in die.attributes:
            type_name, _ = self._resolve_type(die, CU)
            self._referenced_type_names.add(type_name)
            # Check for const qualifier
            type_die = _resolve_type_die(die, CU)
            if type_die is not None and type_die.tag == "DW_TAG_const_type":
                is_const = True

        qualified_name = f"{scope}::{name}" if scope else name

        self.variables.append(Variable(
            name=qualified_name if scope else name,
            mangled=mangled,
            type=type_name,
            visibility=Visibility.PUBLIC,
            is_const=is_const,
        ))

    # -------------------------------------------------------------------
    # Record type (struct/class/union) extraction
    # -------------------------------------------------------------------

    def _process_record_type(self, die: Any, CU: Any, scope: str) -> None:
        """Extract a struct/class/union from DWARF."""
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return  # anonymous — handled via typedef
        if _is_compiler_internal(name):
            return

        qualified = f"{scope}::{name}" if scope else name
        self._process_record_type_named(die, CU, qualified)

    def _process_record_type_named(
        self, die: Any, CU: Any, qualified: str
    ) -> None:
        """Extract a struct/class/union using a given qualified name."""
        byte_size = _attr_int(die, "DW_AT_byte_size")
        if byte_size == 0 and _attr_bool(die, "DW_AT_declaration"):
            return  # forward declaration only

        if qualified in self._seen_type_names:
            if qualified not in self._logged_type_dups:
                self._logged_type_dups.add(qualified)
                log.debug("Duplicate type skipped (first-wins): %s", qualified)
            return  # ODR: first definition wins
        self._seen_type_names.add(qualified)

        tag = die.tag
        is_union = tag == "DW_TAG_union_type"
        kind = "union" if is_union else ("class" if tag == "DW_TAG_class_type" else "struct")

        # Parse fields
        fields: list[TypeField] = []
        bases: list[str] = []
        virtual_bases: list[str] = []
        vtable: list[str] = []

        for child in die.iter_children():
            if child.tag == "DW_TAG_member":
                tf = self._process_field(child, CU)
                if tf is not None:
                    fields.append(tf)
            elif child.tag == "DW_TAG_inheritance":
                base_name = self._resolve_base_name(child, CU)
                if base_name:
                    is_virtual_base = _attr_int(child, "DW_AT_virtuality") > 0
                    if is_virtual_base:
                        virtual_bases.append(base_name)
                    else:
                        bases.append(base_name)
            elif child.tag == "DW_TAG_subprogram":
                # Collect virtual methods for vtable
                if _attr_int(child, "DW_AT_virtuality") > 0:
                    vt_mangled = (
                        _attr_str(child, "DW_AT_linkage_name")
                        or _attr_str(child, "DW_AT_MIPS_linkage_name")
                        or _attr_str(child, "DW_AT_name")
                    )
                    if vt_mangled:
                        vtable.append(vt_mangled)

        alignment = _attr_int(die, "DW_AT_alignment")
        is_opaque = byte_size == 0 and not fields

        # Extract source location from DW_AT_decl_file / DW_AT_decl_line
        source_loc = self._resolve_decl_file(die, CU)

        self.types.append(RecordType(
            name=qualified,
            kind=kind,
            size_bits=byte_size * 8 if byte_size > 0 else None,
            alignment_bits=alignment * 8 if alignment > 0 else None,
            fields=fields,
            bases=bases,
            virtual_bases=virtual_bases,
            vtable=vtable,
            is_union=is_union,
            is_opaque=is_opaque,
            source_location=source_loc,
        ))

    def _resolve_decl_file(self, die: Any, CU: Any) -> str | None:
        """Resolve DW_AT_decl_file to a source file path.

        Returns a string like ``"foo.c:42"`` or ``"foo.h"`` (without line if
        unavailable), or ``None`` if the attribute is absent.
        """
        if "DW_AT_decl_file" not in die.attributes:
            return None
        file_idx = die.attributes["DW_AT_decl_file"].value
        if file_idx == 0:
            return None  # no file
        try:
            line_prog = CU.dwarfinfo.line_program_for_CU(CU)
            if line_prog is None:
                return None
            # DWARF 4: file_entry is 1-indexed into line_prog['file_entry']
            # DWARF 5: file_entry is 0-indexed into line_prog.header['file_names']
            file_entries = line_prog.header.get("file_names") or []
            # pyelftools uses 1-based indexing for DWARF 4
            ver = CU.header.get("version", 4) if hasattr(CU, "header") else 4
            idx = file_idx - 1 if ver < 5 else file_idx
            if 0 <= idx < len(file_entries):
                entry = file_entries[idx]
                fname = entry.name if isinstance(entry.name, str) else entry.name.decode("utf-8", errors="replace")
                line = _attr_int(die, "DW_AT_decl_line")
                return f"{fname}:{line}" if line else fname
        except Exception:  # noqa: BLE001
            log.debug("Failed to resolve source location for DIE")
        return None

    def _process_field(self, die: Any, CU: Any) -> TypeField | None:
        """Extract a struct/class/union field."""
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return None  # anonymous member (padding or anonymous aggregate)

        type_name = "?"
        if "DW_AT_type" in die.attributes:
            type_name, _ = self._resolve_type(die, CU)

        # Byte offset
        byte_offset = 0
        if "DW_AT_data_member_location" in die.attributes:
            byte_offset = _decode_member_location(
                die.attributes["DW_AT_data_member_location"].value
            )

        offset_bits = byte_offset * 8

        # Bitfield
        bit_size = _attr_int(die, "DW_AT_bit_size")
        is_bitfield = bit_size > 0
        bitfield_bits = bit_size if is_bitfield else None

        if is_bitfield:
            if "DW_AT_data_bit_offset" in die.attributes:
                offset_bits = _attr_int(die, "DW_AT_data_bit_offset")
            elif "DW_AT_bit_offset" in die.attributes:
                offset_bits = byte_offset * 8 + _attr_int(die, "DW_AT_bit_offset")

        # Access level
        access_val = _attr_int(die, "DW_AT_accessibility")
        access = self._access_from_dwarf(access_val)

        # Const / volatile
        is_const = False
        is_volatile = False
        type_die = _resolve_type_die(die, CU)
        if type_die is not None:
            if type_die.tag == "DW_TAG_const_type":
                is_const = True
            elif type_die.tag == "DW_TAG_volatile_type":
                is_volatile = True

        return TypeField(
            name=name,
            type=type_name,
            offset_bits=offset_bits,
            is_bitfield=is_bitfield,
            bitfield_bits=bitfield_bits,
            is_const=is_const,
            is_volatile=is_volatile,
            access=access,
        )

    def _resolve_base_name(self, die: Any, CU: Any) -> str:
        """Resolve DW_TAG_inheritance → base class name."""
        if "DW_AT_type" not in die.attributes:
            return ""
        try:
            base_die = _resolve_ref(die, "DW_AT_type", CU)
            return _attr_str(base_die, "DW_AT_name") or ""
        except Exception:  # noqa: BLE001
            return ""

    # -------------------------------------------------------------------
    # Enum extraction
    # -------------------------------------------------------------------

    def _process_enum(self, die: Any, CU: Any, scope: str) -> None:
        """Extract an enum from DWARF."""
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return
        if _is_compiler_internal(name):
            return

        qualified = f"{scope}::{name}" if scope else name
        self._process_enum_named(die, CU, qualified)

    def _process_enum_named(self, die: Any, CU: Any, qualified: str) -> None:
        """Extract an enum using a given qualified name."""
        if qualified in self._seen_enum_names:
            if qualified not in self._logged_enum_dups:
                self._logged_enum_dups.add(qualified)
                log.debug("Duplicate enum skipped (first-wins): %s", qualified)
            return
        self._seen_enum_names.add(qualified)

        byte_size = _attr_int(die, "DW_AT_byte_size")
        if byte_size == 0:
            return  # declaration-only

        # Underlying type
        underlying = "int"
        if "DW_AT_type" in die.attributes:
            underlying, _ = self._resolve_type(die, CU)

        members: list[EnumMember] = []
        for child in die.iter_children():
            if child.tag == "DW_TAG_enumerator":
                m_name = _attr_str(child, "DW_AT_name")
                m_val = _attr_int(child, "DW_AT_const_value")
                if m_name:
                    members.append(EnumMember(name=m_name, value=m_val))

        self.enums.append(EnumType(
            name=qualified,
            members=members,
            underlying_type=underlying,
            source_location=self._resolve_decl_file(die, CU),
        ))

    # -------------------------------------------------------------------
    # Typedef extraction
    # -------------------------------------------------------------------

    def _process_typedef(self, die: Any, CU: Any, scope: str = "") -> None:
        """Extract a typedef from DWARF.

        Also registers anonymous structs/enums under the typedef name
        (e.g. ``typedef struct { int x; } Point;``).

        The typedef is keyed by its *namespace-qualified* name (consistent with
        records and enums) so that standard-library typedefs nested under ``std``
        (e.g. ``std::vector<…>::size_type``) carry their ``std::`` scope and the
        non-ABI-surface filter can recognise them (validation/REPORT.md FP-1).
        """
        name = _attr_str(die, "DW_AT_name")
        if not name:
            return
        if _is_compiler_internal(name):
            return
        qualified = f"{scope}::{name}" if scope else name

        # Check if this typedef points to an anonymous struct/enum
        if "DW_AT_type" in die.attributes:
            try:
                target = _resolve_ref(die, "DW_AT_type", CU)
                target_name = _attr_str(target, "DW_AT_name")
                target_tag = target.tag

                if target_tag in ("DW_TAG_structure_type", "DW_TAG_class_type",
                                  "DW_TAG_union_type"):
                    if not target_name and qualified not in self._seen_type_names:
                        # Anonymous struct/union — register under the qualified
                        # typedef name so same-named typedefs in different
                        # namespaces don't collide on the bare alias.
                        self._process_record_type_named(target, CU, qualified)
                elif target_tag == "DW_TAG_enumeration_type":
                    if not target_name and qualified not in self._seen_enum_names:
                        # Anonymous enum — register under the qualified typedef name
                        self._process_enum_named(target, CU, qualified)
            except Exception:  # noqa: BLE001
                log.debug("Failed to process typedef target for %s", qualified)

        if qualified in self.typedefs:
            return  # first wins

        underlying = "?"
        if "DW_AT_type" in die.attributes:
            underlying = self._resolve_underlying_type(die, CU, depth=0)

        self.typedefs[qualified] = underlying

    def _resolve_underlying_type(
        self, die: Any, CU: Any, depth: int
    ) -> str:
        """Follow typedef chains to the concrete base type."""
        if depth > 20:
            return "?"
        type_die = _resolve_type_die(die, CU)
        if type_die is None:
            return "?"
        if type_die.tag == "DW_TAG_typedef":
            return self._resolve_underlying_type(type_die, CU, depth + 1)
        name, _ = self._die_to_type_name(type_die, CU, depth=0)
        return name

    # -------------------------------------------------------------------
    # Visibility filtering
    # -------------------------------------------------------------------

    def _is_exported(self, mangled: str, name: str) -> bool:
        """Check if a symbol is in the ELF exported symbol set.

        Three-tier matching (FIX-B):
        1. Exact mangled name match (fast path)
        2. Plain name match (C functions)
        3. Demangled fallback (C++ with mangling variance)
        """
        # Tier 1: exact mangled match
        if mangled and mangled in self._exported_names:
            return True
        # Tier 2: plain name match (C functions)
        if name and name in self._exported_names:
            return True
        # Tier 3: demangled fallback for C++ (FIX-B)
        if mangled and mangled.startswith("_Z") and self._demangled_exports:
            from .demangle import demangle as _demangle
            demangled = _demangle(mangled)
            if demangled and demangled in self._demangled_exports:
                return True
        return False

    def _filter_types_by_reachability(self) -> None:
        """Filter types and enums to only those reachable from exports.

        After functions and variables are extracted, _referenced_type_names
        contains the directly referenced types. We transitively include
        types referenced by fields of those types.
        """
        # Build type name → RecordType index
        type_map: dict[str, RecordType] = {t.name: t for t in self.types}

        # Transitive closure: BFS from referenced types
        queue = collections.deque(self._referenced_type_names)
        reachable: set[str] = set(self._referenced_type_names)

        while queue:
            type_name = queue.popleft()
            rec = type_map.get(type_name)
            if rec is None:
                continue
            for field in rec.fields:
                # Strip pointer/reference/const/volatile/array suffixes
                base = _strip_type_decorators(field.type)
                if base not in reachable:
                    reachable.add(base)
                    queue.append(base)
            for base_name in rec.bases + rec.virtual_bases:
                if base_name not in reachable:
                    reachable.add(base_name)
                    queue.append(base_name)

        # Filter: keep all types for now (DWARF types from file-scope are
        # generally ABI-relevant). The reachability set is stored for
        # future stricter filtering if needed.
        # NOTE: We don't filter aggressively because DWARF file-scope types
        # (structs, enums visible in headers) are almost always ABI-relevant.
        # Over-filtering would miss important type changes.

    # -------------------------------------------------------------------
    # Type resolution helpers
    # -------------------------------------------------------------------

    def _resolve_type(self, die: Any, CU: Any) -> tuple[str, int]:
        """Return (type_name, byte_size) for the type referenced by die."""
        if "DW_AT_type" not in die.attributes:
            return ("void", 0)
        try:
            type_die = _resolve_ref(die, "DW_AT_type", CU)
            return self._die_to_type_name(type_die, CU, depth=0)
        except Exception:  # noqa: BLE001
            return ("?", 0)

    def _die_to_type_name(
        self, die: Any, CU: Any, depth: int
    ) -> tuple[str, int]:
        """Resolve a type DIE to (name, byte_size) with caching."""
        if depth > 8:
            return ("...", 0)

        cache_key = (CU.cu_offset, die.offset)
        if cache_key in self._type_cache:
            return self._type_cache[cache_key]

        result = self._compute_type_name(die, CU, depth)
        self._type_cache[cache_key] = result
        return result

    def _compute_type_name(
        self, die: Any, CU: Any, depth: int
    ) -> tuple[str, int]:
        """Compute type name from a DWARF type DIE."""
        tag = die.tag

        if tag == "DW_TAG_base_type":
            return (
                _attr_str(die, "DW_AT_name") or "base",
                _attr_int(die, "DW_AT_byte_size"),
            )

        if tag in ("DW_TAG_structure_type", "DW_TAG_class_type",
                    "DW_TAG_union_type"):
            name = _attr_str(die, "DW_AT_name") or "<anon>"
            return (name, _attr_int(die, "DW_AT_byte_size"))

        if tag == "DW_TAG_enumeration_type":
            name = _attr_str(die, "DW_AT_name") or "<enum>"
            return (f"enum {name}", _attr_int(die, "DW_AT_byte_size"))

        if tag == "DW_TAG_pointer_type":
            inner = self._resolve_inner_name(die, CU, depth)
            size = _attr_int(die, "DW_AT_byte_size") or 0
            return (f"{inner} *" if inner else "void *", size)

        if tag == "DW_TAG_reference_type":
            inner = self._resolve_inner_name(die, CU, depth)
            size = _attr_int(die, "DW_AT_byte_size") or 0
            return (f"{inner} &" if inner else "? &", size)

        if tag == "DW_TAG_rvalue_reference_type":
            inner = self._resolve_inner_name(die, CU, depth)
            size = _attr_int(die, "DW_AT_byte_size") or 0
            return (f"{inner} &&" if inner else "? &&", size)

        if tag in ("DW_TAG_const_type", "DW_TAG_volatile_type",
                    "DW_TAG_restrict_type"):
            qualifier = tag.split("_")[2].lower()
            inner_info = self._resolve_inner_info(die, CU, depth)
            if inner_info is None:
                return (qualifier, 0)
            return (f"{qualifier} {inner_info[0]}", inner_info[1])

        if tag == "DW_TAG_typedef":
            name = _attr_str(die, "DW_AT_name")
            inner_info = self._resolve_inner_info(die, CU, depth)
            if inner_info is None:
                return (name or "typedef", 0)
            return (name or inner_info[0], inner_info[1])

        if tag == "DW_TAG_array_type":
            size = _attr_int(die, "DW_AT_byte_size")
            inner = self._resolve_inner_name(die, CU, depth)
            return (
                f"{inner}[]" if inner else "array",
                size,
            )

        if tag == "DW_TAG_subroutine_type":
            return ("fn(...)", _attr_int(die, "DW_AT_byte_size"))

        # Fallback
        name = _attr_str(die, "DW_AT_name")
        return (name or tag or "unknown", _attr_int(die, "DW_AT_byte_size"))

    def _resolve_inner_name(
        self, die: Any, CU: Any, depth: int
    ) -> str | None:
        """Resolve inner type name (for pointer/reference/array types)."""
        info = self._resolve_inner_info(die, CU, depth)
        return info[0] if info is not None else None

    def _resolve_inner_info(
        self, die: Any, CU: Any, depth: int
    ) -> tuple[str, int] | None:
        """Resolve inner type info (for qualified/typedef types)."""
        if "DW_AT_type" not in die.attributes:
            return None
        try:
            inner_die = _resolve_ref(die, "DW_AT_type", CU)
            return self._die_to_type_name(inner_die, CU, depth + 1)
        except Exception:  # noqa: BLE001
            return None

    def _count_pointer_depth(self, die: Any, CU: Any, depth: int = 0) -> int:
        """Count pointer nesting: T=0, T*=1, T**=2."""
        if depth > 10:
            return 0
        type_die = _resolve_type_die(die, CU)
        if type_die is None:
            return 0
        if type_die.tag == "DW_TAG_pointer_type":
            return 1 + self._count_pointer_depth(type_die, CU, depth + 1)
        if type_die.tag in ("DW_TAG_const_type", "DW_TAG_volatile_type",
                            "DW_TAG_typedef"):
            return self._count_pointer_depth(type_die, CU, depth + 1)
        return 0

    @staticmethod
    def _access_from_dwarf(val: int) -> AccessLevel:
        """Map DW_AT_accessibility value to AccessLevel."""
        if val == 2:  # DW_ACCESS_protected
            return AccessLevel.PROTECTED
        if val == 3:  # DW_ACCESS_private
            return AccessLevel.PRIVATE
        return AccessLevel.PUBLIC  # 0 (absent) or 1 (DW_ACCESS_public)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _strip_type_decorators(type_name: str) -> str:
    """Strip pointer/reference/const/volatile/array suffixes from a type name.

    Used for reachability analysis — we need the base type name to follow
    type references transitively.
    """
    name = type_name.strip()
    # Remove trailing array brackets
    while name.endswith("[]"):
        name = name[:-2].strip()
    # Remove trailing pointer/reference markers
    while name.endswith(("*", "&", "&&")):
        if name.endswith("&&"):
            name = name[:-2].strip()
        else:
            name = name[:-1].strip()
    # Remove leading qualifiers (loop until none remain)
    _qualifiers = ("const ", "volatile ", "restrict ")
    changed = True
    while changed:
        changed = False
        for prefix in _qualifiers:
            if name.startswith(prefix):
                name = name[len(prefix):]
                changed = True
    return name.strip()
