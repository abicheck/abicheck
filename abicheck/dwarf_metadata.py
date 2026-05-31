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

"""DWARF-aware type layout extraction via pyelftools.

Reads DWARF debug info from a compiled .so to extract:
- Struct/class/union sizes and field layouts (offsets, types)
- Enum underlying types and member values
- Alignment information

Requires binaries compiled with -g (DWARF debug info).
If DWARF is absent, returns empty DwarfMetadata gracefully.

See docs/adr/001-technology-stack.md — Sprint 3 layer.

## Design notes

### Iterative traversal
_walk_die_iter uses an explicit collections.deque to avoid Python's
recursion limit (default 1000). Real C++ DWARF trees with deep namespaces
and template specializations can exceed 200 DIE levels of nesting.

### CU-relative vs absolute DWARF references
In DWARF 4, DW_AT_type uses CU-relative offsets (DW_FORM_ref1/2/4/8/udata).
pyelftools' CompileUnit.cu_offset is the absolute position of the CU header
in .debug_info. _resolve_ref() handles both forms transparently.

### Type-resolution caching
_die_to_type_info results are memoized per parse call using a dict keyed by
(cu_offset, die_offset). This avoids the O(n×m) re-resolution overhead when
the same base type DIE (e.g. `int`) appears in hundreds of struct members.

### Bitfield offset handling (DWARF 4 vs DWARF 5)
- DWARF 2/3: DW_AT_bit_offset = bit offset from MSB of the storage unit
- DWARF 4+: DW_AT_data_bit_offset = bit offset from LSB of the container
  Both attributes are read; DW_AT_data_bit_offset takes priority when present.
"""
# pylint: disable=invalid-name  # CU is the standard DWARF term (Compilation Unit)
from __future__ import annotations

import collections
import logging
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .dwarf_utils import BASE_PRUNE_TAGS
from .dwarf_utils import attr_bool as _attr_bool  # noqa: F401
from .dwarf_utils import attr_int as _attr_int
from .dwarf_utils import attr_str as _attr_str
from .dwarf_utils import decode_member_location as _decode_member_location
from .dwarf_utils import resolve_die_ref as _resolve_ref

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class FieldInfo:
    """One field (member) inside a struct/union/class."""
    name: str
    type_name: str      # human-readable type (e.g. "int", "MyStruct *")
    byte_offset: int    # DW_AT_data_member_location
    byte_size: int      # size of the field's type (0 if unknown)
    bit_offset: int = 0 # for bitfields: normalised bit offset from LSB
    bit_size: int = 0 # for bitfields: width in bits (0 = not a bitfield)


@dataclass
class StructLayout:
    """Size and field layout of a struct/class/union."""
    name: str
    byte_size: int                          # DW_AT_byte_size
    alignment: int = 0                      # DW_AT_alignment (DWARF 5; 0 = unknown)
    fields: list[FieldInfo] = field(default_factory=list)
    is_union: bool = False
    # Defining source header, when the debug info records it. DWARF leaves this
    # None (decl-file is resolved on the DIE path); the PDB pipeline fills it
    # from LF_UDT_SRC_LINE / LF_UDT_MOD_SRC_LINE so provenance (ADR-024 Phase 1)
    # works for Windows binaries.
    decl_file: str | None = None


@dataclass
class EnumInfo:
    """Enum type: underlying integer type + all named members."""
    name: str
    underlying_byte_size: int               # sizeof underlying integer type
    members: dict[str, int] = field(default_factory=dict)  # name → value
    # Defining source header — see StructLayout.decl_file (ADR-024 Phase 1).
    decl_file: str | None = None


@dataclass
class DwarfMetadata:
    """All DWARF-derived ABI-relevant type information from one .so.

    Implements the TypeMetadataSource protocol (see type_metadata.py).
    """
    # name → StructLayout  (structs, classes, unions)
    structs: dict[str, StructLayout] = field(default_factory=dict)
    # name → EnumInfo
    enums: dict[str, EnumInfo] = field(default_factory=dict)
    has_dwarf: bool = False   # False = binary had no DWARF info

    # TypeMetadataSource protocol methods
    @property
    def has_data(self) -> bool:
        return self.has_dwarf

    def get_struct_layout(self, name: str) -> StructLayout | None:
        return self.structs.get(name)

    def get_enum_info(self, name: str) -> EnumInfo | None:
        return self.enums.get(name)


