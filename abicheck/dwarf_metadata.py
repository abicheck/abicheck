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
from pathlib import Path
from typing import Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .dwarf_utils import (
    BASE_PRUNE_TAGS,
    attr_bool as _attr_bool,  # noqa: F401
    attr_int as _attr_int,
    attr_str as _attr_str,
    decode_member_location as _decode_member_location,
    has_real_dwarf_info,
    is_skeleton_cu as _is_skeleton_cu,
    resolve_die_ref as _resolve_ref,
)

# Fact dataclasses live in the model package (ADR-061 Phase 5): this module
# parses into them and re-exports them so the historical
# ``from abicheck.dwarf_metadata import DwarfMetadata`` spelling keeps resolving.
from .model.dwarf_facts import (
    DwarfMetadata as DwarfMetadata,
    EnumInfo as EnumInfo,
    FieldInfo as FieldInfo,
    StructLayout as StructLayout,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# Tags whose subtrees we never descend into (function-local types, inline
# frames, lexical blocks). Registering function-local structs as ABI
# surfaces would produce noise and false positives.

# Deduplicate unknown DWARF-tag warnings per process to avoid log flooding
_SEEN_UNKNOWN_DWARF_TAGS: set[str] = set()

_SKIP_TAGS: frozenset[str] = BASE_PRUNE_TAGS | frozenset(
    {
        "DW_TAG_subprogram",
    }
)


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

    if not has_real_dwarf_info(elf):
        log.debug("parse_dwarf_metadata: no DWARF info in %s", so_path)
        return meta

    meta.has_dwarf = True
    meta.evidence_state = "parsed"
    dwarf = elf.get_dwarf_info()  # type: ignore[no-untyped-call]

    # Per-parse type-resolution cache: (cu_offset, die_offset) → (name, byte_size)
    type_cache: dict[tuple[int, int], tuple[str, int]] = {}
    # P1 review, fresh evidence: a per-DIE type-resolution failure inside an
    # otherwise-successful CU (a malformed DW_AT_type reference caught by
    # _resolve_type()/_process_typedef()/_expand_anonymous_member()/
    # _resolve_inner_type_info(), each returning a placeholder rather than
    # raising) previously left cu_failed untouched -- the CU-level try/except
    # below only ever sees an exception that escaped every one of those inner
    # catches. Shared across the whole parse (mirrors type_cache's own
    # per-parse scope) and threaded through the full DIE-walk/type-resolution
    # call chain; folded into evidence_state after the CU loop.
    incomplete: list[bool] = []

    # P2 review: this standalone entry point (still public, re-exported by
    # dwarf_unified.py's shim) previously never stamped evidence_state at
    # all, so it silently stayed at the dataclass default ("not_available")
    # even on a fully-successful parse -- indistinguishable from "never
    # tried". Mirror dwarf_unified.parse_dwarf_from_session's accounting
    # (cu_total/cu_failed -> parsed/partial/failed) so every constructor of
    # this metadata type records its actual extraction outcome, not just
    # the unified single-pass one.
    skeleton_cus = 0
    for CU in dwarf.iter_CUs():  # type: ignore[no-untyped-call]
        meta.cu_total += 1
        if _is_skeleton_cu(CU):
            # P2 review, fresh evidence: this standalone entry point (still
            # public) had no split-DWARF (-gsplit-dwarf) detection at all --
            # a skeleton CU "succeeds" at both iter_CUs() and the per-CU walk
            # below while its real layout/CC DIEs live in an unconsumed
            # .dwo/.dwp file, so the channel read back "parsed" with zero
            # real facts extracted. Mirrors dwarf_unified.
            # parse_dwarf_from_session's identical skeleton-CU downgrade.
            skeleton_cus += 1
        try:
            _process_cu(CU, meta, type_cache, incomplete=incomplete)
        except Exception as exc:  # noqa: BLE001
            meta.cu_failed += 1
            log.warning("parse_dwarf_metadata: skipping CU in %s: %s", so_path, exc)

    if meta.cu_total == 0:
        # An empty/truncated .debug_info section iterates to zero CUs
        # without raising -- has_real_dwarf_info() only confirmed the
        # section exists, not that it holds anything. Mirrors
        # dwarf_unified.parse_dwarf_from_session's identical zero-CU check.
        meta.evidence_state = "failed"
    elif meta.cu_failed or skeleton_cus:
        meta.evidence_state = (
            "failed"
            if meta.cu_failed and meta.cu_failed == meta.cu_total
            else "partial"
        )
    elif incomplete:
        # Every CU-level try/except succeeded, but at least one per-DIE
        # type reference inside one of them could not be resolved.
        meta.evidence_state = "partial"

    return meta


def _process_cu(
    CU: Any,
    meta: DwarfMetadata,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
) -> None:
    """Walk all DIEs in one Compilation Unit (iterative, no recursion)."""
    top_die = CU.get_top_DIE()
    _walk_die_iter(top_die, meta, CU, type_cache, incomplete=incomplete)


def _walk_die_iter(
    root_die: Any,
    meta: DwarfMetadata,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
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
            _process_struct(
                die, meta, CU, type_cache, scope_prefix=scope, incomplete=incomplete
            )
            if die_name:
                next_scope = qualified  # nested types use this as their scope
        elif tag == "DW_TAG_enumeration_type":
            _process_enum(die, meta, CU, scope_prefix=scope)
        elif tag == "DW_TAG_typedef":
            _process_typedef(die, meta, CU, type_cache, incomplete=incomplete)
        elif tag == "DW_TAG_base_type" and die_name:
            bsize = _attr_int(die, "DW_AT_byte_size")
            if bsize:
                # Same-name base types must agree on size within a binary; keep
                # the first non-zero size seen (a later 0 is a declaration).
                meta.base_types.setdefault(die_name, bsize)

        # Push children in reverse order so left-to-right DFS order is preserved
        for child in reversed(list(die.iter_children())):
            stack.append((child, next_scope))


def _process_typedef(
    die: Any,
    meta: DwarfMetadata,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
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
        # P1 review: a malformed DW_AT_type reference here was previously
        # invisible to the CU-level failure accounting -- this typedef's
        # anonymous-struct/enum backfill is silently skipped.
        if incomplete is not None:
            incomplete.append(True)
        return

    tag = target.tag
    target_name = _attr_str(target, "DW_AT_name")

    if tag in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        if not target_name and typedef_name not in meta.structs:
            _process_struct_named(
                target,
                meta,
                CU,
                type_cache,
                override_name=typedef_name,
                incomplete=incomplete,
            )
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
    *,
    incomplete: list[bool] | None = None,
) -> None:
    name = _attr_str(die, "DW_AT_name")
    if not name:
        return  # anonymous — handled via typedef in _process_typedef
    qualified = f"{scope_prefix}::{name}" if scope_prefix else name
    _process_struct_named(
        die, meta, CU, type_cache, override_name=qualified, incomplete=incomplete
    )


def _process_struct_named(
    die: Any,
    meta: DwarfMetadata,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    override_name: str | None,
    *,
    incomplete: list[bool] | None = None,
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
                _expand_anonymous_member(
                    child, CU, type_cache, anon_offset, incomplete=incomplete
                )
            )
        else:
            fi = _process_member(child, CU, type_cache, incomplete=incomplete)
            if fi is not None:
                layout.fields.append(fi)

    # ODR: keep the first complete definition.
    if name in meta.structs:
        existing = meta.structs[name]
        if existing.byte_size != layout.byte_size:
            log.debug(
                "ODR size mismatch for %s: %d vs %d bytes (keeping first)",
                name,
                existing.byte_size,
                layout.byte_size,
            )
    else:
        meta.structs[name] = layout


def _expand_anonymous_member(
    die: Any,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    byte_offset: int,
    *,
    incomplete: list[bool] | None = None,
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
        # P1 review: a malformed DW_AT_type reference here was previously
        # invisible to the CU-level failure accounting -- this anonymous
        # member's fields are silently dropped.
        if incomplete is not None:
            incomplete.append(True)
        return []
    if target.tag not in (
        "DW_TAG_structure_type",
        "DW_TAG_class_type",
        "DW_TAG_union_type",
    ):
        return []

    fields: list[FieldInfo] = []
    for child in target.iter_children():
        if child.tag != "DW_TAG_member":
            continue
        fi = _process_member(child, CU, type_cache, incomplete=incomplete)
        if fi is None:
            continue
        # Adjust offset: anonymous member byte_offset + inner field offset
        fields.append(
            FieldInfo(
                name=fi.name,
                type_name=fi.type_name,
                byte_offset=byte_offset + fi.byte_offset,
                byte_size=fi.byte_size,
                bit_offset=fi.bit_offset,
                bit_size=fi.bit_size,
            )
        )
    return fields


def _process_member(
    die: Any,
    CU: Any,
    type_cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
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
            bit_offset = _attr_int(die, "DW_AT_bit_offset")  # DWARF 2/3
    else:
        bit_offset = 0

    # Resolve field type
    type_name, field_byte_size = _resolve_type(
        die, CU, type_cache, incomplete=incomplete
    )

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
    *,
    incomplete: list[bool] | None = None,
) -> tuple[str, int]:
    """Return (type_name, byte_size) for the type referenced by *die*."""
    if "DW_AT_type" not in die.attributes:
        return ("unknown", 0)
    try:
        type_die = _resolve_ref(die, "DW_AT_type", CU)
        return _die_to_type_info(
            type_die, CU, depth=0, cache=cache, incomplete=incomplete
        )
    except Exception:  # noqa: BLE001
        # P1 review, fresh evidence: a malformed DW_AT_type reference here was
        # previously invisible to the CU-level failure accounting -- this
        # field/return/parameter type silently falls back to "unknown".
        if incomplete is not None:
            incomplete.append(True)
        return ("unknown", 0)


def _die_to_type_info(  # noqa: PLR0911
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
) -> tuple[str, int]:
    """Recursively resolve a type DIE to (name, byte_size).

    Memoised by (CU.cu_offset, die.offset) so each unique type is resolved
    at most once per parse call, avoiding O(n*m) redundant traversals.
    Depth limit = 8 guards against pathological typedef chains.
    """
    if depth > 8:
        # P2 review, fresh evidence (Codex): a cyclic or genuinely
        # more-than-nine-level typedef/qualifier chain hits this guard and
        # substitutes a placeholder ("...", 0) the same way an unresolved
        # DW_AT_type reference does -- but previously did so without
        # touching the completeness accumulator, so the basic channel could
        # report "parsed" despite a real field/return/parameter type being
        # silently truncated here.
        if incomplete is not None:
            incomplete.append(True)
        return ("...", 0)

    cache_key = (CU.cu_offset, die.offset)
    if cache_key in cache:
        return cache[cache_key]

    result = _compute_type_info(die, CU, depth, cache, incomplete=incomplete)
    cache[cache_key] = result
    return result


def _compute_type_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
) -> tuple[str, int]:
    tag = die.tag

    if tag == "DW_TAG_base_type":
        return (
            _attr_str(die, "DW_AT_name") or "base",
            _attr_int(die, "DW_AT_byte_size"),
        )

    if tag in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        return _compute_record_type_info(die, tag)

    if tag == "DW_TAG_enumeration_type":
        name = _attr_str(die, "DW_AT_name") or "<enum>"
        return (f"enum {name}", _attr_int(die, "DW_AT_byte_size"))

    if tag == "DW_TAG_pointer_type":
        return _compute_pointer_like_info(
            die, CU, depth, cache, suffix=" *", fallback="void *", incomplete=incomplete
        )

    if tag in ("DW_TAG_reference_type", "DW_TAG_rvalue_reference_type"):
        suffix = " &&" if tag == "DW_TAG_rvalue_reference_type" else " &"
        return _compute_pointer_like_info(
            die,
            CU,
            depth,
            cache,
            suffix=suffix,
            fallback=f"?{suffix}",
            incomplete=incomplete,
        )

    if tag in ("DW_TAG_const_type", "DW_TAG_volatile_type", "DW_TAG_restrict_type"):
        qualifier = tag.split("_")[2].lower()
        return _compute_qualified_type_info(
            die, CU, depth, cache, qualifier, incomplete=incomplete
        )

    if tag == "DW_TAG_atomic_type":
        # Spelled "_Atomic" (not the generic tag.split() lowercase form) so it
        # matches the C11 keyword diff_atomic.py's _has_atomic() looks for.
        return _compute_qualified_type_info(
            die, CU, depth, cache, "_Atomic", incomplete=incomplete
        )

    if tag == "DW_TAG_typedef":
        return _compute_typedef_info(die, CU, depth, cache, incomplete=incomplete)

    if tag == "DW_TAG_array_type":
        return _compute_array_type_info(die, CU, depth, cache, incomplete=incomplete)

    if tag == "DW_TAG_subroutine_type":
        return ("fn(...)", _attr_int(die, "DW_AT_byte_size"))

    return _compute_fallback_type_info(die, tag, incomplete=incomplete)


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
    *,
    incomplete: list[bool] | None = None,
) -> tuple[str, int]:
    pointee = _resolve_inner_type_name(die, CU, depth, cache, incomplete=incomplete)
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
    *,
    incomplete: list[bool] | None = None,
) -> tuple[str, int]:
    inner = _resolve_inner_type_info(die, CU, depth, cache, incomplete=incomplete)
    if inner is None:
        return (qualifier, 0)
    inner_name, size = inner
    return (f"{qualifier} {inner_name}", size)


