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

"""Mach-O metadata for macOS/iOS dynamic libraries (.dylib / .framework).

Uses ``macholib`` for parsing Mach-O headers, load commands, exported symbols,
and dependency information from Apple shared libraries. Supports both
single-arch and fat/universal binaries (preferred arch slice is analyzed).
"""
from __future__ import annotations

import logging
import os
import platform
import re
import stat
import struct
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Any

from macholib.mach_o import (  # type: ignore[import-untyped]
    CPU_TYPE_NAMES,
    LC_BUILD_VERSION,
    LC_DYLD_INFO,
    LC_DYLD_INFO_ONLY,
    LC_ID_DYLIB,
    LC_LOAD_DYLIB,
    LC_REEXPORT_DYLIB,
    LC_RPATH,
    LC_SEGMENT,
    LC_SEGMENT_64,
    LC_VERSION_MIN_MACOSX,
    N_EXT,
    N_TYPE,
    N_UNDF,
    N_WEAK_DEF,
)
from macholib.MachO import MachO  # type: ignore[import-untyped]
from macholib.SymbolTable import SymbolTable  # type: ignore[import-untyped]

log = logging.getLogger(__name__)

# macholib uses lowercase short names ("dylib"); we use the traditional MH_* form.
_FILETYPE_NAMES: dict[int, str] = {
    1: "MH_OBJECT",
    2: "MH_EXECUTE",
    3: "MH_FVMLIB",
    4: "MH_CORE",
    5: "MH_PRELOAD",
    6: "MH_DYLIB",
    7: "MH_DYLINKER",
    8: "MH_BUNDLE",
    9: "MH_DYLIB_STUB",
    10: "MH_DSYM",
    11: "MH_KEXT_BUNDLE",
}


class MachoSymbolType(str, Enum):
    EXPORTED = "exported"     # N_EXT: externally visible
    WEAK = "weak"             # N_WEAK_DEF: weak definition
    REEXPORT = "reexport"     # re-exported from another dylib
    OTHER = "other"


@dataclass
class MachoExport:
    """A single exported symbol from a Mach-O binary."""
    name: str
    sym_type: MachoSymbolType = MachoSymbolType.EXPORTED
    is_weak: bool = False
    is_data: bool = False  # True when symbol lives in __DATA segment (global variable)


@dataclass
class MachoMetadata:
    """Mach-O metadata from a macOS dynamic library.

    NOTE: Do NOT add ``frozen=True`` — ``@cached_property`` requires a
    writable ``__dict__``.
    """
    # Binary characteristics
    cpu_type: str = ""                   # selected slice, e.g. "ARM64", "X86_64"
    cpu_types: list[str] = field(default_factory=list)  # ALL slices in a fat/universal binary
    filetype: str = ""                   # e.g. "MH_DYLIB", "MH_BUNDLE"
    flags: int = 0                       # MH_* flags bitmask

    # Install name (equivalent of ELF SONAME)
    install_name: str = ""               # LC_ID_DYLIB install name

    # Dependencies (equivalent of ELF DT_NEEDED)
    dependent_libs: list[str] = field(default_factory=list)  # LC_LOAD_DYLIB

    # Re-exported libraries
    reexported_libs: list[str] = field(default_factory=list)  # LC_REEXPORT_DYLIB

    # Exported symbols
    exports: list[MachoExport] = field(default_factory=list)

    # Imported (undefined, N_UNDF) external symbol names — the Mach-O analogue
    # of ELF undefined imports. Needed to see a CPython extension's libpython
    # C-API import surface (G14). Leading '_' stripped, matching exports.
    imported_symbols: list[str] = field(default_factory=list)

    # Version info from LC_ID_DYLIB
    current_version: str = ""            # e.g. "1.2.3"
    compat_version: str = ""             # e.g. "1.0.0"

    # Minimum OS version
    min_os_version: str = ""             # from LC_VERSION_MIN_MACOSX or LC_BUILD_VERSION

    # Runtime search paths (LC_RPATH) — the Mach-O analogue of ELF DT_RUNPATH.
    # Tri-state: None = not captured (legacy snapshot written before this
    # field existed); [] = parsed Mach-O carrying no LC_RPATH commands.
    rpaths: list[str] | None = None

    @cached_property
    def export_map(self) -> dict[str, MachoExport]:
        """Name → MachoExport mapping (built once, cached on first access)."""
        return {e.name: e for e in self.exports if e.name}