# Tags whose subtrees we never descend into (function-local types, inline
# frames, lexical blocks). Registering function-local structs as ABI
# surfaces would produce noise and false positives.

# Deduplicate unknown DWARF-tag warnings per process to avoid log flooding
_SEEN_UNKNOWN_DWARF_TAGS: set[str] = set()

_SKIP_TAGS: frozenset[str] = BASE_PRUNE_TAGS | frozenset({
    "DW_TAG_subprogram",
})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_dwarf_metadata(so_path: Path) -> DwarfMetadata:
    """Extract DWARF type layout metadata from *so_path*.

    Returns empty DwarfMetadata (has_dwarf=False) if the binary has no
    debug info or cannot be parsed. Never raises.
    """
    try:
        with open(so_path, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("parse_dwarf_metadata: not a regular file: %s", so_path)
                return DwarfMetadata()
            return _parse(f, so_path)
    except (ELFError, OSError, ValueError) as exc:
        log.warning("parse_dwarf_metadata: failed to open/parse %s: %s", so_path, exc)
        return DwarfMetadata()


# ---------------------------------------------------------------------------
# Internal implementation
# ---------------------------------------------------------------------------

def _parse(f: Any, so_path: Path) -> DwarfMetadata:
    meta = DwarfMetadata()
    elf = ELFFile(f)  # type: ignore[no-untyped-call]

    if not elf.has_dwarf_info():  # type: ignore[no-untyped-call]
        log.debug("parse_dwarf_metadata: no DWARF info in %s", so_path)
        return meta

    meta.has_dwarf = True
    dwarf = elf.get_dwarf_info()  # type: ignore[no-untyped-call]

    # Per-parse type-resolution cache: (cu_offset, die_offset) → (name, byte_size)
    type_cache: dict[tuple[int, int], tuple[str, int]] = {}

    for CU in dwarf.iter_CUs():  # type: ignore[no-untyped-call]
        try:
            _process_cu(CU, meta, type_cache)
        except Exception as exc:  # noqa: BLE001
            log.warning("parse_dwarf_metadata: skipping CU in %s: %s", so_path, exc)

    return meta


def _process_cu(
    CU: Any,
    meta: DwarfMetadata,
    type_cache: dict[tuple[int, int], tuple[str, int]],
) -> None:
    """Walk all DIEs in one Compilation Unit (iterative, no recursion)."""
    top_die = CU.get_top_DIE()
    _walk_die_iter(top_die, meta, CU, type_cache)


def _walk_die_iter(
    root_die: Any,
    meta: DwarfMetadata,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
) -> None:
    """Iterative depth-first DIE traversal with scope-qualified names.

    Carries a scope prefix (e.g. "MyNS::MyClass") through the stack so that
    identically-named types in different namespaces/classes do not collide in
    meta.structs / meta.enums.

    Uses an explicit stack to avoid Python's default recursion limit (1000),
    which can be exceeded by deeply-nested C++ template/namespace DIE trees.
    Skips subtrees rooted at function-local tags (_SKIP_TAGS).
    """
    # Stack items: (die, scope_prefix)
    stack: collections.deque[tuple[Any, str]] = collections.deque([(root_die, "")])

    while stack:
        die, scope = stack.pop()
        tag = die.tag

        if tag in _SKIP_TAGS:
            continue  # don't descend into function bodies or inlined frames

        # Determine whether this DIE contributes a scope component
        # (namespaces and named classes extend the scope prefix)
        die_name = _attr_str(die, "DW_AT_name")
        next_scope = scope

        if tag == "DW_TAG_namespace" and die_name:
            next_scope = f"{scope}::{die_name}" if scope else die_name
        elif tag in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
            qualified = f"{scope}::{die_name}" if (scope and die_name) else die_name
            _process_struct(die, meta, CU, type_cache, scope_prefix=scope)
            if die_name:
                next_scope = qualified  # nested types use this as their scope
        elif tag == "DW_TAG_enumeration_type":
            _process_enum(die, meta, CU, scope_prefix=scope)
        elif tag == "DW_TAG_typedef":
            _process_typedef(die, meta, CU, type_cache)

        # Push children in reverse order so left-to-right DFS order is preserved
        for child in reversed(list(die.iter_children())):
            stack.append((child, next_scope))


def _process_typedef(
    die: Any,
    meta: DwarfMetadata,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
) -> None:
    """If a typedef points to an anonymous struct/enum, register it under the typedef name."""
    typedef_name = _attr_str(die, "DW_AT_name")
    if not typedef_name:
        return
    if "DW_AT_type" not in die.attributes:
        return
    try:
        target = _resolve_ref(die, "DW_AT_type", CU)
    except Exception:  # noqa: BLE001
        return

    tag = target.tag
    target_name = _attr_str(target, "DW_AT_name")

    if tag in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        if not target_name and typedef_name not in meta.structs:
            _process_struct_named(target, meta, CU, type_cache, override_name=typedef_name)
    elif tag == "DW_TAG_enumeration_type":
        if not target_name and typedef_name not in meta.enums:
            _process_enum_named(target, meta, CU, override_name=typedef_name)


# ---------------------------------------------------------------------------
# Struct / class / union
# ---------------------------------------------------------------------------

def _process_struct(
    die: Any,
    meta: DwarfMetadata,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    scope_prefix: str = "",
) -> None:
    name = _attr_str(die, "DW_AT_name")
    if not name:
        return  # anonymous — handled via typedef in _process_typedef
    qualified = f"{scope_prefix}::{name}" if scope_prefix else name
    _process_struct_named(die, meta, CU, type_cache, override_name=qualified)


def _process_struct_named(
    die: Any,
    meta: DwarfMetadata,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    override_name: str | None,
) -> None:
    name = override_name or _attr_str(die, "DW_AT_name")
    if not name:
        return

    byte_size = _attr_int(die, "DW_AT_byte_size")
    if byte_size == 0:
        return  # declaration-only (DW_AT_declaration) — no layout info

    is_union = die.tag == "DW_TAG_union_type"
    alignment = _attr_int(die, "DW_AT_alignment")  # DWARF 5; 0 if absent

    layout = StructLayout(
        name=name,
        byte_size=byte_size,
        alignment=alignment,
        is_union=is_union,
    )

    for child in die.iter_children():
        if child.tag != "DW_TAG_member":
            continue
        child_name = _attr_str(child, "DW_AT_name")
        if not child_name:
            # Anonymous member — may be an anonymous struct/union; inline its fields
            anon_offset = 0
            if "DW_AT_data_member_location" in child.attributes:
                anon_offset = _decode_member_location(
                    child.attributes["DW_AT_data_member_location"].value
                )
            layout.fields.extend(
                _expand_anonymous_member(child, CU, type_cache, anon_offset)
            )
        else:
            fi = _process_member(child, CU, type_cache)
            if fi is not None:
                layout.fields.append(fi)

    # ODR: keep the first complete definition.
    if name in meta.structs:
        existing = meta.structs[name]
        if existing.byte_size != layout.byte_size:
            log.debug(
                "ODR size mismatch for %s: %d vs %d bytes (keeping first)",
                name, existing.byte_size, layout.byte_size,
            )
    else:
        meta.structs[name] = layout


def _expand_anonymous_member(
    die: Any,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    byte_offset: int,
) -> list[FieldInfo]:
    """Inline the fields of an anonymous struct/union member.

    DWARF uses unnamed DW_TAG_member to embed anonymous aggregates.
    Rather than discarding them, we inline their nested members so that
    layout changes inside anonymous structs/unions are still detected.
    """
    if "DW_AT_type" not in die.attributes:
        return []
    try:
        target = _resolve_ref(die, "DW_AT_type", CU)
    except Exception:  # noqa: BLE001
        return []
    if target.tag not in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        return []

    fields: list[FieldInfo] = []
    for child in target.iter_children():
        if child.tag != "DW_TAG_member":
            continue
        fi = _process_member(child, CU, type_cache)
        if fi is None:
            continue
        # Adjust offset: anonymous member byte_offset + inner field offset
        fields.append(FieldInfo(
            name=fi.name,
            type_name=fi.type_name,
            byte_offset=byte_offset + fi.byte_offset,
            byte_size=fi.byte_size,
            bit_offset=fi.bit_offset,
            bit_size=fi.bit_size,
        ))
    return fields


def _process_member(
    die: Any,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
) -> FieldInfo | None:
    name = _attr_str(die, "DW_AT_name")
    if not name:
        return None  # padding — anonymous aggregates handled by caller

    # Byte offset — DW_AT_data_member_location can be a simple int or a DW_OP block
    byte_offset = 0
    if "DW_AT_data_member_location" in die.attributes:
        byte_offset = _decode_member_location(
            die.attributes["DW_AT_data_member_location"].value
        )

    # Bitfield offsets:
    # DWARF 4+: DW_AT_data_bit_offset = offset from LSB of the container (preferred)
    # DWARF 2/3: DW_AT_bit_offset = offset from MSB of the storage unit
    # DW_AT_data_bit_offset takes priority when present.
    bit_size = _attr_int(die, "DW_AT_bit_size")
    if bit_size:
        if "DW_AT_data_bit_offset" in die.attributes:
            bit_offset = _attr_int(die, "DW_AT_data_bit_offset")  # DWARF 4+
        else:
            bit_offset = _attr_int(die, "DW_AT_bit_offset")       # DWARF 2/3
    else:
        bit_offset = 0

    # Resolve field type
    type_name, field_byte_size = _resolve_type(die, CU, type_cache)

    return FieldInfo(
        name=name,
        type_name=type_name,
        byte_offset=byte_offset,
        byte_size=field_byte_size,
        bit_offset=bit_offset,
        bit_size=bit_size,
    )


# ---------------------------------------------------------------------------
# Enum
# ---------------------------------------------------------------------------

def _process_enum(
    die: Any,
    meta: DwarfMetadata,
    CU: Any,
    scope_prefix: str = "",
) -> None:
    name = _attr_str(die, "DW_AT_name")
    if not name:
        return  # anonymous — handled via typedef in _process_typedef
    qualified = f"{scope_prefix}::{name}" if scope_prefix else name
    _process_enum_named(die, meta, CU, override_name=qualified)


def _process_enum_named(
    die: Any,
    meta: DwarfMetadata,
    CU: Any,
    override_name: str | None,
) -> None:
    name = override_name or _attr_str(die, "DW_AT_name")
    if not name:
        return

    byte_size = _attr_int(die, "DW_AT_byte_size")
    if byte_size == 0:
        return  # declaration-only

    enum = EnumInfo(name=name, underlying_byte_size=byte_size)

    for child in die.iter_children():
        if child.tag == "DW_TAG_enumerator":
            member_name = _attr_str(child, "DW_AT_name")
            # DW_AT_const_value may be signed (DW_FORM_sdata → negative values)
            member_val = _attr_int(child, "DW_AT_const_value")
            if member_name:
                enum.members[member_name] = member_val

    if name not in meta.enums:
        meta.enums[name] = enum


# ---------------------------------------------------------------------------
# DWARF reference resolution
# ---------------------------------------------------------------------------

# _resolve_ref is imported from dwarf_utils at the top of this module.


# ---------------------------------------------------------------------------
# Type resolution helpers (with memoisation)
# ---------------------------------------------------------------------------

def _resolve_type(
    die: Any,
    CU: Any,
    cache: dict[tuple[int, int], tuple[str, int]],
) -> tuple[str, int]:
    """Return (type_name, byte_size) for the type referenced by *die*."""
    if "DW_AT_type" not in die.attributes:
        return ("unknown", 0)
    try:
        type_die = _resolve_ref(die, "DW_AT_type", CU)
        return _die_to_type_info(type_die, CU, depth=0, cache=cache)
    except Exception:  # noqa: BLE001
        return ("unknown", 0)


def _die_to_type_info(  # noqa: PLR0911
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
) -> tuple[str, int]:
    """Recursively resolve a type DIE to (name, byte_size).

    Memoised by (CU.cu_offset, die.offset) so each unique type is resolved
    at most once per parse call, avoiding O(n*m) redundant traversals.
    Depth limit = 8 guards against pathological typedef chains.
    """
    if depth > 8:
        return ("...", 0)

    cache_key = (CU.cu_offset, die.offset)
    if cache_key in cache:
        return cache[cache_key]

    result = _compute_type_info(die, CU, depth, cache)
    cache[cache_key] = result
    return result


def _compute_type_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
) -> tuple[str, int]:
    tag = die.tag

    if tag == "DW_TAG_base_type":
        return (_attr_str(die, "DW_AT_name") or "base", _attr_int(die, "DW_AT_byte_size"))

    if tag in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        return _compute_record_type_info(die, tag)

    if tag == "DW_TAG_enumeration_type":
        name = _attr_str(die, "DW_AT_name") or "<enum>"
        return (f"enum {name}", _attr_int(die, "DW_AT_byte_size"))

    if tag == "DW_TAG_pointer_type":
        return _compute_pointer_like_info(die, CU, depth, cache, suffix=" *", fallback="void *")

    if tag in ("DW_TAG_reference_type", "DW_TAG_rvalue_reference_type"):
        suffix = " &&" if tag == "DW_TAG_rvalue_reference_type" else " &"
        return _compute_pointer_like_info(die, CU, depth, cache, suffix=suffix, fallback=f"?{suffix}")

    if tag in ("DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type"):
        qualifier = tag.split("_")[2].lower()
        return _compute_qualified_type_info(die, CU, depth, cache, qualifier)

    if tag == "DW_TAG_typedef":
        return _compute_typedef_info(die, CU, depth, cache)

    if tag == "DW_TAG_array_type":
        return _compute_array_type_info(die, CU, depth, cache)

    if tag == "DW_TAG_subroutine_type":
        return ("fn(...)", _attr_int(die, "DW_AT_byte_size"))

    return _compute_fallback_type_info(die, tag)


