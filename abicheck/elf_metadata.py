# Copyright 2026 Nikolay Petrov
# SPDX-License-Identifier: Apache-2.0
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

"""ELF dynamic-section and symbol-table metadata.

Uses ``pyelftools`` (pure Python, actively maintained) for robust ELF/DWARF
parsing instead of text-scraping ``readelf`` output.

See docs/adr/001-technology-stack.md for rationale.
"""
from __future__ import annotations

import logging
import os
import re
import stat
import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import IO

from elftools.common.exceptions import ELFError
from elftools.elf.dynamic import DynamicSection, DynamicTag
from elftools.elf.elffile import ELFFile
from elftools.elf.gnuversions import (
    GNUVerDefSection,
    GNUVerNeedSection,
    GNUVerSymSection,
)
from elftools.elf.sections import SymbolTableSection

log = logging.getLogger(__name__)


class SymbolBinding(str, Enum):
    GLOBAL = "global"
    WEAK = "weak"
    LOCAL = "local"
    UNIQUE = "unique"  # STB_GNU_UNIQUE — process-wide uniqueness, inhibits dlclose
    OTHER = "other"


class SymbolType(str, Enum):
    FUNC = "func"
    OBJECT = "object"
    TLS = "tls"
    IFUNC = "ifunc"   # STT_GNU_IFUNC
    COMMON = "common"  # STT_COMMON
    NOTYPE = "notype"
    OTHER = "other"


@dataclass
class ElfSymbol:
    name: str
    binding: SymbolBinding = SymbolBinding.GLOBAL
    sym_type: SymbolType = SymbolType.FUNC
    size: int = 0
    version: str = ""       # version tag from .gnu.version_d/.gnu.version_r
    is_default: bool = True
    visibility: str = "default"  # default / hidden / protected / internal
    origin_lib: str | None = None  # Detected source library, None = native
    # Power-of-two address alignment derived from st_value (capped at the page
    # size, 4096). Segments load page-aligned, so alignment up to a page is
    # preserved at runtime. 0 = unknown (st_value 0, or a legacy snapshot).
    # Used to detect exported-data alignment reductions (copy-reloc hazard).
    value_alignment: int = 0


@dataclass
class ElfImport:
    """An undefined (imported) dynamic symbol — what this DSO requires."""
    name: str
    binding: SymbolBinding = SymbolBinding.GLOBAL  # GLOBAL or WEAK
    sym_type: SymbolType = SymbolType.NOTYPE
    version: str = ""       # required version tag (from .gnu.version + .gnu.version_r)
    is_default: bool = True  # @@default vs @specific
    # Soname of the library that .gnu.version_r names as the provider of this
    # symbol's required version. GNU version labels are scoped per verneed
    # provider (not globally unique), so this disambiguates which DSO satisfies
    # the import when two providers share a label. "" when unversioned.
    version_soname: str = ""


@dataclass
class ElfMetadata:
    """ELF dynamic-section + symbol metadata for one .so.

    NOTE: Do NOT add ``frozen=True`` to this dataclass — ``@cached_property``
    (used by ``symbol_map``) requires a writable instance ``__dict__``.
    """
    soname: str = ""
    needed: list[str] = field(default_factory=list)
    rpath: str = ""
    runpath: str = ""

    # Symbol versions defined by this library (.gnu.version_d)
    versions_defined: list[str] = field(default_factory=list)
    # Symbol versions required from other libraries (.gnu.version_r)
    # dict: library_soname → list of version strings
    versions_required: dict[str, list[str]] = field(default_factory=dict)

    # Exported symbols (.dynsym, GLOBAL/WEAK, not UND, not hidden/internal)
    symbols: list[ElfSymbol] = field(default_factory=list)

    # Imported symbols (.dynsym, SHN_UNDEF, GLOBAL/WEAK)
    imports: list[ElfImport] = field(default_factory=list)

    # ELF interpreter (PT_INTERP, e.g. /lib64/ld-linux-x86-64.so.2)
    interpreter: str = ""
    # ELF data encoding from EI_DATA: "LSB" (little) / "MSB" (big).
    # "" = not captured (legacy snapshot) — detectors must skip, not compare.
    ei_data: str = ""
    # Minimum kernel version from the NT_GNU_ABI_TAG note (.note.ABI-tag),
    # e.g. "3.2.0". "" = note absent or not captured.
    min_kernel_version: str = ""
    # dlopen/dlclose-contract flags decoded from DT_FLAGS/DT_FLAGS_1:
    # subset of {"NODELETE", "NOOPEN", "ORIGIN"}. None = not captured
    # (legacy snapshot); frozenset() = captured, none set.
    dynamic_flags: frozenset[str] | None = None
    # Load/unload-time code presence (DT_INIT/DT_INIT_ARRAY and
    # DT_FINI/DT_FINI_ARRAY). None = not captured (legacy snapshot).
    has_init: bool | None = None
    has_fini: bool | None = None

    # PT_GNU_STACK: True when the ELF has an executable stack (RWE flags).
    # This is a security bad practice (disables NX protection).
    has_executable_stack: bool = False

    # ── checksec-equivalent hardening surface ────────────────────────────
    # These mirror what `checksec`/`hardening-check` report so a release that
    # silently weakens a hardening property can be diffed (see G12).
    #
    # RELRO level: "none" | "partial" | "full".
    #   partial = PT_GNU_RELRO segment present (GOT moved to a read-only page
    #             after relocation), full = partial + BIND_NOW (eager binding,
    #             so the whole GOT is read-only).
    relro: str = "none"
    # BIND_NOW eager binding (DT_BIND_NOW, DF_BIND_NOW, or DF_1_NOW).
    bind_now: bool = False
    # Position-independent executable (ET_DYN + DF_1_PIE). Shared libraries are
    # always position-independent; this flags PIE *executables* specifically.
    is_pie: bool = False
    # Stack-smashing protector: references __stack_chk_fail / __stack_chk_guard.
    has_stack_canary: bool = False
    # _FORTIFY_SOURCE: references at least one fortified libc wrapper (*_chk).
    has_fortify_source: bool = False
    # W^X violation: a loadable segment is simultaneously writable + executable.
    has_writable_executable_segment: bool = False

    # Target pointer width in bytes (4 for ELFCLASS32, 8 for ELFCLASS64).
    # Used by diff_elf_layout.py to turn `_ZTV`/`_ZTI` object sizes into vtable
    # slot counts and inheritance shapes. Defaults to 8 (the common 64-bit case)
    # so in-memory snapshots constructed in tests need not set it explicitly.
    pointer_size: int = 8

    # ── ELF identity (G23-A3) ────────────────────────────────────────────
    # Header fields that define the binary's target contract. A drift here
    # means the two inputs are different-architecture / different-ABI images.
    # ``machine`` is the pyelftools e_machine string (e.g. "EM_X86_64");
    # ``elf_class`` is 32 or 64; ``osabi`` is the EI_OSABI string
    # (e.g. "ELFOSABI_SYSV"). ``e_flags`` is the raw per-arch flag word and
    # ``abi_flags`` a decoded, human-readable subset (float ABI / EABI version)
    # for the architectures we know how to decode (ARM, RISC-V, MIPS).
    machine: str = ""
    elf_class: int = 64
    osabi: str = ""
    e_flags: int = 0
    abi_flags: frozenset[str] = field(default_factory=frozenset)

    # ── Static-TLS drift (G23-A1) ────────────────────────────────────────
    # DF_STATIC_TLS in DT_FLAGS: the library uses the static (initial/local-exec)
    # TLS model and can no longer be reliably dlopen()ed. ``has_tls_symbols`` is
    # True when the library participates in TLS at all — set from *either* a
    # dynamic STT_TLS entry (defined OR an undefined `extern __thread` import) or
    # a PT_TLS program-header segment (which also covers hidden/local __thread
    # variables that never reach .dynsym). Both the import-only and hidden-local
    # cases are just as dlopen-hostile, so the DF_STATIC_TLS suppression guard
    # must consider all of them.
    has_static_tls: bool = False
    has_tls_symbols: bool = False

    # ── GNU-property hardening (G23-A2) ──────────────────────────────────
    # Control-flow protections carried in PT_GNU_PROPERTY / .note.gnu.property.
    # A set of feature tokens drawn from {"IBT", "SHSTK", "BTI", "PAC"}. Dropping
    # a feature between versions weakens the process-wide guarantee (a single
    # non-IBT/BTI DSO disables enforcement for the whole link map).
    gnu_properties: frozenset[str] = field(default_factory=frozenset)

    # ── Linker artifact facts (binutils & glibc skew) ────────────────────
    # DT_RELR packed relative relocations (`-z pack-relative-relocs`). A
    # DT_RELR binary needs glibc ≥ 2.36 (or an equivalent loader) — glibc
    # marks the requirement with a synthetic GLIBC_ABI_DT_RELR verneed.
    has_dt_relr: bool = False
    # Symbol hash-table styles present: subset of {"sysv", "gnu"}
    # (.hash → "sysv", .gnu.hash → "gnu"; ld --hash-style). Dropping a style
    # drops loaders/tools that only support that style.
    hash_styles: frozenset[str] = field(default_factory=frozenset)

    @cached_property
    def symbol_map(self) -> dict[str, ElfSymbol]:
        """Name → ElfSymbol mapping (built once, cached on first access).

        Thread safety: benign race — both threads compute the same dict;
        the last write wins. Functionally correct for read-only use.
        """
        return {s.name: s for s in self.symbols}