# ---------------------------------------------------------------------------
# Magic detection
# ---------------------------------------------------------------------------

# Mach-O magic numbers (both byte orders + fat/universal binaries)
_MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce",   # MH_MAGIC (32-bit)
    b"\xce\xfa\xed\xfe",   # MH_CIGAM (32-bit, swapped)
    b"\xfe\xed\xfa\xcf",   # MH_MAGIC_64 (64-bit)
    b"\xcf\xfa\xed\xfe",   # MH_CIGAM_64 (64-bit, swapped)
    b"\xca\xfe\xba\xbe",   # FAT_MAGIC (universal binary)
    b"\xbe\xba\xfe\xca",   # FAT_CIGAM (universal, swapped)
    b"\xca\xfe\xba\xbf",   # FAT_MAGIC_64 (fat64 universal binary)
    b"\xbf\xba\xfe\xca",   # FAT_CIGAM_64 (fat64, swapped)
}


def is_macho(path: Path) -> bool:
    """Check if file starts with a Mach-O magic number."""
    try:
        with open(path, "rb") as f:
            magic = f.read(4)
            return magic in _MACHO_MAGICS
    except OSError:
        return False


def _version_str(packed: int) -> str:
    """Convert packed Mach-O version (xxxx.yy.zz) to string."""
    major = (packed >> 16) & 0xFFFF
    minor = (packed >> 8) & 0xFF
    patch = packed & 0xFF
    return f"{major}.{minor}.{patch}"


def _version_field_to_str(value: Any) -> str:
    """Decode macholib version field to dotted string.

    Handles either:
    - raw packed integer-like values, or
    - ``mach_version_helper`` objects that store packed value in ``_version``.
    """
    packed = getattr(value, "_version", None)
    if packed is not None:
        return _version_str(int(packed))
    return _version_str(int(value))


# CPU_SUBTYPE_ARM64E (2) selects the pointer-authenticating arm64e ABI — a
# distinct, non-interchangeable ABI from plain arm64 for dylibs. The high bits
# of cpusubtype are capability flags (e.g. versioned-ABI markers), masked off.
_CPU_TYPE_ARM64 = 0x0100000C
_CPU_SUBTYPE_ARM64E = 2
_CPU_SUBTYPE_MASK = 0x00FFFFFF


def _cpu_slice_name(cputype: int, cpusubtype: int) -> str:
    """Human-readable architecture name for a slice, arm64e-aware."""
    if cputype == _CPU_TYPE_ARM64 and (cpusubtype & _CPU_SUBTYPE_MASK) == _CPU_SUBTYPE_ARM64E:
        return "ARM64E"
    return str(CPU_TYPE_NAMES.get(cputype, f"0x{cputype:x}"))


def _dylib_name_from_cmd(data: bytes) -> str:
    """Extract the library name string from dylib load command data.

    In macholib's command tuple ``(lc, cmd, data)``, *data* contains the
    raw bytes that follow the typed command struct — for dylib commands
    this is exactly the null-terminated library path.
    """
    if not data:
        return ""
    end = data.find(b"\x00")
    if end < 0:
        end = len(data)
    return data[:end].decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# dyld export trie (LC_DYLD_INFO / LC_DYLD_EXPORTS_TRIE)
# ---------------------------------------------------------------------------

# linkedit_data_command holding just the export trie (modern linkers emit this
# instead of LC_DYLD_INFO). Defined locally — older macholib lacks the name.
_LC_DYLD_EXPORTS_TRIE = 0x80000033