def _compute_record_type_info(die: Any, tag: str) -> tuple[str, int]:
    name = _attr_str(die, "DW_AT_name") or "<anon>"
    # Use bare names for struct/class/union to match castxml naming.
    # Prefixes like "struct Foo" vs "Foo" can cause false type-drift reports
    # when comparing DWARF-derived layouts to castxml/header-derived models.
    return (name, _attr_int(die, "DW_AT_byte_size"))


def _compute_pointer_like_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
    suffix: str,
    fallback: str,
) -> tuple[str, int]:
    pointee = _resolve_inner_type_name(die, CU, depth, cache)
    size = _attr_int(die, "DW_AT_byte_size") or 0
    if pointee is None:
        return (fallback, size)
    return (f"{pointee}{suffix}", size)


def _compute_qualified_type_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
    qualifier: str,
) -> tuple[str, int]:
    inner = _resolve_inner_type_info(die, CU, depth, cache)
    if inner is None:
        return (qualifier, 0)
    inner_name, size = inner
    return (f"{qualifier} {inner_name}", size)


def _compute_typedef_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
) -> tuple[str, int]:
    name = _attr_str(die, "DW_AT_name")
    inner = _resolve_inner_type_info(die, CU, depth, cache)
    if inner is None:
        return (name or "typedef", 0)
    inner_name, size = inner
    return (name or inner_name, size)


