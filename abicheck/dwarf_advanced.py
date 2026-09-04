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

"""Sprint 4: Advanced DWARF analysis.

Detects:
1. Calling convention changes (DW_AT_calling_convention on exported functions)
2. Struct packing drift (__attribute__((packed)) — via DWARF field offsets vs
   natural alignment of the *type* byte size, properly resolved via DW_AT_type)
3. Toolchain flag drift via DW_AT_producer parsing
   (-fshort-enums, -fpack-struct, -fno-common, -m32/-m64, -mabi=*, etc.)

Design notes:
- Single iterative DWARF walk per binary (deque-based, no recursion)
- DW_AT_type is resolved for member size — fixes false-negative in packed detection
- Imports at module level (style consistency with Sprint 3)
- Specific exception handling: ELFError/OSError/ValueError; re-raises others
- "First CU wins" for DW_AT_producer (acceptable: ABI flags uniform across TUs
  in well-formed libraries; divergence is logged at WARNING level)

Coverage note:
  DW_AT_calling_convention is rarely emitted on Linux x86-64 (System V AMD64 ABI
  uses a single implicit calling convention). This detector is most useful for
  Windows (__stdcall/__cdecl mixed libraries) and embedded targets.
  The toolchain flag detector (DW_AT_producer) provides broader coverage for
  ABI-flag drift on Linux.
"""

# pylint: disable=invalid-name  # CU is the standard DWARF term (Compilation Unit)
from __future__ import annotations

import collections
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .dwarf_utils import (
    BASE_PRUNE_TAGS,
    attr_bool as _attr_bool,
    attr_int as _attr_int,
    attr_str as _attr_str,
    decode_member_location as _shared_decode_member_location,
    has_real_dwarf_info,
    is_skeleton_cu as _is_skeleton_cu,
    resolve_die_ref as _resolve_die_ref,
    resolve_type_die as _resolve_type_die,
)

# Fact dataclasses live in the model package (ADR-061 Phase 5): this module
# parses into them and re-exports them so the historical
# ``from abicheck.dwarf_advanced import AdvancedDwarfMetadata`` spelling keeps resolving.
from .model.dwarf_facts import (
    AdvancedDwarfMetadata as AdvancedDwarfMetadata,
    ToolchainInfo as ToolchainInfo,
)

log = logging.getLogger(__name__)

# DW_AT_calling_convention values (DWARF 5 standard + vendor extensions)
_CC_NAMES: dict[int, str] = {
    0x01: "normal",
    0x02: "program",
    0x03: "nocall",
    0x04: "pass_by_reference",  # DWARF 5
    0x05: "pass_by_value",  # DWARF 5
    0x40: "GNU_renesas_sh",
    0x41: "GNU_borland_fastcall_i386",
    0x80: "GNU_push_call_stub",  # GCC internal
    0x81: "GNU_push_arg",  # GCC internal
    0xB0: "BORLAND_safecall",
    0xB1: "BORLAND_stdcall",
    0xB2: "BORLAND_pascal",
    0xB3: "BORLAND_msfastcall",
    0xB4: "BORLAND_msreturn",
    0xB5: "BORLAND_thiscall",
    0xB6: "BORLAND_fastcall",
    0xB9: "LLVM_PreserveMost",
    0xD0: "LLVM_vectorcall",
}

# Flags in DW_AT_producer that affect binary ABI
_ABI_FLAGS_RE = re.compile(
    r"""
    (?P<short_enums>-fshort-enums)
    |(?P<pack_struct>-fpack-struct(?:=\d+)?)
    |(?P<no_common>-fno-common)
    |(?P<common>-fcommon)
    |(?P<m32>-m32)
    |(?P<m64>-m64)
    |(?P<mabi>-mabi=\S+)
    |(?P<fabi>-fabi-version=\d+)
    |(?P<cxx11abi>-D_GLIBCXX_USE_CXX11_ABI=\d)
    """,
    re.VERBOSE,
)

# Vector-function (SIMD clone) ABI flags in DW_AT_producer. These select the
# ABI of vectorized call variants (e.g. `#pragma omp declare simd` clones or
# auto-vectorized math calls). A change here means the same scalar function's
# vector entry points resolve to a different ABI — a binary break for callers
# of those vector variants. Cross-compiler: -mveclibabi= (GCC),
# -fveclib= (clang), -vecabi= (Intel-style icx/icc).
_VECTOR_ABI_FLAGS_RE = re.compile(r"-mveclibabi=\S+|-fveclib=\S+|-vecabi=\S+")

# wchar_t data-model flag in DW_AT_producer. GCC/Clang document that objects
# built with and without -fshort-wchar are not binary compatible: the flag
# switches wchar_t between the platform default (commonly 4-byte signed on
# Linux/macOS) and a 2-byte unsigned type. Kept in its own field (like the
# vector-ABI flags) rather than folded into _ABI_FLAGS_RE's generic
# toolchain_flag_drift bucket, since it gets its own named, higher-signal
# ChangeKind (WCHAR_MODEL_CHANGED).
_WCHAR_ABI_FLAGS_RE = re.compile(r"-fshort-wchar|-fno-short-wchar")

# Natural alignment (bytes) by type size on most LP64 platforms
_NATURAL_ALIGN: dict[int, int] = {1: 1, 2: 2, 4: 4, 8: 8, 16: 16}