_EXPORT_SYMBOL_FLAGS_WEAK_DEFINITION = 0x04
_EXPORT_SYMBOL_FLAGS_REEXPORT = 0x08
_EXPORT_SYMBOL_FLAGS_STUB_AND_RESOLVER = 0x10

_TRIE_MAX_DEPTH = 128


def _read_uleb128(data: bytes, off: int) -> tuple[int, int]:
    """Read a ULEB128 value; returns (value, next_offset)."""
    result = 0
    shift = 0
    while off < len(data):
        byte = data[off]
        off += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, off
        shift += 7
        if shift > 63:
            break
    raise ValueError("malformed ULEB128 in export trie")


def _walk_export_trie(data: bytes) -> list[tuple[str, int]]:
    """Walk a dyld export trie, returning ``(symbol_name, flags)`` pairs.

    The classic symbol table misses trie-only facts (weak-def, re-export,
    stub-and-resolver flags on modern binaries whose nlist entries are
    stripped), so exports recovered here complement `_parse_macho_symbols`.
    Malformed input raises ValueError (caught by the caller).
    """
    results: list[tuple[str, int]] = []
    visited: set[int] = set()

    def visit(off: int, prefix: str, depth: int) -> None:
        if depth > _TRIE_MAX_DEPTH or off in visited or off >= len(data):
            return
        visited.add(off)
        terminal_size, pos = _read_uleb128(data, off)
        if terminal_size > 0:
            flags, _ = _read_uleb128(data, pos)
            results.append((prefix, flags))
        # The terminal payload occupies exactly terminal_size bytes after the
        # size field; the child list starts right past it.
        pos += terminal_size
        if pos >= len(data):
            return
        child_count = data[pos]
        pos += 1
        for _ in range(child_count):
            end = data.find(b"\x00", pos)
            if end < 0:
                return
            edge = data[pos:end].decode("utf-8", errors="replace")
            pos = end + 1
            child_off, pos = _read_uleb128(data, pos)
            visit(child_off, prefix + edge, depth + 1)

    visit(0, "", 0)
    return results