def _compute_array_type_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
) -> tuple[str, int]:
    size = _attr_int(die, "DW_AT_byte_size")
    inner_name = _resolve_inner_type_name(die, CU, depth, cache)
    return (f"{inner_name}[]", size) if inner_name is not None else ("array", size)


def _resolve_inner_type_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
) -> tuple[str, int] | None:
    if "DW_AT_type" not in die.attributes:
        return None
    try:
        inner_die = _resolve_ref(die, "DW_AT_type", CU)
        return _die_to_type_info(inner_die, CU, depth + 1, cache)
    except Exception:  # noqa: BLE001
        return None


def _resolve_inner_type_name(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
) -> str | None:
    inner = _resolve_inner_type_info(die, CU, depth, cache)
    return inner[0] if inner is not None else None


def _compute_fallback_type_info(die: Any, tag: str) -> tuple[str, int]:
    name = _attr_str(die, "DW_AT_name")
    size = _attr_int(die, "DW_AT_byte_size")
    # Log unknown DWARF type tags so gaps in type resolution are visible.
    # This helps diagnose missing coverage for new/vendor-specific DWARF extensions.
    # abi-dumper #6: __unknown__ type entries should produce a diagnostic.
    if not name:
        tag_key = tag or "<empty>"
        if tag_key not in _SEEN_UNKNOWN_DWARF_TAGS:
            _SEEN_UNKNOWN_DWARF_TAGS.add(tag_key)
            log.warning(
                "Unknown DWARF type tag: %s at offset %s",
                tag,
                getattr(die, "offset", "?"),
            )
    return (name or tag or "unknown", size)


# ---------------------------------------------------------------------------
# Attribute helpers — delegated to dwarf_utils
# ---------------------------------------------------------------------------
# _attr_str, _attr_int, and _resolve_ref are imported from dwarf_utils
# at the top of this module.

# Public alias for dwarf_unified — keeps the contract visible to mypy.
_process_cu_impl = _process_cu