# ---------------------------------------------------------------------------
# Internal constants
# ---------------------------------------------------------------------------

_BINDING_MAP: dict[str, SymbolBinding] = {
    "STB_GLOBAL": SymbolBinding.GLOBAL,
    "STB_WEAK": SymbolBinding.WEAK,
    "STB_LOCAL": SymbolBinding.LOCAL,
    # STB_GNU_UNIQUE (bind value 10, GNU OS-specific range). pyelftools reports
    # it as "STB_GNU_UNIQUE"; older versions surface the raw OS range as
    # "STB_LOOS", which on Linux ELF coincides with STB_GNU_UNIQUE.
    "STB_GNU_UNIQUE": SymbolBinding.UNIQUE,
    "STB_LOOS": SymbolBinding.UNIQUE,
}

_TYPE_MAP: dict[str, SymbolType] = {
    "STT_FUNC": SymbolType.FUNC,
    "STT_OBJECT": SymbolType.OBJECT,
    "STT_TLS": SymbolType.TLS,
    "STT_GNU_IFUNC": SymbolType.IFUNC,
    # pyelftools < 0.33 reports STT_GNU_IFUNC (type=10, OS-specific range) as STT_LOOS.
    # On Linux ELF, STT_LOOS == STT_GNU_IFUNC, so we map it to IFUNC.
    "STT_LOOS": SymbolType.IFUNC,
    "STT_COMMON": SymbolType.COMMON,
    "STT_NOTYPE": SymbolType.NOTYPE,
}

