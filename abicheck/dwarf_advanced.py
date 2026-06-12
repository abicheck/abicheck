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

from .dwarf_utils import BASE_PRUNE_TAGS, has_real_dwarf_info
from .dwarf_utils import attr_bool as _attr_bool
from .dwarf_utils import attr_int as _attr_int
from .dwarf_utils import attr_str as _attr_str
from .dwarf_utils import decode_member_location as _shared_decode_member_location
from .dwarf_utils import resolve_die_ref as _resolve_die_ref
from .dwarf_utils import resolve_type_die as _resolve_type_die

log = logging.getLogger(__name__)

# DW_AT_calling_convention values (DWARF 5 standard + vendor extensions)
_CC_NAMES: dict[int, str] = {
    0x01: "normal",
    0x02: "program",
    0x03: "nocall",
    0x04: "pass_by_reference",      # DWARF 5
    0x05: "pass_by_value",          # DWARF 5
    0x40: "GNU_renesas_sh",
    0x41: "GNU_borland_fastcall_i386",
    0x80: "GNU_push_call_stub",     # GCC internal
    0x81: "GNU_push_arg",           # GCC internal
    0xb0: "BORLAND_safecall",
    0xb1: "BORLAND_stdcall",
    0xb2: "BORLAND_pascal",
    0xb3: "BORLAND_msfastcall",
    0xb4: "BORLAND_msreturn",
    0xb5: "BORLAND_thiscall",
    0xb6: "BORLAND_fastcall",
    0xb9: "LLVM_PreserveMost",
    0xd0: "LLVM_vectorcall",
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
_VECTOR_ABI_FLAGS_RE = re.compile(
    r"-mveclibabi=\S+|-fveclib=\S+|-vecabi=\S+"
)

# Natural alignment (bytes) by type size on most LP64 platforms
_NATURAL_ALIGN: dict[int, int] = {1: 1, 2: 2, 4: 4, 8: 8, 16: 16}

# Tags to prune: don't descend into function bodies or inlined frames
_PRUNE_TAGS: frozenset[str] = BASE_PRUNE_TAGS


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ToolchainInfo:
    """Parsed DW_AT_producer metadata from a binary."""
    producer_string: str = ""       # raw DW_AT_producer value
    compiler: str = ""              # "GCC", "clang", "ICC" (ICC/ICX/DPC++ family)
    version: str = ""               # e.g. "13.2.1"
    abi_flags: set[str] = field(default_factory=set)  # extracted ABI-affecting flags
    vector_abi_flags: set[str] = field(default_factory=set)  # vector-function (SIMD clone) ABI flags


@dataclass
class AdvancedDwarfMetadata:
    """Sprint 4 metadata extracted from a single .so."""
    has_dwarf: bool = False
    # Normalized target architecture (_normalize_arch): "x86_64", "aarch64",
    # "i386", … Empty string when unknown (e.g. arch-less mock snapshots).
    # Gates the SysV-AMD64-specific aggregate-return-convention classification.
    target_arch: str = ""
    toolchain: ToolchainInfo = field(default_factory=ToolchainInfo)
    # linkage_name (mangled) → CC string for ALL externally-visible functions visited.
    # Storing "normal" explicitly lets the diff distinguish "became normal" from
    # "function was removed/added" (sparse dict would conflate the two cases).
    # NOTE: on Linux x86-64 this dict mostly contains "normal" entries since
    # DW_AT_calling_convention is rarely emitted by GCC/Clang for System V AMD64.
    calling_conventions: dict[str, str] = field(default_factory=dict)
    # linkage_name (mangled) → value ABI trait fingerprint derived from DWARF types.
    # Used as fallback signal when DW_AT_calling_convention is not emitted.
    # Example: "ret:v(trivial)" -> "ret:v(nontrivial)" can imply SysV ABI drift.
    value_abi_traits: dict[str, str] = field(default_factory=dict)
    # linkage_name (mangled) → byte size of a by-value aggregate *return* type.
    # Used only to label a return triviality flip: a SysV AMD64 aggregate is
    # returned in registers only when it is trivial AND <= 16 bytes; a larger
    # struct is memory-returned regardless of triviality, so a triviality change
    # there is a value-ABI (copy-semantics) change, not a register<->sret flip.
    return_value_sizes: dict[str, int] = field(default_factory=dict)
    # linkage_name (mangled) → set membership when the by-value aggregate return
    # is forced to memory (sret) by an unaligned member (e.g. a packed struct).
    # Such a type is memory-returned regardless of size/triviality, so a
    # triviality flip there is never a register<->sret convention change.
    return_memory_classified: set[str] = field(default_factory=set)
    # struct names where any field has a misaligned byte offset → __attribute__((packed))
    packed_structs: set[str] = field(default_factory=set)
    # All struct/class names seen (for cross-referencing in diff to avoid
    # false "packing removed" when a struct was simply deleted)
    all_struct_names: set[str] = field(default_factory=set)
    # linkage_name → CFA register name for exported functions (from .eh_frame / .debug_frame).
    # Typically "rsp" or "rbp" on x86-64; empty string when not present.
    # A change from "rbp" (frame-pointer) to "rsp" (stack-pointer) or vice-versa
    # indicates a calling-convention / frame-layout drift (#117).
    frame_registers: dict[str, str] = field(default_factory=dict)
    # linkage_name → frozenset of callee-saved register names for exported functions.
    # Derived from CFI DW_CFA_offset / DW_CFA_rel_offset rules in the function prologue.
    # On x86-64 SysV ABI the callee-saved set is {rbx,rbp,r12-r15}.
    # On x86-64 ms_abi (Windows x64) it additionally includes {rdi,rsi,r10,r11}.
    # Presence of rdi/rsi in the saved-registers set is a strong ELF-level signal
    # that the function uses ms_abi, even when DW_AT_calling_convention is absent (GCC gap).
    callee_saved_regs: dict[str, frozenset[str]] = field(default_factory=dict)


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
            meta = AdvancedDwarfMetadata(has_dwarf=True)
            meta.target_arch = _normalize_arch(elf)
            dwarf = elf.get_dwarf_info()  # type: ignore[no-untyped-call]
            for CU in dwarf.iter_CUs():
                try:
                    _process_cu(CU, meta)
                except (ELFError, OSError, ValueError, KeyError) as exc:
                    log.warning("parse_advanced_dwarf: skipping CU: %s", exc)
            # Parse .eh_frame / .debug_frame CFA register convention (#117)
            _parse_frame_registers(elf, dwarf, meta)
            return meta
    except (ELFError, OSError, ValueError) as exc:
        log.warning("parse_advanced_dwarf: failed %s: %s", so_path, exc)
        return AdvancedDwarfMetadata()


# ---------------------------------------------------------------------------
# Internal: per-CU processing
# ---------------------------------------------------------------------------

def _process_cu(CU: Any, meta: AdvancedDwarfMetadata) -> None:
    top = CU.get_top_DIE()

    # Extract toolchain info from DW_AT_producer on the CU top DIE (first CU wins)
    if not meta.toolchain.producer_string:
        producer = _attr_str(top, "DW_AT_producer")
        if producer:
            meta.toolchain = _parse_producer(producer)

    _walk_cu(top, meta, CU)


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


def _walk_cu(root: Any, meta: AdvancedDwarfMetadata, CU: Any) -> None:
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
            _extract_calling_convention(die, meta, CU, cache=cache)
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
            _check_packed_typedef(die, meta, CU)

        # Push children in reverse order (DFS left-to-right)
        stack.extend(reversed(list(die.iter_children())))


# ---------------------------------------------------------------------------
# Calling convention extraction
# ---------------------------------------------------------------------------

# _resolve_type_die is imported from dwarf_utils at the top of this module.


@dataclass
class _DwarfTypeCache:
    """Per-parse caches to avoid redundant DWARF traversals."""
    unwrap: dict[int, Any] = field(default_factory=dict)    # die.offset → unwrapped DIE
    nontrivial: dict[int, bool] = field(default_factory=dict)  # die.offset → bool


def _is_nontrivial_aggregate(
    type_die: Any,
    cache: dict[int, bool] | None = None,
    CU: Any = None,
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
    result = _check_children_nontrivial(type_die, class_name, cache, CU)

    if cache is not None and key is not None:
        cache[key] = result
    return result


def _check_children_nontrivial(
    type_die: Any, class_name: str, cache: dict[int, bool] | None, CU: Any,
) -> bool:
    """Iterate children of a struct/class DIE to detect non-trivial properties."""
    def _member_type_is_nontrivial(ch: Any) -> bool:
        if CU is None:
            return False
        member_type_die = _resolve_type_die(ch, CU)
        if member_type_die is None:
            return False
        member_tag = getattr(member_type_die, "tag", "")
        if member_tag not in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
            return False
        return _is_nontrivial_aggregate(member_type_die, cache=cache, CU=CU)

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


def _unwrap_qualifiers(type_die: Any, CU: Any, cache: _DwarfTypeCache | None = None) -> Any:
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
            nxt = _resolve_type_die(cur, CU)
            if nxt is None:
                break
            cur = nxt
        else:
            break
    else:
        # for-else: exhausted depth without finding a non-qualifier tag
        log.debug(
            "_unwrap_qualifiers: depth limit reached at tag=%s", getattr(cur, "tag", "?")
        )

    if cache is not None and key is not None:
        cache.unwrap[key] = cur
    return cur


def _value_abi_trait_for_typed_die(die: Any, CU: Any, cache: _DwarfTypeCache | None = None) -> str | None:
    """Return ABI trait for by-value aggregate type (or None if irrelevant).

    Fingerprint contains only ABI-relevant triviality, not type name.
    Type renames don't affect calling convention — including tname causes false positives.
    """
    t0 = _resolve_type_die(die, CU)
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

    t = _unwrap_qualifiers(t0, CU, cache=cache)
    if t.tag not in ("DW_TAG_structure_type", "DW_TAG_class_type", "DW_TAG_union_type"):
        return None

    nontrivial_cache = cache.nontrivial if cache is not None else None
    # Pass CU so member-type non-triviality (e.g. struct Outer { std::string s; }) is detected
    triviality = "nontrivial" if _is_nontrivial_aggregate(t, cache=nontrivial_cache, CU=CU) else "trivial"
    return triviality  # "trivial" or "nontrivial"


def _aggregate_byte_size_for_typed_die(die: Any, CU: Any, cache: _DwarfTypeCache | None = None) -> int | None:
    """Return the byte size of a by-value aggregate type (or None if irrelevant).

    Mirrors :func:`_value_abi_trait_for_typed_die`'s type resolution: only
    struct/class/union types passed/returned *by value* qualify. Used to gate
    the return-convention classification on the SysV register-return threshold.
    """
    t0 = _resolve_type_die(die, CU)
    if t0 is None:
        return None
    if t0.tag in (
        "DW_TAG_pointer_type",
        "DW_TAG_reference_type",
        "DW_TAG_rvalue_reference_type",
    ):
        return None
    t = _unwrap_qualifiers(t0, CU, cache=cache)
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


def _type_unaligned_at(type_die: Any, CU: Any, base_offset: int, cache: _DwarfTypeCache | None) -> bool:
    """Whether any scalar leaf of *type_die* lands at a misaligned absolute offset.

    *base_offset* is the absolute offset at which this type starts within the
    outermost aggregate. Recurses through nested aggregates (carrying member
    offsets) and array members (an array shares its element's alignment, so the
    array's own offset determines element alignment). A scalar/enum/pointer leaf
    is misaligned when ``base_offset`` is not a multiple of its natural alignment.
    By-value nesting is a DAG, so this terminates.
    """
    t = _unwrap_qualifiers(type_die, CU, cache=cache)
    if t.tag in _SCALAR_LEAF_TAGS:
        return base_offset % _scalar_leaf_align(t) != 0
    if t.tag == "DW_TAG_array_type":
        elem = _resolve_type_die(t, CU)
        return elem is not None and _type_unaligned_at(elem, CU, base_offset, cache)
    if t.tag in _AGGREGATE_TAGS:
        for child in t.iter_children():
            if child.tag != "DW_TAG_member" or _attr_int(child, "DW_AT_bit_size"):
                continue
            mt = _resolve_type_die(child, CU)
            if mt is None:
                continue
            abs_offset = base_offset + _decode_member_location(child)
            if _type_unaligned_at(mt, CU, abs_offset, cache):
                return True
    return False


def _aggregate_has_unaligned_member(die: Any, CU: Any, cache: _DwarfTypeCache | None = None) -> bool:
    """Whether a by-value aggregate return type has an unaligned member (recursively).

    A struct/class/union with a leaf at a misaligned offset (e.g. a packed
    aggregate) is MEMORY-classified by the SysV AMD64 ABI regardless of size, so
    it is returned via a hidden sret pointer either way. Walks the full type tree
    — nested aggregates and array members included — accumulating absolute
    offsets, so e.g. ``packed R{char c; int a[1];}`` (``a[0]`` at offset 1) and
    ``packed Outer{char c; Inner{double d};}`` (``i.d`` at offset 1) are caught.
    """
    t0 = _resolve_type_die(die, CU)
    if t0 is None or t0.tag in (
        "DW_TAG_pointer_type",
        "DW_TAG_reference_type",
        "DW_TAG_rvalue_reference_type",
    ):
        return False
    t = _unwrap_qualifiers(t0, CU, cache=cache)
    if t.tag not in _AGGREGATE_TAGS:
        return False
    return _type_unaligned_at(t, CU, 0, cache)


def _extract_calling_convention(die: Any, meta: AdvancedDwarfMetadata, CU: Any, cache: _DwarfTypeCache | None = None) -> None:
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
    ret_trait = _value_abi_trait_for_typed_die(die, CU, cache=cache)
    if ret_trait is not None:
        parts.append(f"ret:{ret_trait}")
        ret_size = _aggregate_byte_size_for_typed_die(die, CU, cache=cache)
        if ret_size is not None:
            meta.return_value_sizes[key] = ret_size
        if _aggregate_has_unaligned_member(die, CU, cache=cache):
            meta.return_memory_classified.add(key)
    pidx = 0
    for ch in die.iter_children():
        if ch.tag != "DW_TAG_formal_parameter":
            continue
        ptrait = _value_abi_trait_for_typed_die(ch, CU, cache=cache)
        if ptrait is not None:
            parts.append(f"p{pidx}:{ptrait}")
        pidx += 1
    if parts:
        meta.value_abi_traits[key] = "|".join(parts)


# ---------------------------------------------------------------------------
# Packed struct detection
# ---------------------------------------------------------------------------

def _check_packed_typedef(die: Any, meta: AdvancedDwarfMetadata, CU: Any) -> None:
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
            log.debug("packed struct detected: %s field at offset %d (natural align %d)",
                      name, offset, natural)
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
    0: "rax", 1: "rdx", 2: "rcx", 3: "rbx", 4: "rsi", 5: "rdi",
    6: "rbp", 7: "rsp", 8: "r8", 9: "r9", 10: "r10", 11: "r11",
    12: "r12", 13: "r13", 14: "r14", 15: "r15", 16: "rip",
}
_REG_NAMES_X86: dict[int, str] = {
    0: "eax", 1: "ecx", 2: "edx", 3: "ebx", 4: "esp", 5: "ebp",
    6: "esi", 7: "edi", 8: "eip",
}
_REG_NAMES_AARCH64: dict[int, str] = {
    **{i: f"x{i}" for i in range(31)},
    31: "sp", 32: "pc",
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
        "x64": "x64", "x86_64": "x64",
        "x86": "x86", "i386": "x86",
        "AArch64": "aarch64", "aarch64": "aarch64",
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


def _get_cfi_source(dwarf: Any) -> Any:
    """Return CFI entry iterator, preferring .eh_frame over .debug_frame."""
    try:
        src = dwarf.get_EH_CFI_entries()
        if src is not None:
            return src
    except (AttributeError, ELFError):
        pass
    try:
        return dwarf.get_CFI_entries()
    except (AttributeError, ELFError):
        return None


def _extract_cfa_reg_from_fde(entry: Any, arch_key: str) -> str | None:
    """Extract the dominant CFA register name from an FDE.

    Returns the register name string (e.g. 'rsp', 'rbp') or None if not found.

    Heuristic:
    - Build a sequence of (pc, reg_num) rows where CFA is available.
    - Select the modal CFA register across decoded rows (most frequent), which
      captures the settled function-body convention and avoids epilogue bias.
    - Break ties by selecting the register from the highest-PC row among tied
      candidates (preserves post-prologue behavior for 2-row entry/body tables).
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
        return None


def _parse_frame_registers(elf: Any, dwarf: Any, meta: AdvancedDwarfMetadata) -> None:
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
    """
    try:
        arch_key = _normalize_arch(elf)
        addr_to_sym = _build_addr_to_sym(elf)
        cfi_src = _get_cfi_source(dwarf)
        if cfi_src is None:
            return

        for entry in cfi_src:
            try:
                if entry.__class__.__name__ != "FDE":
                    continue
                pc_begin: int = entry["initial_location"]
                sym_name = addr_to_sym.get(pc_begin, "")
                if not sym_name:
                    continue
                reg = _extract_cfa_reg_from_fde(entry, arch_key)
                if reg is not None:
                    meta.frame_registers[sym_name] = reg
                # Extract callee-saved register fingerprint from prologue
                saved = _extract_callee_saved_regs(entry, arch_key)
                if saved is not None:
                    meta.callee_saved_regs[sym_name] = saved
            except (ELFError, OSError, ValueError, KeyError, IndexError) as exc:
                log.debug("_parse_frame_registers: skipping FDE: %s", exc)

    except (ELFError, OSError, ValueError) as exc:
        log.warning("_parse_frame_registers: failed: %s", exc)


def _extract_callee_saved_regs(entry: Any, arch_key: str) -> frozenset[str] | None:
    """Extract the set of register names saved in the function prologue.

    Uses DW_CFA_offset and DW_CFA_rel_offset rules (register is spilled to stack)
    to identify callee-saved registers.

    Returns:
    - frozenset[str] on successful decode (including empty set), or
    - None when decoding failed and no trustworthy data is available.
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
                if rule_type and str(rule_type).lower() in ("offset", "reg_rule_offset"):
                    if isinstance(reg_key, int):
                        saved.add(_reg_name(reg_key, arch_key))
        return frozenset(saved)
    except Exception:  # noqa: BLE001
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

    return info


# ---------------------------------------------------------------------------
# Diff (called from checker.py _diff_advanced_dwarf)
# ---------------------------------------------------------------------------

def _diff_calling_conventions(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> tuple[list[tuple[str, str, str, str | None, str | None]], set[str]]:
    """Diff explicit DW_AT_calling_convention. Returns (results, already_reported_cc)."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_cc_keys = set(old_meta.calling_conventions)
    new_cc_keys = set(new_meta.calling_conventions)
    for fname in sorted(old_cc_keys & new_cc_keys):
        old_cc = old_meta.calling_conventions[fname]
        new_cc = new_meta.calling_conventions[fname]
        if old_cc != new_cc:
            results.append((
                "calling_convention_changed", fname,
                f"Calling convention changed: {fname} ({old_cc} → {new_cc})",
                old_cc, new_cc,
            ))
    already_reported_cc = {fname for fname in (old_cc_keys & new_cc_keys)
                           if old_meta.calling_conventions[fname] != new_meta.calling_conventions[fname]}
    return results, already_reported_cc


def _diff_callee_saved_regs(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
    already_reported_cc: set[str],
) -> tuple[list[tuple[str, str, str, str | None, str | None]], set[str]]:
    """Diff ELF CFI callee-saved fingerprint. Returns (results, updated already_reported_cc)."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_saved_keys = set(old_meta.callee_saved_regs)
    new_saved_keys = set(new_meta.callee_saved_regs)
    already_reported_cc = set(already_reported_cc)
    _MS_ABI_MARKERS = frozenset(("rdi", "rsi"))
    for fname in sorted((old_saved_keys & new_saved_keys) - already_reported_cc):
        old_saved = old_meta.callee_saved_regs[fname]
        new_saved = new_meta.callee_saved_regs[fname]
        if old_saved != new_saved:
            old_has_ms_hint = bool(old_saved & _MS_ABI_MARKERS)
            new_has_ms_hint = bool(new_saved & _MS_ABI_MARKERS)
            if old_has_ms_hint == new_has_ms_hint:
                continue
            results.append((
                "calling_convention_changed", fname,
                f"Calling convention changed (ELF CFI fallback): {fname} "
                f"(saved regs: {sorted(old_saved)} → {sorted(new_saved)}) "
                f"(ms_abi/sysv_abi drift inferred from CFI saved regs)",
                ",".join(sorted(old_saved)), ",".join(sorted(new_saved)),
            ))
            already_reported_cc.add(fname)
    return results, already_reported_cc


#: SysV AMD64 returns a trivial aggregate in registers only when it fits in two
#: eightbytes (<= 16 bytes); larger aggregates are returned via a hidden pointer
#: regardless of triviality. Used to gate the return-convention classification.
_SYSV_MAX_REGISTER_RETURN_BYTES = 16

#: Architectures whose by-value aggregate-return rules match the SysV AMD64
#: model encoded in ``_returns_in_registers`` (trivial-for-calls AND <= 16
#: bytes AND no unaligned member → registers, else hidden sret pointer). The
#: register<->sret *convention-flip* classification is only sound for these.
#: Other ABIs use different rules — an AArch64 HFA such as ``struct {double
#: a,b,c,d;}`` is returned in vector registers despite being 32 bytes; i386
#: returns every aggregate via memory — so a triviality flip there is just a
#: generic value-ABI change, not a convention flip. An empty/unknown arch is
#: treated as SysV AMD64 to preserve behaviour for arch-less mocks/snapshots.
_SYSV_AMD64_RETURN_ARCHES = frozenset({"x86_64", "x64", ""})


def _sysv_amd64_return_model(old_arch: str, new_arch: str) -> bool:
    """Whether both sides use the SysV-AMD64 aggregate-return model (or unknown)."""
    return old_arch in _SYSV_AMD64_RETURN_ARCHES and new_arch in _SYSV_AMD64_RETURN_ARCHES


def _diff_value_abi_traits(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
    already_reported_cc: set[str],
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff DWARF value-ABI trait fingerprints. Returns results list."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_trait_keys = set(old_meta.value_abi_traits)
    new_trait_keys = set(new_meta.value_abi_traits)
    # The sret-flip classification is only sound for the SysV AMD64 return model.
    sysv_return = _sysv_amd64_return_model(old_meta.target_arch, new_meta.target_arch)
    for fname in sorted((old_trait_keys & new_trait_keys) - already_reported_cc):
        old_trait = old_meta.value_abi_traits[fname]
        new_trait = new_meta.value_abi_traits[fname]
        old_rc = _ret_component(old_trait)
        new_rc = _ret_component(new_trait)
        old_reg = _returns_in_registers(
            old_rc, old_meta.return_value_sizes.get(fname),
            fname in old_meta.return_memory_classified,
        )
        new_reg = _returns_in_registers(
            new_rc, new_meta.return_value_sizes.get(fname),
            fname in new_meta.return_memory_classified,
        )
        # struct_return_convention_changed only on the SysV AMD64 return model
        # (``sysv_return``) and when BOTH sides return an aggregate by value (both
        # ret components present) AND the register-vs-hidden-sret mechanism
        # actually flipped — this covers a triviality flip, a size crossing the
        # SysV 16-byte threshold (trait unchanged), or a packing change that
        # forces MEMORY. On other ABIs the rules differ, so a changed trait falls
        # through to the generic finding. When the return component is only
        # added/removed (aggregate <-> scalar) the scalar side can still be
        # register-returned, so that is left to the generic return/type findings.
        if sysv_return and old_rc is not None and new_rc is not None and old_reg != new_reg:
            results.append((
                "struct_return_convention_changed", fname,
                f"Aggregate return convention changed: {fname} "
                f"({old_trait} → {new_trait})",
                old_trait, new_trait,
            ))
        elif old_trait != new_trait:
            # Same return mechanism (or a non-return trait change), but the
            # value-ABI fingerprint still changed — a generic value-ABI trait
            # change (parameter passing or copy-semantics).
            results.append((
                "value_abi_trait_changed", fname,
                f"DWARF value-ABI trait changed: {fname} ({old_trait} → {new_trait})",
                old_trait, new_trait,
            ))
        # else: identical trait and same return mechanism — nothing to report.
    return results


def _returns_in_registers(
    ret_component: str | None, size: int | None, memory_forced: bool = False
) -> bool:
    """Whether a by-value aggregate return is passed in registers (SysV AMD64).

    A struct is register-returned only when it is **trivial for the purposes of
    calls**, fits in two eightbytes (<= 16 bytes), *and* has no unaligned member.
    A non-trivial aggregate, a large one, or one with an unaligned member (e.g. a
    packed struct, ``memory_forced``) is memory-returned via a hidden sret
    pointer. An unknown size on an otherwise-eligible trivial aggregate is
    treated as register-eligible (stay conservative — preserves the pre-size-
    gating behaviour for snapshots/mocks that carry no size).
    """
    if ret_component is None or memory_forced:
        return False
    # Component is the triviality token: "trivial"/"nontrivial" (or the mock
    # "v(trivial)"/"v(nontrivial)"). It is non-trivial iff it says so.
    if "nontrivial" in ret_component:
        return False
    return size is None or size <= _SYSV_MAX_REGISTER_RETURN_BYTES


def _ret_component(trait: str) -> str | None:
    """Extract the ``ret:`` component of a value-ABI trait fingerprint.

    Trait strings look like ``"ret:trivial|p0:nontrivial"``; returns the value
    after ``ret:`` (e.g. ``"trivial"``) or ``None`` when the function has no
    by-value aggregate return component.
    """
    for part in trait.split("|"):
        if part.startswith("ret:"):
            return part[4:]
    return None


def _diff_struct_packing(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff struct packing attributes. Returns results list."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    both_struct_names = old_meta.all_struct_names & new_meta.all_struct_names
    for name in sorted((old_meta.packed_structs - new_meta.packed_structs) & both_struct_names):
        results.append((
            "struct_packing_changed", name,
            f"Struct packing removed: {name} was packed, now standard layout",
            "packed", "standard",
        ))
    for name in sorted((new_meta.packed_structs - old_meta.packed_structs) & old_meta.all_struct_names):
        results.append((
            "struct_packing_changed", name,
            f"Struct packing added: {name} is now __attribute__((packed))",
            "standard", "packed",
        ))
    return results


def _diff_toolchain_flags(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff ABI-affecting compiler flags. Returns results list."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_flags = old_meta.toolchain.abi_flags
    new_flags = new_meta.toolchain.abi_flags
    removed_flags = old_flags - new_flags
    added_flags = new_flags - old_flags
    if removed_flags or added_flags:
        parts = []
        if added_flags:
            parts.append(f"added: {', '.join(sorted(added_flags))}")
        if removed_flags:
            parts.append(f"removed: {', '.join(sorted(removed_flags))}")
        results.append((
            "toolchain_flag_drift", "<toolchain>",
            f"ABI-affecting compiler flags changed: {'; '.join(parts)}",
            ",".join(sorted(old_flags)) or None,
            ",".join(sorted(new_flags)) or None,
        ))
    return results


def _diff_vector_abi_flags(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff vector-function (SIMD clone) ABI flags. Returns results list.

    A change in the vector-ABI flag set (-mveclibabi/-fveclib/-vecabi) means
    the vectorized call variants of functions resolve to a different ABI, which
    breaks callers that were compiled against the old vector entry points.
    """
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_flags = old_meta.toolchain.vector_abi_flags
    new_flags = new_meta.toolchain.vector_abi_flags
    if old_flags != new_flags:
        removed_flags = old_flags - new_flags
        added_flags = new_flags - old_flags
        parts = []
        if added_flags:
            parts.append(f"added: {', '.join(sorted(added_flags))}")
        if removed_flags:
            parts.append(f"removed: {', '.join(sorted(removed_flags))}")
        results.append((
            "vector_abi_changed", "<vector-abi>",
            f"Vector-function (SIMD clone) ABI flags changed: {'; '.join(parts)}",
            ",".join(sorted(old_flags)) or None,
            ",".join(sorted(new_flags)) or None,
        ))
    return results


def _diff_frame_registers(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Diff frame/CFA register usage. Returns results list."""
    results: list[tuple[str, str, str, str | None, str | None]] = []
    old_fr_keys = set(old_meta.frame_registers)
    new_fr_keys = set(new_meta.frame_registers)
    for fname in sorted(old_fr_keys & new_fr_keys):
        old_reg = old_meta.frame_registers[fname]
        new_reg = new_meta.frame_registers[fname]
        if old_reg != new_reg:
            results.append((
                "frame_register_changed", fname,
                f"Frame/CFA register changed: {fname} ({old_reg} → {new_reg})",
                old_reg, new_reg,
            ))
    return results


def diff_advanced_dwarf(
    old_meta: AdvancedDwarfMetadata,
    new_meta: AdvancedDwarfMetadata,
) -> list[tuple[str, str, str, str | None, str | None]]:
    """Return (kind, symbol, description, old_value, new_value) tuples.

    Returns [] gracefully if either side has no DWARF.
    """
    if not old_meta.has_dwarf or not new_meta.has_dwarf:
        return []

    cc_results, already_reported_cc = _diff_calling_conventions(old_meta, new_meta)
    csr_results, already_reported_cc = _diff_callee_saved_regs(old_meta, new_meta, already_reported_cc)
    trait_results = _diff_value_abi_traits(old_meta, new_meta, already_reported_cc)
    pack_results = _diff_struct_packing(old_meta, new_meta)
    flag_results = _diff_toolchain_flags(old_meta, new_meta)
    vec_results = _diff_vector_abi_flags(old_meta, new_meta)
    frame_results = _diff_frame_registers(old_meta, new_meta)

    return (cc_results + csr_results + trait_results + pack_results
            + flag_results + vec_results + frame_results)


# ---------------------------------------------------------------------------
# Attribute helpers — delegated to dwarf_utils
# ---------------------------------------------------------------------------
# _attr_str, _attr_int, _attr_bool, and _resolve_type_die are imported
# from dwarf_utils at the top of this module.

# Public alias for dwarf_unified — keeps the contract visible to mypy.
_process_cu_impl = _process_cu