def _parse_export_trie(dylib_path: Path, header: Any, meta: MachoMetadata) -> None:
    """Merge dyld-export-trie facts into *meta.exports*.

    Adds trie-only exports the classic symbol table missed and upgrades the
    weak / re-export flags of exports already seen there.
    """
    export_off = export_size = 0
    for lc, cmd, _data in header.commands:
        if lc.cmd in (LC_DYLD_INFO, LC_DYLD_INFO_ONLY):
            export_off = int(getattr(cmd, "export_off", 0))
            export_size = int(getattr(cmd, "export_size", 0))
            break
        if lc.cmd == _LC_DYLD_EXPORTS_TRIE:
            export_off = int(getattr(cmd, "dataoff", 0))
            export_size = int(getattr(cmd, "datasize", 0))
            break
    if not export_off or not export_size or export_size > 64 * 1024 * 1024:
        return
    try:
        with open(dylib_path, "rb") as f:
            f.seek(int(getattr(header, "offset", 0)) + export_off)
            trie = f.read(export_size)
        entries = _walk_export_trie(trie)
    except (OSError, ValueError) as exc:
        log.debug("parse_macho_metadata: export trie unreadable for %s: %s", dylib_path, exc)
        return

    by_name = {e.name: e for e in meta.exports if e.name}
    for raw_name, flags in entries:
        name = raw_name[1:] if raw_name.startswith("_") else raw_name
        if not name:
            continue
        is_weak = bool(flags & _EXPORT_SYMBOL_FLAGS_WEAK_DEFINITION)
        is_reexport = bool(flags & _EXPORT_SYMBOL_FLAGS_REEXPORT)
        existing = by_name.get(name)
        if existing is not None:
            if is_weak and not existing.is_weak:
                existing.is_weak = True
                existing.sym_type = MachoSymbolType.WEAK
            if is_reexport:
                existing.sym_type = MachoSymbolType.REEXPORT
            continue
        sym_type = (
            MachoSymbolType.REEXPORT
            if is_reexport
            else MachoSymbolType.WEAK if is_weak else MachoSymbolType.EXPORTED
        )
        export = MachoExport(name=name, sym_type=sym_type, is_weak=is_weak)
        meta.exports.append(export)
        by_name[name] = export


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_macho_metadata(dylib_path: Path) -> MachoMetadata:
    """Extract Mach-O export/import metadata from *dylib_path*.

    Uses ``macholib`` for parsing. For fat/universal binaries, selects the
    host-architecture slice (arm64 or x86_64); falls back to the first
    available slice.

    Returns an empty ``MachoMetadata`` on any parse error (logged as WARNING).
    """
    try:
        with open(dylib_path, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("parse_macho_metadata: not a regular file: %s", dylib_path)
                return MachoMetadata()

        return _parse(dylib_path)
    except (OSError, ValueError, KeyError, struct.error) as exc:
        log.warning("parse_macho_metadata: failed to parse %s: %s", dylib_path, exc)
        return MachoMetadata()


def _select_header(macho: MachO) -> Any:
    """Pick the best architecture header from a (possibly fat) MachO object.

    Prefers arm64 on Apple Silicon, x86_64 otherwise; falls back to first.
    Returns a ``MachOHeader`` instance or *None*.
    """
    if not macho.headers:
        return None
    if len(macho.headers) == 1:
        return macho.headers[0]

    # CPU type constants
    _CPU_TYPE_X86_64 = 0x01000007  # pylint: disable=invalid-name
    _CPU_TYPE_ARM64 = 0x0100000C  # pylint: disable=invalid-name

    preferred = _CPU_TYPE_ARM64 if platform.machine() in ("arm64", "aarch64") else _CPU_TYPE_X86_64
    fallback_type = _CPU_TYPE_X86_64 if preferred == _CPU_TYPE_ARM64 else _CPU_TYPE_ARM64

    for hdr in macho.headers:
        if int(hdr.header.cputype) == preferred:
            return hdr
    for hdr in macho.headers:
        if int(hdr.header.cputype) == fallback_type:
            return hdr
    return macho.headers[0]


def _parse(dylib_path: Path) -> MachoMetadata:
    """Parse Mach-O metadata using macholib."""
    macho = MachO(str(dylib_path))
    header = _select_header(macho)
    if header is None:
        return MachoMetadata()

    meta = MachoMetadata()
    hdr = header.header

    # Basic header info. Slice names are arm64e-aware: arm64 vs arm64e are
    # distinct, non-interchangeable ABIs (pointer authentication), so they
    # must not collapse into the same "ARM64" label.
    cputype = int(hdr.cputype)
    meta.cpu_type = _cpu_slice_name(cputype, int(getattr(hdr, "cpusubtype", 0)))
    # All architectures present (fat/universal binaries carry several). Used by
    # the arch-drift detector so adding a slice (single-arch → universal) is not
    # mistaken for an architecture change when the original slice still ships.
    meta.cpu_types = [
        _cpu_slice_name(int(h.header.cputype), int(getattr(h.header, "cpusubtype", 0)))
        for h in macho.headers
    ]
    filetype = int(hdr.filetype)
    meta.filetype = _FILETYPE_NAMES.get(filetype, f"0x{filetype:x}")
    meta.flags = int(hdr.flags)

    # Parse load commands. A successful parse captures the LC_RPATH surface
    # even when no such command exists ([] = "verified none"), keeping None
    # reserved for legacy snapshots where it was never looked at.
    rpaths: list[str] = []
    for lc, cmd, data in header.commands:
        cmd_type = lc.cmd

        if cmd_type == LC_ID_DYLIB:
            meta.install_name = _dylib_name_from_cmd(data)
            meta.current_version = _version_field_to_str(cmd.current_version)
            meta.compat_version = _version_field_to_str(cmd.compatibility_version)

        elif cmd_type == LC_LOAD_DYLIB:
            name = _dylib_name_from_cmd(data)
            if name:
                meta.dependent_libs.append(name)

        elif cmd_type == LC_REEXPORT_DYLIB:
            name = _dylib_name_from_cmd(data)
            if name:
                meta.reexported_libs.append(name)

        elif cmd_type == LC_VERSION_MIN_MACOSX:
            meta.min_os_version = _version_str(int(cmd.version))  # p_uint32

        elif cmd_type == LC_BUILD_VERSION:
            meta.min_os_version = _version_str(int(cmd.minos))  # p_uint32

        elif cmd_type == LC_RPATH:
            path = _dylib_name_from_cmd(data)
            if path:
                rpaths.append(path)
    meta.rpaths = rpaths

    # Build section ordinal → segment name mapping so we can distinguish
    # __TEXT (function) from __DATA (variable) symbols via nlist n_sect.
    _section_segment: dict[int, str] = {}  # 1-based ordinal → segment name
    _sect_ordinal = 1
    for lc, cmd, data in header.commands:
        if lc.cmd in (LC_SEGMENT, LC_SEGMENT_64) and isinstance(data, list):
            seg_name = getattr(cmd, "segname", b"")
            if isinstance(seg_name, bytes):
                seg_name = seg_name.rstrip(b"\x00").decode("utf-8", errors="replace")
            for _sect in data:
                _section_segment[_sect_ordinal] = seg_name
                _sect_ordinal += 1

    # Parse exported symbols via SymbolTable
    _parse_macho_symbols(macho, header, _section_segment, meta, dylib_path)

    # Merge dyld export-trie facts (trie-only exports, weak/re-export flags).
    _parse_export_trie(dylib_path, header, meta)

    return meta


def _parse_macho_symbols(
    macho: MachO,
    header: Any,
    section_segment: dict[int, str],
    meta: MachoMetadata,
    dylib_path: Path,
) -> None:
    """Parse Mach-O symbol table and populate *meta.exports*."""
    try:
        symtab = SymbolTable(macho, header=header)
        # Prefer the split external tables (available when LC_DYSYMTAB is
        # present): extdefsyms = external *defined* (exports), undefsyms =
        # external *undefined* (imports). Concatenate both so the import surface
        # is captured; fall back to nlists (all symbols) with manual filtering.
        extdef = getattr(symtab, "extdefsyms", None)
        undef = getattr(symtab, "undefsyms", None)
        if extdef is not None or undef is not None:
            symbols = list(extdef or []) + list(undef or [])
        else:
            symbols = symtab.nlists
        for nlist_entry, name_bytes in symbols:
            n_type = int(nlist_entry.n_type)
            n_desc = int(nlist_entry.n_desc)

            # Only external symbols; keep undefined ones as imports.
            if not (n_type & N_EXT):
                continue

            name = name_bytes.decode("utf-8", errors="replace") if name_bytes else ""
            # Strip leading underscore (Mach-O C symbol convention)
            if name.startswith("_"):
                name = name[1:]

            if (n_type & N_TYPE) == N_UNDF:
                # Undefined external symbol → an import (what this dylib requires).
                if name:
                    meta.imported_symbols.append(name)
                continue

            is_weak = bool(n_desc & N_WEAK_DEF)
            sym_type = MachoSymbolType.WEAK if is_weak else MachoSymbolType.EXPORTED
            # Classify as data (variable) when the symbol lives in __DATA segment.
            n_sect = int(nlist_entry.n_sect)
            seg = section_segment.get(n_sect, "")
            is_data = seg == "__DATA"
            meta.exports.append(MachoExport(
                name=name, sym_type=sym_type, is_weak=is_weak, is_data=is_data,
            ))
    except Exception as exc:  # noqa: BLE001
        # SymbolTable may fail on binaries without LC_SYMTAB (stripped, .tbd stubs, etc.)
        log.debug("parse_macho_metadata: SymbolTable failed for %s: %s", dylib_path, exc)


# ---------------------------------------------------------------------------
# AArch64 AAPCS64 aggregate passing classification
# ---------------------------------------------------------------------------
#: Fundamental floating-point member types that can form an HFA (Homogeneous
#: Floating-point Aggregate) under AAPCS64 §5.9.5.
_AAPCS64_HFA_BASE_TYPES = frozenset({
    "float", "double", "long double",
    "__fp16", "_Float16", "__bf16",
})

#: AArch64 passes an aggregate in general registers only when it is <= 16 bytes;
#: larger aggregates are passed indirectly (by reference to a caller copy).
AAPCS64_AGGREGATE_REGISTER_LIMIT = 16

#: Short-vector (SIMD) member types that can form an HVA (Homogeneous
#: short-Vector Aggregate) under AAPCS64 §5.9.5 — NEON intrinsic types like
#: ``float32x4_t`` / ``int8x16_t`` / ``poly16x8_t`` (incl. the array-of-vector
#: ``...x4x2_t`` forms), plus generically-named GCC/Clang vector types.
_AAPCS64_VECTOR_RE = re.compile(r"(?:u?int|float|poly|bfloat)\d+x\d+", re.IGNORECASE)


def _is_short_vector(type_name: str) -> bool:
    """True if *type_name* looks like an AArch64 short-vector (SIMD) type.

    This is a *name-based heuristic* (NEON intrinsic spelling + generic vector
    names), not a DWARF/type-system query: it can over-match a scalar typedef
    whose name happens to contain the pattern, or miss a toolchain-specific
    vector spelling. Adequate for the AAPCS64 modeling primitive below.
    """
    lowered = type_name.lower()
    return (
        bool(_AAPCS64_VECTOR_RE.search(lowered))
        or "vector" in lowered
        or "__simd" in lowered
    )


def classify_aapcs64_aggregate(byte_size: int, member_base_types: list[str]) -> str:
    """Classify how AArch64 (AAPCS64) passes an aggregate *by value*.

    This is the calling-convention dimension that differs from the SysV
    x86-64 path and is otherwise invisible to a size-only diff. Crossing one
    of these boundaries (e.g. growing past 16 bytes, or ceasing to be an HFA/HVA)
    is a real ARM64 ABI change for by-value parameters/returns.

    NOTE: this is currently a *modeling primitive* — it is exercised by unit
    tests (see ``tests/test_macos_arm64_abi.py``) but is not yet wired into the
    diff pipeline, so it does not by itself emit findings. Wiring it into the
    value-ABI trait path (so an aggregate crossing a register/indirect or
    HFA/HVA boundary surfaces a ``value_abi_trait_changed``) is tracked under G1
    and intentionally deferred to keep this change low-risk on the x86-64 path.

    Args:
        byte_size: ``sizeof`` of the aggregate.
        member_base_types: fundamental type names of the aggregate's members
            (flattened). An HFA requires 1..4 members all of the same
            floating-point fundamental type; an HVA the same of a short-vector
            (SIMD) type.

    Returns:
        - ``"hfa<N>"`` — Homogeneous Floating-point Aggregate of N members,
          passed in N SIMD/FP registers (v0..v3).
        - ``"hva<N>"`` — Homogeneous short-Vector Aggregate of N members, also
          passed in N SIMD/FP registers regardless of total size (≤ 4×16 = 64 B).
        - ``"register"`` — aggregate <= 16 bytes, passed in up to two GP
          registers (x0/x1).
        - ``"indirect"`` — aggregate > 16 bytes, passed by reference.
    """
    members = [m for m in member_base_types if m]
    homogeneous = 1 <= len(members) <= 4 and len(set(members)) == 1
    if homogeneous and members[0] in _AAPCS64_HFA_BASE_TYPES:
        return f"hfa{len(members)}"
    # HVA must be checked BEFORE the 16-byte cutoff: an HVA is register-passed
    # even at up to 64 bytes (4 × 16-byte vectors).
    if homogeneous and _is_short_vector(members[0]):
        return f"hva{len(members)}"
    if byte_size > AAPCS64_AGGREGATE_REGISTER_LIMIT:
        return "indirect"
    return "register"