# Tags to prune: don't descend into function bodies or inlined frames
_PRUNE_TAGS: frozenset[str] = BASE_PRUNE_TAGS


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_advanced_dwarf(so_path: Path) -> AdvancedDwarfMetadata:
    """Extract Sprint 4 metadata from *so_path*.

    Returns empty AdvancedDwarfMetadata (has_dwarf=False) if binary has no
    debug info or cannot be parsed. Never raises.
    """
    try:
        with open(so_path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            if not has_real_dwarf_info(elf):
                return AdvancedDwarfMetadata()
            meta = AdvancedDwarfMetadata(has_dwarf=True, evidence_state="parsed")
            meta.target_arch = _normalize_arch(elf)
            dwarf = elf.get_dwarf_info()  # type: ignore[no-untyped-call]
            # P2 review: mirror dwarf_unified's cu_total/cu_failed accounting
            # -- this standalone entry point is still public and previously
            # never recorded a skipped CU at all.
            skeleton_cus = 0
            # P1 review, fresh evidence: same gap as the basic channel's own
            # `incomplete` list (dwarf_metadata._parse) -- a malformed
            # DW_AT_type on an exported function's return/parameter type,
            # caught deep inside the value-ABI-trait walk
            # (resolve_type_die/_unwrap_qualifiers/_is_nontrivial_aggregate/
            # _type_unaligned_at, each returning a placeholder rather than
            # raising) previously left cu_failed untouched here, silently
            # omitting that function's value_abi_traits/return_value_sizes/
            # return_memory_classified entries with no completeness signal.
            incomplete: list[bool] = []
            for CU in dwarf.iter_CUs():
                meta.cu_total += 1
                if _is_skeleton_cu(CU):
                    # P2 review, fresh evidence: mirrors dwarf_unified.
                    # parse_dwarf_from_session's identical skeleton-CU
                    # downgrade -- this standalone entry point had no
                    # split-DWARF (-gsplit-dwarf) detection at all, so a
                    # skeleton CU (real DIEs live in an unconsumed .dwo/
                    # .dwp file) "succeeded" here while extracting zero
                    # real calling-convention/value-ABI facts.
                    skeleton_cus += 1
                try:
                    _process_cu(CU, meta, incomplete=incomplete)
                except (ELFError, OSError, ValueError, KeyError) as exc:
                    meta.cu_failed += 1
                    log.warning("parse_advanced_dwarf: skipping CU: %s", exc)
            if meta.cu_total == 0:
                # Mirrors dwarf_unified.parse_dwarf_from_session's/
                # dwarf_metadata._parse's identical zero-CU check: an
                # empty/truncated .debug_info section iterates to zero
                # CUs without raising.
                meta.evidence_state = "failed"
            elif meta.cu_failed or skeleton_cus:
                meta.evidence_state = (
                    "failed"
                    if meta.cu_failed and meta.cu_failed == meta.cu_total
                    else "partial"
                )
            elif incomplete:
                # Every CU-level try/except succeeded, but at least one
                # value-ABI-trait type reference inside one of them could
                # not be resolved.
                meta.evidence_state = "partial"
            # Parse .eh_frame / .debug_frame CFA register convention (#117)
            cfi_complete = _parse_frame_registers(elf, dwarf, meta)
            if not cfi_complete and meta.evidence_state == "parsed":
                # P1 review, fresh evidence: a malformed/unsupported FDE is
                # caught and skipped inside _parse_frame_registers itself,
                # so the pass "succeeds" (never raises) while frame-
                # register/callee-saved-register facts for that FDE were
                # never extracted. Only downgrades a clean "parsed" -- an
                # already partial/failed state from the CU accounting above
                # is not overwritten either direction.
                meta.evidence_state = "partial"
            return meta
    except (ELFError, OSError, ValueError) as exc:
        log.warning("parse_advanced_dwarf: failed %s: %s", so_path, exc)
        return AdvancedDwarfMetadata()


# ---------------------------------------------------------------------------
# Internal: per-CU processing
# ---------------------------------------------------------------------------


def _process_cu(
    CU: Any, meta: AdvancedDwarfMetadata, *, incomplete: list[bool] | None = None
) -> None:
    top = CU.get_top_DIE()

    # Extract toolchain info from DW_AT_producer on the CU top DIE. The first CU
    # sets the compiler/version/producer string; ABI flags are *unioned* across
    # every CU, because a flag like -fshort-enums can be applied to only some
    # translation units and would otherwise be missed if it were absent from the
    # first CU (G23-C).
    producer = _attr_str(top, "DW_AT_producer")
    if producer:
        parsed = _parse_producer(producer)
        if not meta.toolchain.producer_string:
            meta.toolchain = parsed
        else:
            meta.toolchain.abi_flags |= parsed.abi_flags
            meta.toolchain.vector_abi_flags |= parsed.vector_abi_flags
            meta.toolchain.wchar_flags |= parsed.wchar_flags

    _walk_cu(top, meta, CU, incomplete=incomplete)


def _get_type_align(member_die: Any, CU: Any) -> int:
    """Return the natural alignment of a member's type in bytes.

    Strategy (in order):
    1. DW_AT_alignment on the type DIE (DWARF 5 — authoritative)
    2. DW_TAG_base_type / DW_TAG_pointer_type / DW_TAG_reference_type:
       alignment == byte_size (primitive / pointer).
    3. Everything else (struct, array, typedef chain, etc.): return 0 to skip.
       We must not use byte_size as a proxy for alignment of composite types —
       a struct { int a; char b; } is size=8 but alignment=4.

    Returns 0 when alignment cannot be determined reliably (caller should skip).
    """
    if "DW_AT_type" not in member_die.attributes:
        return 0
    try:
        type_die = _resolve_die_ref(member_die, "DW_AT_type", CU)

        # Follow transparent wrapper tags via _unwrap_qualifiers
        type_die = _unwrap_qualifiers(type_die, CU)

        # 1. DW_AT_alignment present on the resolved type (DWARF 5)
        if "DW_AT_alignment" in type_die.attributes:
            return int(type_die.attributes["DW_AT_alignment"].value)

        # 2. Primitive types: alignment == byte_size
        prim_tags = (
            "DW_TAG_base_type",
            "DW_TAG_pointer_type",
            "DW_TAG_reference_type",
            "DW_TAG_rvalue_reference_type",
        )
        if type_die.tag in prim_tags:
            sz_attr = type_die.attributes.get("DW_AT_byte_size")
            if sz_attr:
                sz = int(sz_attr.value)
                return _NATURAL_ALIGN.get(min(sz, 16), 1)

        # 3. Composite / array / enum etc.: cannot infer alignment from size
        return 0
    except Exception:  # noqa: BLE001
        return 0


def _walk_cu(
    root: Any,
    meta: AdvancedDwarfMetadata,
    CU: Any,
    *,
    incomplete: list[bool] | None = None,
) -> None:
    """Iterative depth-first DIE walk.

    Does NOT descend into DW_TAG_subprogram children — we only need the
    subprogram DIE itself for calling convention. This halves traversal time
    in function-heavy TUs. Packed struct check still needs struct member
    children (handled directly in _check_packed).
    """
    stack: collections.deque[Any] = collections.deque([root])
    cache = _DwarfTypeCache()  # per-CU cache to avoid redundant traversals

    while stack:
        die = stack.pop()
        tag = die.tag

        if tag in _PRUNE_TAGS:
            continue

        if tag in ("DW_TAG_subprogram", "DW_TAG_subroutine_type"):
            _extract_calling_convention(
                die, meta, CU, cache=cache, incomplete=incomplete
            )
            # Don't descend into subprogram children — not needed for CC extraction
            # and avoids traversing all local variables, params, inlined calls
            continue

        if tag in ("DW_TAG_structure_type", "DW_TAG_class_type"):
            # Register name in all_struct_names only for complete types (byte_size > 0).
            # Forward declarations (byte_size == 0) must NOT be registered: a forward
            # decl of a deleted struct in the new binary would cause a false
            # "packing removed" report via the both_struct_names guard.
            sname = _attr_str(die, "DW_AT_name")
            if sname and _attr_int(die, "DW_AT_byte_size") > 0:
                meta.all_struct_names.add(sname)
            _check_packed(die, meta, CU, override_name=None)

        elif tag == "DW_TAG_typedef":
            # Anonymous struct typedef: `typedef struct {...} Name` — struct has no
            # DW_AT_name; resolve the typedef target and check if it's a packed struct.
            _check_packed_typedef(die, meta, CU, incomplete=incomplete)

        # Push children in reverse order (DFS left-to-right)
        stack.extend(reversed(list(die.iter_children())))


# ---------------------------------------------------------------------------
# Calling convention extraction
# ---------------------------------------------------------------------------

# _resolve_type_die is imported from dwarf_utils at the top of this module.


@dataclass
class _DwarfTypeCache:
    """Per-parse caches to avoid redundant DWARF traversals."""

    unwrap: dict[int, Any] = field(default_factory=dict)  # die.offset → unwrapped DIE
    nontrivial: dict[int, bool] = field(default_factory=dict)  # die.offset → bool


def _is_nontrivial_aggregate(
    type_die: Any,
    cache: dict[int, bool] | None = None,
    CU: Any = None,
    *,
    incomplete: list[bool] | None = None,
) -> bool:
    """Detect non-trivial-for-calls aggregate per Itanium C++ ABI §3.1.2.

    Non-trivial if ANY of:
    1. User-defined (non-defaulted, non-artificial) destructor present.
    2. User-declared copy or move constructor (C1E/C2E in linkage name).
    3. Any DW_TAG_inheritance child (base class) — conservative: base
       triviality is not recursively resolved.
    4. Any DW_TAG_member whose resolved type is itself non-trivial (e.g.
       ``struct Outer { std::string s; }`` — no explicit dtor, but std::string
       has one, making Outer non-trivial for calls too).
       Member type resolution requires a CU reference; if CU is None, member
       types are not checked (safe degradation — no false positives).
    """
    key = getattr(type_die, "offset", None)
    if cache is not None and key is not None and key in cache:
        return cache[key]

    tag = getattr(type_die, "tag", "")
    if tag not in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        result = False
        if cache is not None and key is not None:
            cache[key] = result
        return result

    # Sentinel: mark in-progress to break potential cycles (recursive member types).
    if cache is not None and key is not None:
        cache[key] = False  # assume trivial; overwrite below if non-trivial found

    class_name = _attr_str(type_die, "DW_AT_name") or ""
    result = _check_children_nontrivial(
        type_die, class_name, cache, CU, incomplete=incomplete
    )

    if cache is not None and key is not None:
        cache[key] = result
    return result


def _check_children_nontrivial(
    type_die: Any,
    class_name: str,
    cache: dict[int, bool] | None,
    CU: Any,
    *,
    incomplete: list[bool] | None = None,
) -> bool:
    """Iterate children of a struct/class DIE to detect non-trivial properties."""

    def _member_type_is_nontrivial(ch: Any) -> bool:
        if CU is None:
            return False
        member_type_die = _resolve_type_die(ch, CU, incomplete=incomplete)
        if member_type_die is None:
            return False
        member_tag = getattr(member_type_die, "tag", "")
        if member_tag not in (
            "DW_TAG_structure_type",
            "DW_TAG_class_type",
            "DW_TAG_union_type",
        ):
            return False
        return _is_nontrivial_aggregate(
            member_type_die, cache=cache, CU=CU, incomplete=incomplete
        )

    def _is_user_defined_special_member(ch: Any) -> bool:
        name = _attr_str(ch, "DW_AT_name") or ""
        linkage = _attr_str(ch, "DW_AT_linkage_name") or ""
        defaulted = ch.attributes.get("DW_AT_defaulted")
        artificial = ch.attributes.get("DW_AT_artificial")
        if (defaulted is not None and int(defaulted.value) != 0) or (
            artificial is not None and int(artificial.value) != 0
        ):
            return False
        if name.startswith("~") or any(p in linkage for p in ("D0Ev", "D1Ev", "D2Ev")):
            return True
        return bool(
            class_name
            and linkage
            and any(p in linkage for p in (f"{class_name}C1E", f"{class_name}C2E"))
        )

    for ch in type_die.iter_children():
        if ch.tag == "DW_TAG_inheritance":
            # Any base class -> conservatively non-trivial
            return True

        if ch.tag == "DW_TAG_member":
            if _member_type_is_nontrivial(ch):
                return True
            continue

        if ch.tag != "DW_TAG_subprogram":
            continue

        if _is_user_defined_special_member(ch):
            return True

    return False


def _unwrap_qualifiers(
    type_die: Any,
    CU: Any,
    cache: _DwarfTypeCache | None = None,
    *,
    incomplete: list[bool] | None = None,
) -> Any:
    """Unwrap transparent qualifier/typedef layers."""
    key = getattr(type_die, "offset", None)
    if cache is not None and key is not None and key in cache.unwrap:
        return cache.unwrap[key]

    cur = type_die
    for _ in range(12):
        tag = getattr(cur, "tag", "")
        if tag in (
            "DW_TAG_typedef",
            "DW_TAG_const_type",
            "DW_TAG_volatile_type",
            "DW_TAG_restrict_type",
        ):
            nxt = _resolve_type_die(cur, CU, incomplete=incomplete)
            if nxt is None:
                break
            cur = nxt
        else:
            break
    else:
        # for-else: exhausted depth without finding a non-qualifier tag
        log.debug(
            "_unwrap_qualifiers: depth limit reached at tag=%s",
            getattr(cur, "tag", "?"),
        )

    if cache is not None and key is not None:
        cache.unwrap[key] = cur
    return cur


def _value_abi_trait_for_typed_die(
    die: Any,
    CU: Any,
    cache: _DwarfTypeCache | None = None,
    *,
    incomplete: list[bool] | None = None,
) -> str | None:
    """Return ABI trait for by-value aggregate type (or None if irrelevant).

    Fingerprint contains only ABI-relevant triviality, not type name.
    Type renames don't affect calling convention — including tname causes false positives.
    """
    t0 = _resolve_type_die(die, CU, incomplete=incomplete)
    if t0 is None:
        return None

    # Reference/pointer params are not passed by value and do not trigger SysV
    # aggregate return/arg convention drift from triviality changes.
    if t0.tag in (
        "DW_TAG_pointer_type",
        "DW_TAG_reference_type",
        "DW_TAG_rvalue_reference_type",
    ):
        return None

    t = _unwrap_qualifiers(t0, CU, cache=cache, incomplete=incomplete)
    if t.tag not in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        return None

    nontrivial_cache = cache.nontrivial if cache is not None else None
    # Pass CU so member-type non-triviality (e.g. struct Outer { std::string s; }) is detected
    triviality = (
        "nontrivial"
        if _is_nontrivial_aggregate(
            t, cache=nontrivial_cache, CU=CU, incomplete=incomplete
        )
        else "trivial"
    )
    return triviality  # "trivial" or "nontrivial"


def _aggregate_byte_size_for_typed_die(
    die: Any,
    CU: Any,
    cache: _DwarfTypeCache | None = None,
    *,
    incomplete: list[bool] | None = None,
) -> int | None:
    """Return the byte size of a by-value aggregate type (or None if irrelevant).

    Mirrors :func:`_value_abi_trait_for_typed_die`'s type resolution: only
    struct/class/union types passed/returned *by value* qualify. Used to gate
    the return-convention classification on the SysV register-return threshold.
    """
    t0 = _resolve_type_die(die, CU, incomplete=incomplete)
    if t0 is None:
        return None
    if t0.tag in (
        "DW_TAG_pointer_type",
        "DW_TAG_reference_type",
        "DW_TAG_rvalue_reference_type",
    ):
        return None
    t = _unwrap_qualifiers(t0, CU, cache=cache, incomplete=incomplete)
    if t.tag not in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        return None
    size = _attr_int(t, "DW_AT_byte_size")
    return size if size > 0 else None


#: Scalar (leaf) type tags whose alignment is byte-size-derived (or DW_AT_alignment).
_SCALAR_LEAF_TAGS: tuple[str, ...] = (
    "DW_TAG_base_type",
    "DW_TAG_pointer_type",
    "DW_TAG_reference_type",
    "DW_TAG_rvalue_reference_type",
    "DW_TAG_enumeration_type",
    "DW_TAG_ptr_to_member_type",
)
_AGGREGATE_TAGS: tuple[str, ...] = (
    "DW_TAG_structure_type",
    "DW_TAG_class_type",
    "DW_TAG_union_type",
)


def _scalar_leaf_align(t: Any) -> int:
    """Natural alignment of an already-unwrapped scalar/enum/pointer type DIE."""
    if "DW_AT_alignment" in t.attributes:
        try:
            return int(t.attributes["DW_AT_alignment"].value)
        except (TypeError, ValueError):
            pass
    sz = _attr_int(t, "DW_AT_byte_size")
    return _NATURAL_ALIGN.get(min(sz, 16), 1) if sz > 0 else 1


def _type_unaligned_at(
    type_die: Any,
    CU: Any,
    base_offset: int,
    cache: _DwarfTypeCache | None,
    *,
    incomplete: list[bool] | None = None,
) -> bool:
    """Whether any scalar leaf of *type_die* lands at a misaligned absolute offset.

    *base_offset* is the absolute offset at which this type starts within the
    outermost aggregate. Recurses through nested aggregates (carrying member
    offsets) and array members (an array shares its element's alignment, so the
    array's own offset determines element alignment). A scalar/enum/pointer leaf
    is misaligned when ``base_offset`` is not a multiple of its natural alignment.
    By-value nesting is a DAG, so this terminates.
    """
    t = _unwrap_qualifiers(type_die, CU, cache=cache, incomplete=incomplete)
    if t.tag in _SCALAR_LEAF_TAGS:
        return base_offset % _scalar_leaf_align(t) != 0
    if t.tag == "DW_TAG_array_type":
        elem = _resolve_type_die(t, CU, incomplete=incomplete)
        return elem is not None and _type_unaligned_at(
            elem, CU, base_offset, cache, incomplete=incomplete
        )
    if t.tag in _AGGREGATE_TAGS:
        for child in t.iter_children():
            if child.tag != "DW_TAG_member" or _attr_int(child, "DW_AT_bit_size"):
                continue
            mt = _resolve_type_die(child, CU, incomplete=incomplete)
            if mt is None:
                continue
            abs_offset = base_offset + _decode_member_location(child)
            if _type_unaligned_at(mt, CU, abs_offset, cache, incomplete=incomplete):
                return True
    return False


def _aggregate_has_unaligned_member(
    die: Any,
    CU: Any,
    cache: _DwarfTypeCache | None = None,
    *,
    incomplete: list[bool] | None = None,
) -> bool:
    """Whether a by-value aggregate return type has an unaligned member (recursively).

    A struct/class/union with a leaf at a misaligned offset (e.g. a packed
    aggregate) is MEMORY-classified by the SysV AMD64 ABI regardless of size, so
    it is returned via a hidden sret pointer either way. Walks the full type tree
    — nested aggregates and array members included — accumulating absolute
    offsets, so e.g. ``packed R{char c; int a[1];}`` (``a[0]`` at offset 1) and
    ``packed Outer{char c; Inner{double d};}`` (``i.d`` at offset 1) are caught.
    """
    t0 = _resolve_type_die(die, CU, incomplete=incomplete)
    if t0 is None or t0.tag in (
        "DW_TAG_pointer_type",
        "DW_TAG_reference_type",
        "DW_TAG_rvalue_reference_type",
    ):
        return False
    t = _unwrap_qualifiers(t0, CU, cache=cache, incomplete=incomplete)
    if t.tag not in _AGGREGATE_TAGS:
        return False
    return _type_unaligned_at(t, CU, 0, cache, incomplete=incomplete)


def _extract_calling_convention(
    die: Any,
    meta: AdvancedDwarfMetadata,
    CU: Any,
    cache: _DwarfTypeCache | None = None,
    *,
    incomplete: list[bool] | None = None,
) -> None:
    """Record calling conventions + DWARF value-ABI traits for ABI-exported functions.

    Key: DW_AT_linkage_name (mangled), falling back to DW_AT_MIPS_linkage_name,
    then DW_AT_name. Using the mangled name avoids collisions on overloaded C++
    functions that share a DW_AT_name but differ in signature.

    ALL externally-visible functions are recorded (including those with "normal"
    calling convention). This lets diff_advanced_dwarf distinguish between
    "CC became normal" and "function was added/removed" without a secondary
    ELF symbol lookup.

    On Linux x86-64 (System V AMD64), GCC/Clang rarely emit DW_AT_calling_convention
    (it defaults to DW_CC_normal which is omitted). As a fallback, we also record
    value-ABI traits derived from DWARF types (e.g., trivial→nontrivial aggregate
    return/arg changes), which can imply calling convention drift.
    """
    # Only externally-visible functions matter for ABI surface
    if not _attr_bool(die, "DW_AT_external"):
        return
    # Prefer mangled linkage name for C++ overload uniqueness
    key = (
        _attr_str(die, "DW_AT_linkage_name")
        or _attr_str(die, "DW_AT_MIPS_linkage_name")
        or _attr_str(die, "DW_AT_name")
    )
    if not key:
        return
    if "DW_AT_calling_convention" in die.attributes:
        raw = die.attributes["DW_AT_calling_convention"].value
        cc_name = _CC_NAMES.get(int(raw), f"unknown(0x{int(raw):02x})")
    else:
        cc_name = "normal"
    meta.calling_conventions[key] = cc_name

    # Fallback value-ABI trait (for platforms where DW_AT_calling_convention is omitted)
    parts: list[str] = []
    ret_trait = _value_abi_trait_for_typed_die(
        die, CU, cache=cache, incomplete=incomplete
    )
    if ret_trait is not None:
        parts.append(f"ret:{ret_trait}")
        ret_size = _aggregate_byte_size_for_typed_die(
            die, CU, cache=cache, incomplete=incomplete
        )
        if ret_size is not None:
            meta.return_value_sizes[key] = ret_size
        if _aggregate_has_unaligned_member(die, CU, cache=cache, incomplete=incomplete):
            meta.return_memory_classified.add(key)
    pidx = 0
    for ch in die.iter_children():
        if ch.tag != "DW_TAG_formal_parameter":
            continue
        ptrait = _value_abi_trait_for_typed_die(
            ch, CU, cache=cache, incomplete=incomplete
        )
        if ptrait is not None:
            parts.append(f"p{pidx}:{ptrait}")
        pidx += 1
    if parts:
        meta.value_abi_traits[key] = "|".join(parts)


# ---------------------------------------------------------------------------
# Packed struct detection
# ---------------------------------------------------------------------------


def _check_packed_typedef(
    die: Any,
    meta: AdvancedDwarfMetadata,
    CU: Any,
    *,
    incomplete: list[bool] | None = None,
) -> None:
    """Handle `typedef struct __attribute__((packed)) {...} Name`.

    In this pattern the struct itself is anonymous (no DW_AT_name); the typedef
    provides the visible name. We resolve the target DIE and check packing
    using the typedef name as the identifier.
    """
    typedef_name = _attr_str(die, "DW_AT_name")
    if not typedef_name or "DW_AT_type" not in die.attributes:
        return
    try:
        target = _resolve_die_ref(die, "DW_AT_type", CU)
    except Exception:  # noqa: BLE001
        # P1 review, fresh evidence (Codex): a malformed DW_AT_type on an
        # anonymous struct typedef previously left this packed-typedef
        # walk's own failure invisible to the caller -- _walk_cu threaded
        # `incomplete` into the calling-convention path only, not here, so
        # both the unified and standalone parsers could report advanced
        # evidence "parsed" while silently omitting this typedef's packing
        # facts.
        if incomplete is not None:
            incomplete.append(True)
        return

    tag = target.tag
    if tag not in ("DW_TAG_structure_type", "DW_TAG_class_type"):
        return
    target_name = _attr_str(target, "DW_AT_name")
    if target_name:
        return  # named struct — will be registered under its own name

    _check_packed(target, meta, CU, override_name=typedef_name)


def _check_packed(
    die: Any,
    meta: AdvancedDwarfMetadata,
    CU: Any,
    override_name: str | None = None,
) -> None:
    """Detect if struct has misaligned fields → __attribute__((packed)).

    Uses _get_type_align() to resolve the natural alignment of each member's type.
    This correctly handles primitive types (alignment == size) while skipping
    composite types where size != alignment (e.g. struct{int,char} is size=8, align=4).
    A single misaligned primitive field is sufficient to classify the struct as packed.
    """
    name = override_name or _attr_str(die, "DW_AT_name")
    if not name:
        return
    byte_size = _attr_int(die, "DW_AT_byte_size")
    if byte_size == 0:
        return  # forward declaration only

    meta.all_struct_names.add(name)

    for child in die.iter_children():
        if child.tag != "DW_TAG_member":
            continue
        if _attr_int(child, "DW_AT_bit_size"):
            continue  # bitfields: skip (always "misaligned" by nature)

        # Get byte offset of this field.
        # DW_AT_data_member_location can be:
        #   - int  (DWARF 3+ constant form — most common case)
        #   - list of DWARFExprOp (DWARF 2/3 location expression)
        #     The typical expression is [DW_OP_plus_uconst N] where N is the offset.
        offset = _decode_member_location(child)

        # Get natural alignment via type tag (NOT byte_size of composite types)
        natural = _get_type_align(child, CU)
        if natural <= 1:
            continue  # char/bool/unknown composite: cannot determine — skip

        if offset % natural != 0:
            log.debug(
                "packed struct detected: %s field at offset %d (natural align %d)",
                name,
                offset,
                natural,
            )
            meta.packed_structs.add(name)
            return  # one misaligned field is sufficient


def _decode_member_location(member_die: Any) -> int:
    """Decode DW_AT_data_member_location to a byte offset.

    Delegates to the shared implementation in dwarf_utils.
    """
    if "DW_AT_data_member_location" not in member_die.attributes:
        return 0
    return _shared_decode_member_location(
        member_die.attributes["DW_AT_data_member_location"].value
    )


# ---------------------------------------------------------------------------
# DW_AT_producer parsing
# ---------------------------------------------------------------------------

# Register name tables for common architectures (pyelftools register numbers)
_REG_NAMES_X86_64: dict[int, str] = {
    0: "rax",
    1: "rdx",
    2: "rcx",
    3: "rbx",
    4: "rsi",
    5: "rdi",
    6: "rbp",
    7: "rsp",
    8: "r8",
    9: "r9",
    10: "r10",
    11: "r11",
    12: "r12",
    13: "r13",
    14: "r14",
    15: "r15",
    16: "rip",
}
_REG_NAMES_X86: dict[int, str] = {
    0: "eax",
    1: "ecx",
    2: "edx",
    3: "ebx",
    4: "esp",
    5: "ebp",
    6: "esi",
    7: "edi",
    8: "eip",
}
_REG_NAMES_AARCH64: dict[int, str] = {
    **{i: f"x{i}" for i in range(31)},
    31: "sp",
    32: "pc",
}


def _reg_name(reg_num: int, arch: str) -> str:
    """Convert a register number to a human-readable name for the given arch."""
    if arch in ("x64", "x86_64"):
        return _REG_NAMES_X86_64.get(reg_num, f"reg{reg_num}")
    if arch in ("x86", "i386"):
        return _REG_NAMES_X86.get(reg_num, f"reg{reg_num}")
    if arch in ("aarch64", "arm64"):
        return _REG_NAMES_AARCH64.get(reg_num, f"reg{reg_num}")
    return f"reg{reg_num}"


def _normalize_arch(elf: Any) -> str:
    """Normalize ELF machine arch string to internal arch_key for register lookup."""
    arch = str(elf.get_machine_arch())
    return {
        "x64": "x64",
        "x86_64": "x64",
        "x86": "x86",
        "i386": "x86",
        "AArch64": "aarch64",
        "aarch64": "aarch64",
    }.get(arch, arch)


def _build_addr_to_sym(elf: Any) -> dict[int, str]:
    """Build address → symbol name map from .dynsym (preferred) and .symtab.

    .dynsym is iterated first to populate exported symbol names.
    .symtab is iterated second but does NOT overwrite existing .dynsym entries:
    .dynsym contains only exported ABI symbols; .symtab additionally contains
    local/static symbols that could shadow exported names at the same address.

    Only STB_GLOBAL and STB_WEAK symbols at non-zero addresses are included.
    """
    addr_to_sym: dict[int, str] = {}
    for section_name in (".dynsym", ".symtab"):
        sect = elf.get_section_by_name(section_name)
        if sect is None:
            continue
        for sym in sect.iter_symbols():
            st_value = sym.entry.st_value
            bind = sym.entry.st_info.bind
            if bind in ("STB_GLOBAL", "STB_WEAK") and st_value > 0:
                # .dynsym entries take priority — do not overwrite with .symtab
                if st_value not in addr_to_sym:
                    addr_to_sym[st_value] = sym.name
    return addr_to_sym


def _has_fde(entries: Any) -> bool:
    """Whether a CFI entry list contains at least one real FDE (as opposed
    to only CIE/ZERO terminator entries -- what an ``.eh_frame`` section
    with no actual frame data still yields)."""
    if not entries:
        return False
    return any(e.__class__.__name__ == "FDE" for e in entries)


def _get_cfi_source(dwarf: Any, *, source_failed: list[bool] | None = None) -> Any:
    """Return CFI entry iterator, preferring .eh_frame over .debug_frame.

    P1 review, four rounds of fresh evidence against this same function:

    1. pyelftools' real ``DWARFInfo`` API is
       ``EH_CFI_entries()``/``CFI_entries()`` -- there is no
       ``get_``-prefixed spelling. The previous ``get_EH_CFI_entries()``/
       ``get_CFI_entries()`` calls always raised ``AttributeError``,
       silently caught below, so this function unconditionally returned
       ``None`` and every FDE-backed detector family (frame-register
       convention, callee-saved fingerprint) was never evaluated against
       any real binary despite the advanced channel reporting ``parsed``.
    2. Calling the *real* method names exposed two more real
       absent/empty-section semantics an unconditional
       ``if src is not None: return src`` could not handle: pyelftools
       raises ``AssertionError`` when the underlying section is entirely
       absent, and an *empty*-of-real-data ``.eh_frame`` section (e.g.
       ``-fno-asynchronous-unwind-tables``) returned a non-``None``,
       no-real-FDE list that the old code accepted immediately, never
       falling back to ``.debug_frame``. Fixed by checking section
       presence via ``has_EH_CFI()``/``has_CFI()`` first, and only
       accepting the ``.eh_frame`` result when a real FDE is present.
    3. A section that genuinely *is* present but whose entries raise on
       decode (a malformed/truncated ``.eh_frame``, ``ELFParseError``)
       was caught by the same broad ``except`` as the legitimate
       "section absent" case, so this function returned ``None`` either
       way -- indistinguishable to the caller, which treats ``None`` as
       "no CFI section at all, nothing to be incomplete about" and
       reports ``complete=True``. ``source_failed``, when given, has
       ``True`` appended whenever entries actually failed to decode
       (never for a section that was legitimately never present), so
       ``_parse_frame_registers`` can downgrade completeness for this
       shape too.
    4. The ``.debug_frame`` fallback still accepted whatever
       ``CFI_entries()`` returned unconditionally, unlike the ``.eh_frame``
       branch's own ``_has_fde()`` gate -- a malformed ``.eh_frame``
       (recorded via ``source_failed``) falling back to a present but
       real-FDE-empty ``.debug_frame`` (CIE-only, or genuinely no frame
       data) returned that empty list as a non-``None`` source anyway,
       which made ``_parse_frame_registers``'s own ``cfi_src is None``
       failure check unreachable and erased the recorded EH-frame decode
       failure. Now symmetric with the ``.eh_frame`` branch: only a
       ``.debug_frame`` result with a real FDE is returned as a usable
       source.
    """
    try:
        has_eh = dwarf.has_EH_CFI()
    except (AttributeError, ELFError):
        has_eh = False
    if has_eh:
        try:
            entries = dwarf.EH_CFI_entries()
            if _has_fde(entries):
                return entries
        except (AttributeError, AssertionError, ELFError):
            if source_failed is not None:
                source_failed.append(True)
    try:
        has_dbg = dwarf.has_CFI()
    except (AttributeError, ELFError):
        has_dbg = False
    if has_dbg:
        try:
            entries = dwarf.CFI_entries()
            if _has_fde(entries):
                return entries
        except (AttributeError, AssertionError, ELFError):
            if source_failed is not None:
                source_failed.append(True)
    return None


def _extract_cfa_reg_from_fde(
    entry: Any, arch_key: str, *, decode_failed: list[bool] | None = None
) -> str | None:
    """Extract the dominant CFA register name from an FDE.

    Returns the register name string (e.g. 'rsp', 'rbp') or None if not found.

    Heuristic:
    - Build a sequence of (pc, reg_num) rows where CFA is available.
    - Select the modal CFA register across decoded rows (most frequent), which
      captures the settled function-body convention and avoids epilogue bias.
    - Break ties by selecting the register from the highest-PC row among tied
      candidates (preserves post-prologue behavior for 2-row entry/body tables).

    ``decode_failed``, when given, has ``True`` appended whenever this FDE's
    decode genuinely failed (as opposed to decoding cleanly into a table with
    no CFA data, which is a legitimate ``None`` and never appended) -- so a
    caller accumulating completeness across many FDEs (``_parse_frame_
    registers``) can tell "no CFA row" apart from "decode raised," which this
    helper's own ``except`` would otherwise silently collapse together (P1
    review, fresh evidence: this helper previously reported both shapes as an
    indistinguishable ``None``).
    """
    try:
        decoded = entry.get_decoded()
        if not decoded.table:
            return None

        regs_by_pc: list[tuple[int, int]] = []
        for row in decoded.table:
            cfa = row.get("cfa")
            if cfa is None:
                continue
            cfa_reg = getattr(cfa, "reg", None)
            if cfa_reg is None:
                continue
            regs_by_pc.append((int(row.get("pc", 0)), int(cfa_reg)))

        if not regs_by_pc:
            return None

        counts = collections.Counter(reg for _, reg in regs_by_pc)
        max_count = max(counts.values())
        tied_regs = {reg for reg, cnt in counts.items() if cnt == max_count}
        dominant_reg = max((pc, reg) for pc, reg in regs_by_pc if reg in tied_regs)[1]

        return _reg_name(dominant_reg, arch_key)
    except (ELFError, OSError, ValueError, KeyError, IndexError):
        if decode_failed is not None:
            decode_failed.append(True)
        return None


def _parse_frame_registers(elf: Any, dwarf: Any, meta: AdvancedDwarfMetadata) -> bool:
    """Extract CFA register convention + callee-saved regs for exported functions.

    For each FDE in .eh_frame / .debug_frame:
    - Records the dominant CFA register (frame_registers): rbp/rsp drift.
    - Records callee-saved register fingerprint (callee_saved_regs): the set of
      registers spilled in the prologue via DW_CFA_offset/DW_CFA_rel_offset.

    Callee-saved fingerprint heuristic for calling-convention detection:
      x86-64 SysV ABI  callee-saved: rbx, rbp, r12–r15
      x86-64 ms_abi    callee-saved: rbx, rbp, rdi, rsi, r12–r15, xmm6–xmm15
    Presence of rdi or rsi in the saved-register set is a reliable ELF-level
    signal that the function uses ms_abi even when DW_AT_calling_convention is
    absent (GCC does not emit this attribute for __attribute__((ms_abi))).

    Graceful: any parsing error is logged/skipped. Never raises.

    P1 review, fresh evidence: this pass runs (see the two DWARF entry
    points below) but previously exposed no completion signal at all, so a
    malformed/unsupported FDE that this function itself catches and skips
    left the advanced channel's ``evidence_state`` at whatever the CU
    accounting already decided -- "parsed" on an otherwise-clean binary,
    even though frame-register/callee-saved-register facts for the skipped
    FDE(s) were never actually extracted. Returns ``True`` when CFI
    extraction completed without skipping anything, ``False`` whenever a
    per-FDE error was caught and skipped, the whole pass failed outright,
    or -- P2 review, fresh evidence, round two -- neither ``.eh_frame`` nor
    ``.debug_frame`` is present at all (both call sites only invoke this
    function when real DWARF DIEs exist, so a total absence of unwind
    sections means they were stripped independently of debug info, not
    that there is nothing to extract). Callers downgrade ``evidence_state``
    to ``"partial"`` on a ``False`` return, mirroring the cu_failed/
    skeleton-CU downgrades they already apply.
    """
    try:
        arch_key = _normalize_arch(elf)
        addr_to_sym = _build_addr_to_sym(elf)
        source_failed: list[bool] = []
        cfi_src = _get_cfi_source(dwarf, source_failed=source_failed)
        if cfi_src is None:
            if source_failed:
                # A present CFI section whose entries failed to decode --
                # genuinely incomplete evidence.
                return False
            # P2 review, fresh evidence (Codex): neither .eh_frame nor
            # .debug_frame is present at all. Both call sites gate this
            # function on cu_total > 0 (real DWARF DIEs exist), so this
            # shape is a binary whose unwind sections were stripped
            # independently of its debug info -- frame_registers/
            # callee_saved_regs then stay empty for every function, which
            # previously read as a clean "complete" pass (a self-comparison
            # of such a binary reported analysis_assurance.status="complete"
            # and exited 0 under --require-complete-analysis). Fail closed:
            # a total absence of unwind data is incomplete evidence for the
            # calling-convention-drift analysis this pass exists to run,
            # not "nothing to be incomplete about".
            return False

        complete = True
        for entry in cfi_src:
            try:
                if entry.__class__.__name__ != "FDE":
                    continue
                pc_begin: int = entry["initial_location"]
                sym_name = addr_to_sym.get(pc_begin, "")
                if not sym_name:
                    continue
                decode_failed: list[bool] = []
                reg = _extract_cfa_reg_from_fde(
                    entry, arch_key, decode_failed=decode_failed
                )
                if reg is not None:
                    meta.frame_registers[sym_name] = reg
                # Extract callee-saved register fingerprint from prologue
                saved = _extract_callee_saved_regs(
                    entry, arch_key, decode_failed=decode_failed
                )
                if saved is not None:
                    meta.callee_saved_regs[sym_name] = saved
                if decode_failed:
                    # P1 review, fresh evidence: both helpers above catch and
                    # swallow their own decode errors (so callers of them
                    # standalone keep getting a plain None), which otherwise
                    # left this loop's own except below unreachable for that
                    # failure shape -- this FDE's facts were genuinely
                    # skipped, so the pass is not complete.
                    complete = False
            except (ELFError, OSError, ValueError, KeyError, IndexError) as exc:
                log.debug("_parse_frame_registers: skipping FDE: %s", exc)
                complete = False

        return complete

    except (ELFError, OSError, ValueError) as exc:
        log.warning("_parse_frame_registers: failed: %s", exc)
        return False


def _extract_callee_saved_regs(
    entry: Any, arch_key: str, *, decode_failed: list[bool] | None = None
) -> frozenset[str] | None:
    """Extract the set of register names saved in the function prologue.

    Uses DW_CFA_offset and DW_CFA_rel_offset rules (register is spilled to stack)
    to identify callee-saved registers.

    Returns:
    - frozenset[str] on successful decode (including empty set), or
    - None when decoding failed and no trustworthy data is available.

    ``decode_failed`` mirrors ``_extract_cfa_reg_from_fde``'s own parameter of
    the same name -- see its docstring for why the distinction matters to a
    caller accumulating completeness across many FDEs.
    """
    try:
        decoded = entry.get_decoded()
        if not decoded.table:
            return frozenset()

        saved: set[str] = set()
        for row in decoded.table:
            for reg_key, rule in row.items():
                if reg_key in ("pc", "cfa"):
                    continue
                # rule is an object with .type; "offset" means register is saved
                rule_type = getattr(rule, "type", None)
                if rule_type and str(rule_type).lower() in (
                    "offset",
                    "reg_rule_offset",
                ):
                    if isinstance(reg_key, int):
                        saved.add(_reg_name(reg_key, arch_key))
        return frozenset(saved)
    except Exception:  # noqa: BLE001
        if decode_failed is not None:
            decode_failed.append(True)
        return None


def _parse_producer(producer: str) -> ToolchainInfo:
    """Parse raw DW_AT_producer string into ToolchainInfo."""
    info = ToolchainInfo(producer_string=producer)

    if "GCC" in producer or "GNU" in producer:
        info.compiler = "GCC"
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", producer)
        if m:
            info.version = m.group(1)
    elif re.search(r"clang|LLVM", producer, re.I):
        info.compiler = "clang"
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", producer)
        if m:
            info.version = m.group(1)
    elif re.search(r"Intel|ICC|ICX|DPC\+\+", producer):
        info.compiler = "ICC"
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", producer)
        if m:
            info.version = m.group(1)

    for m in _ABI_FLAGS_RE.finditer(producer):
        info.abi_flags.add(m.group(0))

    for m in _VECTOR_ABI_FLAGS_RE.finditer(producer):
        info.vector_abi_flags.add(m.group(0))

    for m in _WCHAR_ABI_FLAGS_RE.finditer(producer):
        info.wchar_flags.add(m.group(0))

    return info


# ---------------------------------------------------------------------------
# Attribute helpers — delegated to dwarf_utils
# ---------------------------------------------------------------------------
# _attr_str, _attr_int, _attr_bool, and _resolve_type_die are imported
# from dwarf_utils at the top of this module.

# Public alias for dwarf_unified — keeps the contract visible to mypy.
_process_cu_impl = _process_cu


# ---------------------------------------------------------------------------
# Compatibility shim — old `from abicheck.dwarf_advanced import
# diff_advanced_dwarf`-shaped imports (P1 review)
# ---------------------------------------------------------------------------
# diff_advanced_dwarf and its diff-only siblings moved to
# compare/dwarf_advanced_diff.py (ADR-061 canonical-package migration): this
# module stays classified extract/, so it may not statically import back
# from compare/ (extract -> model, storage only). A downstream caller still
# importing `from abicheck.dwarf_advanced import diff_advanced_dwarf`
# (this module's own tests included, historically) would otherwise see a
# hard ImportError; resolved lazily instead, per AGENTS.md's "Moving
# helpers out of a module that re-exports them?" guidance (see
# cli_buildsource.py's own shim for the identical pattern) -- a static
# `from .compare.dwarf_advanced_diff import ...` re-export would reintroduce
# the same reverse-dependency this split was meant to avoid.
_DWARF_ADVANCED_DIFF_REEXPORTS = frozenset(
    {
        "diff_advanced_dwarf",
        "_diff_calling_conventions",
        "_diff_callee_saved_regs",
        "_sysv_amd64_return_model",
        "_diff_value_abi_traits",
        "_returns_in_registers",
        "_ret_component",
        "_diff_struct_packing",
        "_diff_toolchain_flags",
        "_diff_vector_abi_flags",
        "_diff_wchar_flags",
        "_diff_frame_registers",
    }
)

# P2 review, fresh evidence: `from abicheck.dwarf_advanced import *` no
# longer surfaced `diff_advanced_dwarf` (or its siblings above) despite the
# compatibility shim's own stated promise -- `import *` reads `__all__`
# directly and never consults a module's `__getattr__` (PEP 562) for names
# absent from it. Declaring `__all__` here fixes that: Python's import-star
# machinery resolves each listed name via `getattr(module, name)`, which
# *does* fall through to `__getattr__` for a name not otherwise bound in the
# module namespace, so the lazy resolution above still applies per-name.
__all__ = [
    "AdvancedDwarfMetadata",
    "ToolchainInfo",
    "parse_advanced_dwarf",
    *sorted(_DWARF_ADVANCED_DIFF_REEXPORTS),
]


def __getattr__(name: str) -> Any:
    if name in _DWARF_ADVANCED_DIFF_REEXPORTS:
        import importlib

        return getattr(
            importlib.import_module("abicheck.compare.dwarf_advanced_diff"), name
        )
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