def _compute_typedef_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
) -> tuple[str, int]:
    name = _attr_str(die, "DW_AT_name")
    inner = _resolve_inner_type_info(die, CU, depth, cache, incomplete=incomplete)
    if inner is None:
        return (name or "typedef", 0)
    inner_name, size = inner
    return (name or inner_name, size)


def _compute_array_type_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
) -> tuple[str, int]:
    size = _attr_int(die, "DW_AT_byte_size")
    inner_name = _resolve_inner_type_name(die, CU, depth, cache, incomplete=incomplete)
    return (f"{inner_name}[]", size) if inner_name is not None else ("array", size)


def _resolve_inner_type_info(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
) -> tuple[str, int] | None:
    if "DW_AT_type" not in die.attributes:
        return None
    try:
        inner_die = _resolve_ref(die, "DW_AT_type", CU)
        return _die_to_type_info(inner_die, CU, depth + 1, cache, incomplete=incomplete)
    except Exception:  # noqa: BLE001
        # P1 review, fresh evidence: a malformed DW_AT_type reference here was
        # previously invisible to the CU-level failure accounting -- the
        # enclosing pointer/qualifier/array/typedef falls back to a
        # placeholder inner type with no completeness signal.
        if incomplete is not None:
            incomplete.append(True)
        return None