_HIDDEN_VISIBILITIES = frozenset({"STV_HIDDEN", "STV_INTERNAL"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_elf_metadata(so_path: Path) -> ElfMetadata:
    """Extract ELF dynamic + symbol metadata from *so_path* using pyelftools.

    Returns an empty ``ElfMetadata`` on any parse error (logged as WARNING).
    Uses fstat() after open() to prevent TOCTOU symlink/FIFO attacks.
    """
    try:
        with open(so_path, "rb") as f:
            # Verify it's a regular file *after* open to avoid TOCTOU race.
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("parse_elf_metadata: not a regular file: %s", so_path)
                return ElfMetadata()
            return _parse(f, so_path)
    except (ELFError, OSError, ValueError) as exc:
        log.warning("parse_elf_metadata: failed to open/parse %s: %s", so_path, exc)
        return ElfMetadata()


# ---------------------------------------------------------------------------
# Internal parsing
# ---------------------------------------------------------------------------

def _parse(f: IO[bytes], so_path: Path) -> ElfMetadata:
    meta = ElfMetadata()
    elf = ELFFile(f)

    meta.pointer_size = _read_pointer_size(elf, so_path)
    _read_identity(elf, meta, so_path)
    _parse_gnu_property(elf, meta, so_path)
    _parse_abi_tag(elf, meta, so_path)
    has_relro_segment, is_et_dyn = _parse_segments(elf, meta, so_path)

    ver_sym_section, dynsym_section, ver_index_map = _parse_all_sections(elf, meta, so_path)

    # Correlate per-symbol version entries using sections captured above.
    _correlate_symbol_versions(ver_sym_section, dynsym_section, meta, ver_index_map, so_path)

    _postprocess_metadata(meta, ver_index_map, ver_sym_section, dynsym_section, so_path)

    # Finalize derived hardening properties now that segments, dynamic flags,
    # and the symbol table have all been parsed.
    _finalize_hardening(meta, has_relro_segment=has_relro_segment, is_et_dyn=is_et_dyn)

    return meta


def _read_pointer_size(elf: ELFFile, so_path: Path) -> int:
    """Return pointer width in bytes from ELF class (32-bit → 4, 64-bit → 8).

    Used to decode vtable/typeinfo object sizes in diff_elf_layout.py.
    Falls back to 8 (64-bit default) on parse error.
    """
    try:
        return 4 if elf.elfclass == 32 else 8
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_elf_metadata: failed to read ELF class from %s: %s", so_path, exc)
        return 8


# ── ELF identity / per-arch ABI-flag decoding (G23-A3) ──────────────────────
# Only the bits that select an *incompatible* calling convention are decoded;
# unknown architectures fall back to reporting the raw e_flags value.
_EF_ARM_ABI_FLOAT_HARD = 0x00000400
_EF_ARM_ABI_FLOAT_SOFT = 0x00000200
_EF_ARM_EABI_MASK = 0xFF000000
_EF_RISCV_FLOAT_ABI_MASK = 0x0006  # 0=soft, 2=single, 4=double, 6=quad
# EF_RISCV_RVE (0x8) reduces the integer register file 32→16 and changes the
# calling convention, so it IS ABI-selecting. EF_RISCV_RVC (0x1, compressed
# instructions) is an ISA-encoding choice with no calling-convention effect, so
# it is deliberately NOT decoded — toggling it must not report an ABI break.
_EF_RISCV_RVE = 0x0008
_EF_MIPS_ABI_MASK = 0x0000F000
_RISCV_FLOAT_ABI_NAMES = {0x0: "float-soft", 0x2: "float-single", 0x4: "float-double", 0x6: "float-quad"}


def _decode_abi_flags(machine: str, e_flags: int) -> frozenset[str]:
    """Decode the ABI-selecting bits of ``e_flags`` for architectures we know.

    Returns a set of human-readable tokens whose *change* between two versions
    signals a calling-convention-incompatible rebuild (float ABI, EABI version).
    Unknown architectures return an empty set; the raw ``e_flags`` value is
    diffed separately so drift is still caught, just without a decoded label.
    """
    tokens: set[str] = set()
    if machine in ("EM_ARM",):
        if e_flags & _EF_ARM_ABI_FLOAT_HARD:
            tokens.add("float-hard")
        elif e_flags & _EF_ARM_ABI_FLOAT_SOFT:
            tokens.add("float-soft")
        eabi = (e_flags & _EF_ARM_EABI_MASK) >> 24
        if eabi:
            tokens.add(f"eabi{eabi}")
    elif machine in ("EM_RISCV",):
        tokens.add(_RISCV_FLOAT_ABI_NAMES.get(e_flags & _EF_RISCV_FLOAT_ABI_MASK, "float-unknown"))
        if e_flags & _EF_RISCV_RVE:
            tokens.add("rve")
    elif machine in ("EM_MIPS",):
        abi = e_flags & _EF_MIPS_ABI_MASK
        if abi:
            tokens.add(f"mips-abi-{abi:#x}")
    return frozenset(tokens)


def _read_identity(elf: ELFFile, meta: ElfMetadata, so_path: Path) -> None:
    """Capture the ELF header fields that define the target binary contract."""
    try:
        meta.machine = elf["e_machine"]
        meta.elf_class = 32 if elf.elfclass == 32 else 64
        meta.osabi = elf["e_ident"]["EI_OSABI"]
        meta.e_flags = int(elf["e_flags"])
        meta.abi_flags = _decode_abi_flags(meta.machine, meta.e_flags)
        meta.ei_data = "LSB" if elf.little_endian else "MSB"
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_elf_metadata: failed to read ELF identity from %s: %s", so_path, exc)


# ── GNU-property control-flow-protection decoding (G23-A2) ──────────────────
# pyelftools reports the note type as the *string* "NT_GNU_PROPERTY_TYPE_0"
# (its known-type name), not the raw numeric 5 — accept both forms.
_NT_GNU_PROPERTY_TYPE_0 = 5
_NT_GNU_PROPERTY_TYPE_0_NAMES = frozenset({5, "NT_GNU_PROPERTY_TYPE_0"})
_GNU_PROPERTY_X86_FEATURE_1_AND = 0xC0000002
_GNU_PROPERTY_X86_FEATURE_1_IBT = 0x1
_GNU_PROPERTY_X86_FEATURE_1_SHSTK = 0x2
_GNU_PROPERTY_AARCH64_FEATURE_1_AND = 0xC0000000
_GNU_PROPERTY_AARCH64_FEATURE_1_BTI = 0x1
_GNU_PROPERTY_AARCH64_FEATURE_1_PAC = 0x2
# Required micro-architecture level (glibc-hwcaps builds, -march=x86-64-vN).
# A raised level means older CPUs can no longer run the library at all.
_GNU_PROPERTY_X86_ISA_1_NEEDED = 0xC0008002
_X86_ISA_1_LEVEL_TOKENS: tuple[tuple[int, str], ...] = (
    (0x1, "x86-64-baseline"),
    (0x2, "x86-64-v2"),
    (0x4, "x86-64-v3"),
    (0x8, "x86-64-v4"),
)


def _decode_gnu_property_desc(desc: bytes, little_endian: bool, align: int = 8) -> frozenset[str]:
    """Parse a NT_GNU_PROPERTY_TYPE_0 note description into feature tokens.

    The description is a sequence of properties, each laid out as
    ``pr_type (u32) | pr_datasz (u32) | pr_data[pr_datasz] | pad``. Each
    property is padded up to *align* bytes — 8 for ELFCLASS64, **4** for
    ELFCLASS32 — so a wrong alignment skips or misreads later properties. Only
    the x86 and AArch64 control-flow-protection AND-features are decoded.
    """
    endian = "<" if little_endian else ">"
    tokens: set[str] = set()
    off = 0
    n = len(desc)
    while off + 8 <= n:
        pr_type, pr_datasz = struct.unpack_from(endian + "II", desc, off)
        off += 8
        if off + pr_datasz > n:
            break
        data = desc[off : off + pr_datasz]
        if pr_type == _GNU_PROPERTY_X86_FEATURE_1_AND and pr_datasz >= 4:
            (bits,) = struct.unpack_from(endian + "I", data, 0)
            if bits & _GNU_PROPERTY_X86_FEATURE_1_IBT:
                tokens.add("IBT")
            if bits & _GNU_PROPERTY_X86_FEATURE_1_SHSTK:
                tokens.add("SHSTK")
        elif pr_type == _GNU_PROPERTY_AARCH64_FEATURE_1_AND and pr_datasz >= 4:
            (bits,) = struct.unpack_from(endian + "I", data, 0)
            if bits & _GNU_PROPERTY_AARCH64_FEATURE_1_BTI:
                tokens.add("BTI")
            if bits & _GNU_PROPERTY_AARCH64_FEATURE_1_PAC:
                tokens.add("PAC")
        elif pr_type == _GNU_PROPERTY_X86_ISA_1_NEEDED and pr_datasz >= 4:
            (bits,) = struct.unpack_from(endian + "I", data, 0)
            for bit, token in _X86_ISA_1_LEVEL_TOKENS:
                if bits & bit:
                    tokens.add(token)
        # Advance past pr_data, padded up to the class alignment.
        off += (pr_datasz + align - 1) & ~(align - 1)
    return frozenset(tokens)


# NT_GNU_ABI_TAG (n_type 1, name "GNU"): 4 words — OS id (0 = Linux) followed
# by the minimum required kernel version (major, minor, subminor).
_NT_GNU_ABI_TAG_NAMES = frozenset({1, "NT_GNU_ABI_TAG"})
_ELF_OSABI_TAG_LINUX = 0


def _decode_abi_tag_desc(desc: bytes, little_endian: bool) -> str:
    """Decode an NT_GNU_ABI_TAG description into a kernel-floor string.

    Returns ``"major.minor.subminor"`` for a Linux tag, ``""`` for a non-Linux
    OS id or a malformed description.
    """
    if len(desc) < 16:
        return ""
    endian = "<" if little_endian else ">"
    os_id, major, minor, subminor = struct.unpack_from(endian + "IIII", desc, 0)
    if os_id != _ELF_OSABI_TAG_LINUX:
        return ""
    return f"{major}.{minor}.{subminor}"


def _parse_abi_tag(elf: ELFFile, meta: ElfMetadata, so_path: Path) -> None:
    """Read the minimum-kernel floor from the .note.ABI-tag section."""
    try:
        section = elf.get_section_by_name(".note.ABI-tag")
        if section is None or not hasattr(section, "iter_notes"):
            return
        for note in section.iter_notes():
            if note.get("n_type") not in _NT_GNU_ABI_TAG_NAMES:
                continue
            if note.get("n_name") not in (None, "GNU"):
                continue
            desc = note.get("n_descdata") or note.get("n_desc")
            if isinstance(desc, str):
                desc = desc.encode("latin-1", "replace")
            if isinstance(desc, (bytes, bytearray)):
                floor = _decode_abi_tag_desc(bytes(desc), elf.little_endian)
                if floor:
                    meta.min_kernel_version = floor
                    return
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_elf_metadata: failed to read .note.ABI-tag from %s: %s", so_path, exc)


def _parse_gnu_property(elf: ELFFile, meta: ElfMetadata, so_path: Path) -> None:
    """Read control-flow-protection features from GNU-property notes.

    Prefers the ``.note.gnu.property`` section, but falls back to the loadable
    ``PT_GNU_PROPERTY`` program segment when section headers have been stripped
    (common for production artifacts) — otherwise CET/BTI/PAC drift would be
    invisible on exactly those binaries.
    """
    try:
        # GNU property entries are padded to the ELF class word size: 8 bytes
        # for ELFCLASS64, 4 bytes for ELFCLASS32.
        align = 4 if elf.elfclass == 32 else 8
        features: set[str] = set()
        for desc in _iter_gnu_property_descs(elf):
            features |= _decode_gnu_property_desc(desc, elf.little_endian, align)
        meta.gnu_properties = frozenset(features)
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_elf_metadata: failed to read GNU-property notes from %s: %s", so_path, exc)


def _iter_gnu_property_descs(elf: ELFFile) -> Iterator[bytes]:
    """Yield NT_GNU_PROPERTY_TYPE_0 description blobs from section or segment."""
    section = elf.get_section_by_name(".note.gnu.property")
    if section is not None and hasattr(section, "iter_notes"):
        found = False
        for note in section.iter_notes():
            found = True
            if note.get("n_type") not in _NT_GNU_PROPERTY_TYPE_0_NAMES:
                continue
            desc = note.get("n_descdata") or note.get("n_desc")
            if isinstance(desc, str):
                desc = desc.encode("latin-1", "replace")
            if isinstance(desc, (bytes, bytearray)):
                yield bytes(desc)
        if found:
            return
    # Fallback: section absent / empty (stripped) — parse the PT_GNU_PROPERTY
    # program segment's raw note bytes directly.
    try:
        segments = list(elf.iter_segments())
    except Exception:  # noqa: BLE001
        return
    for seg in segments:
        if getattr(seg.header, "p_type", None) != "PT_GNU_PROPERTY":
            continue
        yield from _parse_raw_notes(seg.data(), elf.little_endian)


def _parse_raw_notes(data: bytes, little_endian: bool) -> Iterator[bytes]:
    """Parse ELF notes from raw bytes, yielding GNU-property description blobs.

    The note wrapper (namesz | descsz | n_type | name | desc) uses 4-byte
    padding regardless of ELF class; only the property array *inside* the
    description follows the class alignment (handled by the desc decoder).
    """
    endian = "<" if little_endian else ">"
    off = 0
    n = len(data)
    while off + 12 <= n:
        namesz, descsz, n_type = struct.unpack_from(endian + "III", data, off)
        off += 12
        name = data[off : off + namesz]
        off += (namesz + 3) & ~3
        desc = data[off : off + descsz]
        off += (descsz + 3) & ~3
        if n_type in _NT_GNU_PROPERTY_TYPE_0_NAMES and name.rstrip(b"\x00") == b"GNU":
            yield desc


_PF_X = 0x1
_PF_W = 0x2


def _parse_segments(
    elf: ELFFile, meta: ElfMetadata, so_path: Path
) -> tuple[bool, bool]:
    """Iterate program headers; populate interpreter and segment-level hardening fields.

    Returns ``(has_relro_segment, is_et_dyn)``.

    * ``has_relro_segment`` — a PT_GNU_RELRO segment was found.
    * ``is_et_dyn``         — the ELF type is ET_DYN (needed to gate PIE detection).
    """
    has_relro_segment = False
    try:
        for seg in elf.iter_segments():
            _process_segment(seg, meta, so_path)
            if seg.header.p_type == "PT_GNU_RELRO":
                has_relro_segment = True
            elif seg.header.p_type == "PT_TLS":
                # A PT_TLS segment means the object defines TLS storage of its
                # own — including hidden/local `__thread` variables that never
                # appear in .dynsym. Used (alongside dynamic STT_TLS symbols) to
                # gate the DF_STATIC_TLS finding so hidden static TLS is not
                # suppressed (G23-A1).
                meta.has_tls_symbols = True
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_elf_metadata: failed to read program headers from %s: %s", so_path, exc)

    # PIE = position-independent *executable* (ET_DYN with the DF_1_PIE flag).
    # _parse_dynamic sets meta.is_pie tentatively from DF_1_PIE; gate it on
    # ET_DYN here so a non-PIE object never claims PIE.
    is_et_dyn = False
    try:
        is_et_dyn = elf.header.e_type == "ET_DYN"
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_elf_metadata: failed to read e_type from %s: %s", so_path, exc)

    return has_relro_segment, is_et_dyn


def _process_segment(seg: object, meta: ElfMetadata, so_path: Path) -> None:
    """Apply a single ELF program-header segment to *meta*."""
    p_type = seg.header.p_type
    if p_type == "PT_INTERP":
        # PT_INTERP contains a null-terminated path string.
        meta.interpreter = seg.get_interp_name()
    elif p_type == "PT_GNU_STACK":
        # PF_X = executable. Executable stack is a security risk.
        if seg.header.p_flags & _PF_X:
            meta.has_executable_stack = True
    elif p_type == "PT_LOAD":
        # A loadable segment that is both writable and executable
        # violates W^X (memory should never be both at once).
        if (seg.header.p_flags & _PF_W) and (seg.header.p_flags & _PF_X):
            meta.has_writable_executable_segment = True


# Type alias for version index maps shared across section helpers.
_VerIndexMap = dict[int, tuple[str, str, bool]]


def _parse_all_sections(
    elf: ELFFile, meta: ElfMetadata, so_path: Path
) -> tuple[GNUVerSymSection | None, SymbolTableSection | None, _VerIndexMap]:
    """Iterate all ELF sections; parse each into *meta* and return captured refs.

    Returns ``(ver_sym_section, dynsym_section, ver_index_map)`` for the
    subsequent version-correlation pass.

    * ``ver_sym_section`` — the ``.gnu.version`` section (one entry per .dynsym symbol).
    * ``dynsym_section``  — the ``.dynsym`` section (exported/imported symbols).
    * ``ver_index_map``   — merged verdef + verneed index maps; verdef takes priority.

    Relocatable objects (``.o``) carry no ``.dynsym`` — their symbol surface lives
    in ``.symtab``.  When ``.dynsym`` is absent and ``.symtab`` is present the
    fallback parsing fills ``meta.symbols``/``meta.imports`` via the same
    ``_parse_dynsym`` path used for shared libraries.
    """
    # Build separate version-index maps from .gnu.version_d and .gnu.version_r.
    # Verdef and verneed indices are normally non-overlapping, but separating
    # them prevents mis-attribution if a malformed ELF reuses an index.
    verdef_index_map: _VerIndexMap = {}   # idx → ("", ver, True)
    verneed_index_map: _VerIndexMap = {}  # idx → (lib, ver, False)

    ver_sym_section: GNUVerSymSection | None = None
    dynsym_section: SymbolTableSection | None = None
    symtab_section: SymbolTableSection | None = None

    for section in elf.iter_sections():
        try:
            dynsym_section, symtab_section, ver_sym_section = _process_section(
                section, meta, verdef_index_map, verneed_index_map,
                dynsym_section, symtab_section, ver_sym_section,
            )
        except Exception as exc:  # noqa: BLE001
            # Partial-success: log malformed section, keep results from other sections.
            log.warning("parse_elf_metadata: skipping malformed section %r in %s: %s",
                        section.name, so_path, exc)

    # Relocatable objects (ET_REL `.o`, e.g. a probe-built object) carry no
    # `.dynsym` — their symbol surface lives in `.symtab`. Fall back to it so the
    # defined GLOBAL/WEAK symbols of a `.o` are captured (the same classification
    # `_parse_dynsym` applies to a `.dynsym`). A normal shared library always has
    # `.dynsym`, so this never alters DSO parsing.
    if dynsym_section is None and symtab_section is not None:
        try:
            _parse_dynsym(symtab_section, meta)
        except Exception as exc:  # noqa: BLE001
            log.warning("parse_elf_metadata: skipping malformed .symtab in %s: %s",
                        so_path, exc)

    # Merge: verdef entries take priority over verneed on index collision.
    ver_index_map: _VerIndexMap = {**verneed_index_map, **verdef_index_map}

    return ver_sym_section, dynsym_section, ver_index_map


def _process_section(
    section: object,
    meta: ElfMetadata,
    verdef_index_map: _VerIndexMap,
    verneed_index_map: _VerIndexMap,
    dynsym_section: SymbolTableSection | None,
    symtab_section: SymbolTableSection | None,
    ver_sym_section: GNUVerSymSection | None,
) -> tuple[SymbolTableSection | None, SymbolTableSection | None, GNUVerSymSection | None]:
    """Dispatch one ELF section to the appropriate parser; return updated refs."""
    if isinstance(section, DynamicSection):
        _parse_dynamic(section, meta)
    elif isinstance(section, GNUVerDefSection):
        _parse_version_def(section, meta)
        _build_verdef_index(section, verdef_index_map)
    elif isinstance(section, GNUVerNeedSection):
        _parse_version_need(section, meta)
        _build_verneed_index(section, verneed_index_map)
    elif isinstance(section, GNUVerSymSection):
        ver_sym_section = section
    elif isinstance(section, SymbolTableSection) and section.name == ".dynsym":
        _parse_dynsym(section, meta)
        dynsym_section = section
    elif isinstance(section, SymbolTableSection) and section.name == ".symtab":
        # Captured but not parsed yet — only used as a fallback for
        # relocatable objects (.o) that have no .dynsym (see below).
        symtab_section = section
    else:
        name = getattr(section, "name", "")
        if name == ".hash":
            meta.hash_styles = meta.hash_styles | {"sysv"}
        elif name == ".gnu.hash":
            meta.hash_styles = meta.hash_styles | {"gnu"}
        elif name == ".relr.dyn":
            # Section-level fallback for DT_RELR (the dynamic tag is the
            # primary signal; a stripped/static-pie image may only keep the
            # section).
            meta.has_dt_relr = True
    return dynsym_section, symtab_section, ver_sym_section


# Fortified libc wrappers are named ``__<func>_chk`` (e.g. __memcpy_chk,
# __printf_chk); their presence as undefined references means the object was
# built with -D_FORTIFY_SOURCE.
_CANARY_SYMS = frozenset({"__stack_chk_fail", "__stack_chk_guard"})
_FORTIFY_RE = re.compile(r"^__\w+_chk$")


def _finalize_hardening(
    meta: ElfMetadata, *, has_relro_segment: bool, is_et_dyn: bool
) -> None:
    """Derive RELRO level, PIE, stack-canary, and FORTIFY from parsed data."""
    # RELRO: a PT_GNU_RELRO segment gives partial RELRO; combined with eager
    # binding (BIND_NOW) the whole GOT is read-only → full RELRO.
    if has_relro_segment:
        meta.relro = "full" if meta.bind_now else "partial"
    else:
        meta.relro = "none"

    # PIE only applies to ET_DYN images flagged DF_1_PIE.
    meta.is_pie = meta.is_pie and is_et_dyn

    # Stack canary / FORTIFY are observed via referenced libc symbols. Check
    # both the imported (undefined) set and any defined symbols.
    names = [s.name for s in meta.imports] + [s.name for s in meta.symbols]
    for name in names:
        if name in _CANARY_SYMS:
            meta.has_stack_canary = True
        elif _FORTIFY_RE.match(name):
            meta.has_fortify_source = True


def _postprocess_metadata(
    meta: ElfMetadata,
    ver_index_map: dict[int, tuple[str, str, bool]],
    ver_sym_section: GNUVerSymSection | None,
    dynsym_section: SymbolTableSection | None,
    so_path: Path,
) -> None:
    """Post-loop processing: filter version-def aux symbols and fix origin hints."""
    # Filter out version-definition auxiliary symbols.
    # GNU ld emits these as OBJECT/size=0 in .dynsym; lld/gold may use NOTYPE.
    # Both are ELF artefacts of --version-script, not real exported functions.
    _ver_def_names: set[str] = set(meta.versions_defined)
    if _ver_def_names:
        meta.symbols = [
            sym for sym in meta.symbols
            if not (
                sym.name in _ver_def_names
                and sym.size == 0
                and sym.sym_type in (SymbolType.OBJECT, SymbolType.NOTYPE)
            )
        ]

    # Post-parse fixup: re-run origin detection now that meta.needed is fully
    # populated.  .dynsym is often parsed before .dynamic, so the initial
    # _guess_symbol_origin call in _parse_dynsym always sees an empty needed list.
    # The fixup also corrects symbols that were mis-attributed to the wrong
    # default library (e.g. libstdc++.so.6 vs libc++.so.1).
    _GENERIC_FALLBACKS = frozenset({  # pylint: disable=invalid-name
        "libstdc++.so.6",
        "libgcc_s.so.1",
        "libc.so.6",
    })
    for sym in meta.symbols:
        if sym.origin_lib is None or sym.origin_lib in _GENERIC_FALLBACKS:
            new_origin = _guess_symbol_origin(sym.name, meta.needed)
            if new_origin is not None:
                sym.origin_lib = new_origin


# Dynamic-flag bit constants (elf.h).
_DT_RELR = 36             # DT_RELR (packed relative relocations)
_DF_ORIGIN = 0x1          # DT_FLAGS
_DF_BIND_NOW = 0x8        # DT_FLAGS
_DF_STATIC_TLS = 0x10     # DT_FLAGS
_DF_1_NOW = 0x1           # DT_FLAGS_1
_DF_1_NODELETE = 0x8      # DT_FLAGS_1
_DF_1_NOOPEN = 0x10       # DT_FLAGS_1
_DF_1_ORIGIN = 0x80       # DT_FLAGS_1
_DF_1_PIE = 0x08000000    # DT_FLAGS_1

# Dynamic tags that mark load/unload-time code. The *SZ tags are checked too:
# lld emits DT_INIT_ARRAY even when empty, so a zero size must not count.
_INIT_TAGS = frozenset({"DT_INIT", "DT_PREINIT_ARRAY"})
_FINI_TAGS = frozenset({"DT_FINI"})

# All init/fini-related dynamic tags, deferred to _apply_init_fini_tags.
_INIT_FINI_TAGS = frozenset(
    _INIT_TAGS
    | _FINI_TAGS
    | {"DT_INIT_ARRAY", "DT_FINI_ARRAY", "DT_INIT_ARRAYSZ", "DT_FINI_ARRAYSZ"}
)


def _apply_simple_dynamic_tag(
    d_tag: object, tag: DynamicTag, meta: ElfMetadata
) -> bool:
    """Handle a self-contained dynamic tag; return True if the tag was consumed."""
    if d_tag == "DT_SONAME":
        meta.soname = tag.soname
    elif d_tag == "DT_NEEDED":
        meta.needed.append(tag.needed)
    elif d_tag == "DT_RPATH":
        meta.rpath = tag.rpath
    elif d_tag == "DT_RUNPATH":
        meta.runpath = tag.runpath
    elif d_tag == "DT_BIND_NOW":
        meta.bind_now = True
    elif d_tag in ("DT_RELR", _DT_RELR):
        # Packed relative relocations. pyelftools spells known tags as
        # strings; an older release may pass the raw numeric through.
        meta.has_dt_relr = True
    else:
        return False
    return True


def _apply_dt_flags(d_val: int, meta: ElfMetadata, dyn_flags: set[str]) -> None:
    """Apply DT_FLAGS bits (DF_*) to the metadata and the collected flag set."""
    if d_val & _DF_BIND_NOW:
        meta.bind_now = True
    if d_val & _DF_STATIC_TLS:
        meta.has_static_tls = True
    if d_val & _DF_ORIGIN:
        dyn_flags.add("ORIGIN")


def _apply_dt_flags_1(d_val: int, meta: ElfMetadata, dyn_flags: set[str]) -> None:
    """Apply DT_FLAGS_1 bits (DF_1_*) to the metadata and the collected flag set."""
    if d_val & _DF_1_NOW:
        meta.bind_now = True
    if d_val & _DF_1_PIE:
        # Tentative; gated on ET_DYN by the caller.
        meta.is_pie = True
    if d_val & _DF_1_NODELETE:
        dyn_flags.add("NODELETE")
    if d_val & _DF_1_NOOPEN:
        dyn_flags.add("NOOPEN")
    if d_val & _DF_1_ORIGIN:
        dyn_flags.add("ORIGIN")


def _array_tag_is_populated(present: bool, size: int | None) -> bool:
    """True when a DT_*_ARRAY tag exists and its *SZ tag is absent or non-zero."""
    return present and (size is None or size > 0)


def _apply_init_fini_tags(tags: list[DynamicTag], meta: ElfMetadata) -> None:
    """Set meta.has_init/has_fini from the collected DT_INIT*/DT_FINI* tags."""
    init_array_sz: int | None = None  # None = no DT_INIT_ARRAYSZ tag seen
    fini_array_sz: int | None = None
    has_init_array = has_fini_array = False
    for tag in tags:
        d_tag = tag.entry.d_tag
        if d_tag in _INIT_TAGS:
            meta.has_init = True
        elif d_tag in _FINI_TAGS:
            meta.has_fini = True
        elif d_tag == "DT_INIT_ARRAY":
            has_init_array = True
        elif d_tag == "DT_FINI_ARRAY":
            has_fini_array = True
        elif d_tag == "DT_INIT_ARRAYSZ":
            init_array_sz = int(tag.entry.d_val)
        elif d_tag == "DT_FINI_ARRAYSZ":
            fini_array_sz = int(tag.entry.d_val)
    if _array_tag_is_populated(has_init_array, init_array_sz):
        meta.has_init = True
    if _array_tag_is_populated(has_fini_array, fini_array_sz):
        meta.has_fini = True


def _parse_dynamic(section: DynamicSection, meta: ElfMetadata) -> None:
    # A dynamic section exists — the loader-contract fields below are now
    # *captured* (tri-state None → concrete), so detectors may compare them.
    dyn_flags: set[str] = set(meta.dynamic_flags or ())
    meta.has_init = bool(meta.has_init)
    meta.has_fini = bool(meta.has_fini)
    init_fini_tags: list[DynamicTag] = []
    for tag in section.iter_tags():
        d_tag = tag.entry.d_tag
        if _apply_simple_dynamic_tag(d_tag, tag, meta):
            continue
        if d_tag in _INIT_FINI_TAGS:
            init_fini_tags.append(tag)
        elif d_tag == "DT_FLAGS":
            _apply_dt_flags(tag.entry.d_val, meta, dyn_flags)
        elif d_tag == "DT_FLAGS_1":
            _apply_dt_flags_1(tag.entry.d_val, meta, dyn_flags)
    _apply_init_fini_tags(init_fini_tags, meta)
    meta.dynamic_flags = frozenset(dyn_flags)


def _parse_version_def(section: GNUVerDefSection, meta: ElfMetadata) -> None:
    # ELF version definition section (.gnu.version_d).
    # The first entry has VER_FLG_BASE (flags==1) and names the SONAME -- skip it.
    # Only real named version nodes (e.g. LIBFOO_1.0) should appear in versions_defined.
    VER_FLG_BASE = 0x1  # pylint: disable=invalid-name
    for verdef, verdaux_iter in section.iter_versions():
        is_base = bool(verdef.entry.vd_flags & VER_FLG_BASE)
        for verdaux in verdaux_iter:
            name = verdaux.name
            if name and not is_base and name not in meta.versions_defined:
                meta.versions_defined.append(name)


def _parse_version_need(section: GNUVerNeedSection, meta: ElfMetadata) -> None:
    for verneed, vernaux_iter in section.iter_versions():
        lib = verneed.name
        # pyelftools' .name fields are str | None; skip entries with no
        # library name rather than indexing the dict with None.
        if not lib:
            continue
        if lib not in meta.versions_required:
            meta.versions_required[lib] = []
        for vernaux in vernaux_iter:
            ver = vernaux.name
            if ver and ver not in meta.versions_required[lib]:
                meta.versions_required[lib].append(ver)


def _find_libcxx(needed_libs: list[str]) -> str | None:
    """Find a libc++ (not libstdc++) library in the needed list."""
    for lib in needed_libs:
        if "c++" in lib and "stdc++" not in lib:
            return lib
    return None


def _find_cxx_stdlib(needed_libs: list[str]) -> str | None:
    """Find any C++ standard library (libstdc++ or libc++) in the needed list."""
    for lib in needed_libs:
        if "stdc++" in lib or "c++" in lib:
            return lib
    return None


def _find_fundamental_cxx_rtti_runtime(needed_libs: list[str]) -> str | None:
    """Find the C++ runtime that owns fundamental RTTI, excluding libc++abi."""
    for lib in needed_libs:
        if "stdc++" in os.path.basename(lib):
            return lib
    for lib in needed_libs:
        if os.path.basename(lib).startswith("libc++."):
            return lib
    return None


def _is_libmvec_vector_symbol(name: str) -> bool:
    """Return true for glibc libmvec vector ABI symbols, not C++ guard vars."""
    if not name.startswith("_ZGV"):
        return False
    if name.startswith("_ZGVZ"):
        return False
    return len(name) > 4 and name[4] in {"b", "c", "d", "e", "n", "s"}


# Lookup table: (prefix_tuple, finder_fn_or_None, default_if_no_finder_match)
# When finder_fn is None, default is returned unconditionally.
_FinderFn = Callable[[list[str]], str | None]
_ORIGIN_PREFIX_TABLE: list[tuple[tuple[str, ...], _FinderFn | None, str]] = [
    # libc++ inline namespace __1 — must be checked BEFORE generic _ZNSt
    (("_ZNSt3__1", "_ZNKSt3__1"), _find_libcxx, "libc++.so.1"),
    # C++ stdlib symbols (libstdc++ / libc++)
    (
        (
            "_ZNSt",
            "_ZNKSt",
            "_ZSt",
            "_ZTISt",
            "_ZTSSt",
            "_ZTVSt",
            "_ZTIS",
            "_ZTSS",
            "_ZTVS",
            "_ZTINSt",
            "_ZTSNSt",
            "_ZTVNSt",
            "_ZTIN9__gnu_cxx",
            "_ZTSN9__gnu_cxx",
            "_ZTVN9__gnu_cxx",
            "_ZTIN10__cxxabiv",
            "_ZTSN10__cxxabiv",
            "_ZTVN10__cxxabiv",
        ),
        _find_cxx_stdlib,
        "libstdc++.so.6",
    ),
    # C++ operator new / delete (Itanium ABI)
    (("_Znwm", "_Znwj", "_Znam", "_Znaj", "_ZdlPv", "_ZdaPv", "_ZnwmSt", "_ZnamSt"), _find_cxx_stdlib, "libstdc++.so.6"),
    # Intel SVML
    (("__svml_",), None, "<intel-compiler-rt>"),
    # x87 math helpers (libgcc.a static)
    (("ix86_",), None, "libgcc.a (static)"),
    # libm SIMD helpers
    (("__libm_sse2_", "__libm_avx_"), None, "libm.so.6"),
    # GCC runtime support
    (("__cpu_model", "__cpu_features"), None, "libgcc_s.so.1"),
    # GNU libc internal
    (("__libc_", "__glibc_"), None, "libc.so.6"),
]


_FUNDAMENTAL_CXX_RTTI_SINGLE_CHAR_TYPE_CODES: frozenset[str] = frozenset({
    "v",   # void
    "w",   # wchar_t
    "b",   # bool
    "c",   # char
    "a",   # signed char
    "h",   # unsigned char
    "s",   # short
    "t",   # unsigned short
    "i",   # int
    "j",   # unsigned int
    "l",   # long
    "m",   # unsigned long
    "x",   # long long
    "y",   # unsigned long long
    "n",   # __int128
    "o",   # unsigned __int128
    "f",   # float
    "d",   # double
    "e",   # long double
    "g",   # __float128
    "z",   # ellipsis
})

_FUNDAMENTAL_CXX_RTTI_MULTI_CHAR_TYPE_CODES: frozenset[str] = frozenset({
    "Dn",  # std::nullptr_t
    "Du",  # char8_t
    "Di",  # char32_t
    "Ds",  # char16_t
    "Dh",  # half-precision floating point
    "Df",  # decimal32
    "Dd",  # decimal64
    "De",  # decimal128
})

_CXX_SIZED_FLOAT_TYPE_CODE_RE = re.compile(r"DF[0-9]+(?:_|[A-Za-z]+)")

_FUNDAMENTAL_CXX_RTTI_TYPE_MODIFIERS: frozenset[str] = frozenset({
    "P",  # pointer
    "R",  # lvalue reference
    "O",  # rvalue reference
    "K",  # const qualifier
    "V",  # volatile qualifier
    "r",  # restrict qualifier
})


def _is_fundamental_cxx_type_encoding(encoding: str) -> bool:
    """Return True for builtin Itanium C++ type encodings and simple wrappers."""
    while encoding:
        if encoding in _FUNDAMENTAL_CXX_RTTI_SINGLE_CHAR_TYPE_CODES:
            return True
        if encoding in _FUNDAMENTAL_CXX_RTTI_MULTI_CHAR_TYPE_CODES:
            return True
        if _CXX_SIZED_FLOAT_TYPE_CODE_RE.fullmatch(encoding):
            return True
        if encoding[0] not in _FUNDAMENTAL_CXX_RTTI_TYPE_MODIFIERS:
            return False
        encoding = encoding[1:]
    return False


def _is_fundamental_cxx_rtti_symbol(name: str) -> bool:
    """Return True for libstdc++ RTTI/typeinfo-name symbols for builtin types."""
    if not (name.startswith("_ZTI") or name.startswith("_ZTS")):
        return False
    return _is_fundamental_cxx_type_encoding(name[4:])


def _guess_symbol_origin(name: str, needed_libs: list[str]) -> str | None:
    """Guess which dependency library a symbol likely originates from.

    Analyses the symbol's mangled name prefix to determine whether it is likely
    exported by a well-known runtime dependency (libstdc++, libgcc, libc) rather
    than natively defined by the library being inspected.

    Returns a library name hint (e.g. ``'libstdc++.so.6'``) or ``None`` if the
    symbol appears to be native to this library.

    This is a heuristic — false positives are possible for symbols that happen to
    share a prefix with standard-library symbols but are defined by the library
    itself.  The result is used to annotate the ``origin_lib`` field of
    :class:`ElfSymbol`; it is informational and never suppresses real changes.
    """
    if _is_fundamental_cxx_rtti_symbol(name):
        return _find_fundamental_cxx_rtti_runtime(needed_libs) or "libstdc++.so.6"

    if _is_libmvec_vector_symbol(name):
        return "libmvec.so.1"

    for prefixes, finder_fn, default in _ORIGIN_PREFIX_TABLE:
        if name.startswith(prefixes):
            if finder_fn is not None:
                found = finder_fn(needed_libs)
                if found is not None:
                    return found
            return default

    return None  # likely native to this library


# Alignment derived from a symbol address is only meaningful up to the page
# size — PT_LOAD segments map page-aligned, so intra-page alignment survives
# relocation while anything larger is a load-address accident.
_PAGE_ALIGNMENT_CAP = 4096


def _value_alignment(st_value: int) -> int:
    """Power-of-two alignment of *st_value*, capped at the page size.

    0 means unknown (st_value of 0 carries no alignment information).
    """
    if st_value <= 0:
        return 0
    return min(st_value & -st_value, _PAGE_ALIGNMENT_CAP)


def _parse_dynsym(section: SymbolTableSection, meta: ElfMetadata) -> None:
    for sym in section.iter_symbols():
        binding_str = sym.entry.st_info.bind
        type_str = sym.entry.st_info.type
        vis_str = sym.entry.st_other.visibility
        binding = _BINDING_MAP.get(binding_str, SymbolBinding.OTHER)
        name = sym.name

        # Any STT_TLS entry (defined or undefined import) means the library
        # participates in TLS — used to gate the DF_STATIC_TLS finding so a
        # TLS-free library is never flagged (G23-A1).
        if type_str == "STT_TLS":
            meta.has_tls_symbols = True

        # Collect undefined symbols as imports.
        if sym.entry.st_shndx == "SHN_UNDEF":
            if not name or binding == SymbolBinding.LOCAL:
                continue
            sym_type = _TYPE_MAP.get(type_str, SymbolType.NOTYPE)
            meta.imports.append(ElfImport(
                name=name,
                binding=binding,
                sym_type=sym_type,
                version="",  # correlated later via .gnu.version
                is_default=True,
            ))
            continue

        # Skip absolute (version-def markers).
        if sym.entry.st_shndx == "SHN_ABS":
            continue

        # Skip local symbols — not part of public ABI surface
        if binding == SymbolBinding.LOCAL:
            continue

        # Skip hidden/internal — not exported from DSO
        if vis_str in _HIDDEN_VISIBILITIES:
            continue

        sym_type = _TYPE_MAP.get(type_str, SymbolType.OTHER)

        # NOTE: pyelftools does NOT embed version suffixes (@@/@ notation) in
        # sym.name — that's a readelf text-output artifact. Symbol version info
        # comes from the .gnu.version section correlated with .gnu.version_d/r,
        # which is parsed separately in _parse_version_def/_parse_version_need.
        # We leave version="" here; callers correlate via versions_defined/required.

        meta.symbols.append(ElfSymbol(
            name=name,
            binding=binding,
            sym_type=sym_type,
            size=sym.entry.st_size,
            version="",
            is_default=True,
            visibility=vis_str.replace("STV_", "").lower(),
            origin_lib=_guess_symbol_origin(name, meta.needed),
            value_alignment=_value_alignment(int(sym.entry.st_value)),
        ))


def _build_verdef_index(
    section: GNUVerDefSection,
    ver_index_map: dict[int, tuple[str, str, bool]],
) -> None:
    """Build version-index → (lib="", version_name, is_defined=True) from .gnu.version_d."""
    VER_FLG_BASE = 0x1  # noqa: N806
    for verdef, verdaux_iter in section.iter_versions():
        is_base = bool(verdef.entry.vd_flags & VER_FLG_BASE)
        idx = verdef.entry.vd_ndx
        for verdaux in verdaux_iter:
            name = verdaux.name
            if name and not is_base:
                ver_index_map[idx] = ("", name, True)
            break  # only first verdaux is the version name


def _build_verneed_index(
    section: GNUVerNeedSection,
    ver_index_map: dict[int, tuple[str, str, bool]],
) -> None:
    """Build version-index → (library, version_name, is_defined=False) from .gnu.version_r."""
    for verneed, vernaux_iter in section.iter_versions():
        lib = verneed.name
        if not lib:
            continue
        for vernaux in vernaux_iter:
            idx = vernaux.entry.vna_other
            name = vernaux.name
            if name:
                ver_index_map[idx] = (lib, name, False)


def _is_import_sym(sym: object) -> bool:
    """Check if a dynsym entry is a counted import symbol."""
    if sym.entry.st_shndx != "SHN_UNDEF":
        return False
    return bool(sym.name and _BINDING_MAP.get(sym.entry.st_info.bind, SymbolBinding.OTHER) != SymbolBinding.LOCAL)


def _is_export_sym(sym: object) -> bool:
    """Check if a dynsym entry is a counted export symbol."""
    if sym.entry.st_shndx in ("SHN_UNDEF", "SHN_ABS"):
        return False
    binding = _BINDING_MAP.get(sym.entry.st_info.bind, SymbolBinding.OTHER)
    vis_str = sym.entry.st_other.visibility
    return binding != SymbolBinding.LOCAL and vis_str not in _HIDDEN_VISIBILITIES


def _parse_ver_entries(
    ver_sym_section: object, num_vers: int, so_path: Path,
) -> list[tuple[int, bool]] | None:
    """Parse .gnu.version into a list of (version_index, is_hidden) per symbol."""
    ver_entries: list[tuple[int, bool]] = []
    try:
        for i in range(num_vers):
            entry = ver_sym_section.get_symbol(i)
            raw = entry.entry["ndx"]
            if isinstance(raw, str):
                if raw == "VER_NDX_LOCAL":
                    ver_entries.append((0, False))
                else:
                    ver_entries.append((1, False))
                continue
            is_hidden = bool(raw & 0x8000)
            idx = raw & 0x7FFF
            ver_entries.append((idx, is_hidden))
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_elf_metadata: failed to read .gnu.version from %s: %s", so_path, exc)
        return None
    return ver_entries


def _correlate_symbol_versions(
    ver_sym_section: GNUVerSymSection | None,
    dynsym: SymbolTableSection | None,
    meta: ElfMetadata,
    ver_index_map: dict[int, tuple[str, str, bool]],
    so_path: Path,
) -> None:
    """Correlate .gnu.version entries with exports and imports.

    The .gnu.version section contains one Elf_Half per .dynsym entry,
    mapping each symbol to a version index. Index 0 = VER_NDX_LOCAL,
    1 = VER_NDX_GLOBAL (unversioned). Higher indices come from
    .gnu.version_d (defined) or .gnu.version_r (required).
    Bit 15 (0x8000) indicates a hidden (non-default) version.

    Accepts pre-captured sections from the main iteration loop to avoid
    redundant ``elf.iter_sections()`` calls.
    """
    if ver_sym_section is None or not ver_index_map:
        return

    try:
        num_vers = ver_sym_section.num_symbols()
    except Exception:  # noqa: BLE001
        return

    ver_entries = _parse_ver_entries(ver_sym_section, num_vers, so_path)
    if ver_entries is None:
        return

    if dynsym is None:
        return

    export_idx = 0
    import_idx = 0
    for sym_ordinal, sym in enumerate(dynsym.iter_symbols()):
        if sym_ordinal >= len(ver_entries):
            break
        ver_idx, is_hidden = ver_entries[sym_ordinal]
        export_idx, import_idx = _apply_version_to_symbol(
            sym, ver_idx, is_hidden, ver_index_map, meta, export_idx, import_idx,
        )


def _apply_version_to_symbol(
    sym: object,
    ver_idx: int,
    is_hidden: bool,
    ver_index_map: dict[int, tuple[str, str, bool]],
    meta: ElfMetadata,
    export_idx: int,
    import_idx: int,
) -> tuple[int, int]:
    """Apply version info to a single symbol, returning updated indices."""
    if ver_idx < 2:
        if _is_import_sym(sym):
            import_idx += 1
        elif _is_export_sym(sym):
            export_idx += 1
        return export_idx, import_idx

    entry = ver_index_map.get(ver_idx)
    if entry is None:
        if _is_import_sym(sym):
            import_idx += 1
        elif _is_export_sym(sym):
            export_idx += 1
        return export_idx, import_idx

    _lib_name, ver_name, _is_defined = entry

    if _is_import_sym(sym):
        if import_idx < len(meta.imports):
            meta.imports[import_idx].version = ver_name
            meta.imports[import_idx].is_default = not is_hidden
            # Record the verneed provider soname so consumers can resolve which
            # DSO satisfies this import even when a version label collides.
            meta.imports[import_idx].version_soname = _lib_name
        import_idx += 1
    elif _is_export_sym(sym):
        if export_idx < len(meta.symbols):
            meta.symbols[export_idx].version = ver_name
            meta.symbols[export_idx].is_default = not is_hidden
        export_idx += 1

    return export_idx, import_idx