def _resolve_inner_type_name(
    die: Any,
    CU: Any,
    depth: int,
    cache: dict[tuple[int, int], tuple[str, int]],
    *,
    incomplete: list[bool] | None = None,
) -> str | None:
    inner = _resolve_inner_type_info(die, CU, depth, cache, incomplete=incomplete)
    return inner[0] if inner is not None else None


def _compute_fallback_type_info(
    die: Any, tag: str, *, incomplete: list[bool] | None = None
) -> tuple[str, int]:
    name = _attr_str(die, "DW_AT_name")
    size = _attr_int(die, "DW_AT_byte_size")
    # Log unknown DWARF type tags so gaps in type resolution are visible.
    # This helps diagnose missing coverage for new/vendor-specific DWARF extensions.
    # abi-dumper #6: __unknown__ type entries should produce a diagnostic.
    if not name:
        # P1 review, fresh evidence (Codex): a standard tag with no
        # dedicated _compute_type_info() branch (e.g.
        # DW_TAG_ptr_to_member_type, which typically carries no
        # DW_AT_name) previously fell through to this same placeholder
        # substitution as an unresolved DW_AT_type reference does, but
        # without touching the completeness accumulator -- so two DIEs
        # sharing this fallback (e.g. `int A::*` vs `long A::*`, both
        # bare DW_TAG_ptr_to_member_type with no name) resolve to the
        # identical "DW_TAG_ptr_to_member_type" placeholder string on
        # both sides, reading as NO_CHANGE while analysis_assurance still
        # reports "parsed" -- silently masking a real field-type change.
        if incomplete is not None:
            incomplete.append(True)
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
