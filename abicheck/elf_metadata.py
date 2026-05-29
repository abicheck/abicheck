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

"""ELF dynamic-section and symbol-table metadata.

Uses ``pyelftools`` (pure Python, actively maintained) for robust ELF/DWARF
parsing instead of text-scraping ``readelf`` output.

See docs/adr/001-technology-stack.md for rationale.
"""
from __future__ import annotations

import logging
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import IO

from elftools.common.exceptions import ELFError
from elftools.elf.dynamic import DynamicSection
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


@dataclass
class ElfImport:
    """An undefined (imported) dynamic symbol — what this DSO requires."""
    name: str
    binding: SymbolBinding = SymbolBinding.GLOBAL  # GLOBAL or WEAK
    sym_type: SymbolType = SymbolType.NOTYPE
    version: str = ""       # required version tag (from .gnu.version + .gnu.version_r)
    is_default: bool = True  # @@default vs @specific


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

    # PT_GNU_STACK: True when the ELF has an executable stack (RWE flags).
    # This is a security bad practice (disables NX protection).
    has_executable_stack: bool = False

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

    # Extract PT_INTERP (ELF interpreter path).
    try:
        for seg in elf.iter_segments():
            if seg.header.p_type == "PT_INTERP":
                # PT_INTERP contains a null-terminated path string.
                meta.interpreter = seg.get_interp_name()
            elif seg.header.p_type == "PT_GNU_STACK":
                # PF_X (0x1) = executable. Executable stack is a security risk.
                if seg.header.p_flags & 0x1:
                    meta.has_executable_stack = True
    except Exception as exc:  # noqa: BLE001
        log.warning("parse_elf_metadata: failed to read PT_INTERP/PT_GNU_STACK from %s: %s", so_path, exc)

    # Build separate version-index maps from .gnu.version_d and .gnu.version_r.
    # Verdef and verneed indices are normally non-overlapping, but separating
    # them prevents mis-attribution if a malformed ELF reuses an index.
    verdef_index_map: dict[int, tuple[str, str, bool]] = {}   # idx → ("", ver, True)
    verneed_index_map: dict[int, tuple[str, str, bool]] = {}  # idx → (lib, ver, False)

    # Capture .gnu.version and .dynsym sections during the main loop so that
    # _correlate_symbol_versions does not need to re-iterate all sections.
    ver_sym_section: GNUVerSymSection | None = None
    dynsym_section: SymbolTableSection | None = None

    for section in elf.iter_sections():
        try:
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
        except Exception as exc:  # noqa: BLE001
            # Partial-success: log malformed section, keep results from other sections.
            log.warning("parse_elf_metadata: skipping malformed section %r in %s: %s",
                        section.name, so_path, exc)

    # Merge: verdef entries take priority over verneed on index collision.
    ver_index_map: dict[int, tuple[str, str, bool]] = {**verneed_index_map, **verdef_index_map}

    # Correlate per-symbol version entries using sections captured above.
    _correlate_symbol_versions(ver_sym_section, dynsym_section, meta, ver_index_map, so_path)

    _postprocess_metadata(meta, ver_index_map, ver_sym_section, dynsym_section, so_path)

    return meta


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


def _parse_dynamic(section: DynamicSection, meta: ElfMetadata) -> None:
    for tag in section.iter_tags():
        if tag.entry.d_tag == "DT_SONAME":
            meta.soname = tag.soname
        elif tag.entry.d_tag == "DT_NEEDED":
            meta.needed.append(tag.needed)
        elif tag.entry.d_tag == "DT_RPATH":
            meta.rpath = tag.rpath
        elif tag.entry.d_tag == "DT_RUNPATH":
            meta.runpath = tag.runpath


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


# Lookup table: (prefix_tuple, finder_fn_or_None, default_if_no_finder_match)
# When finder_fn is None, default is returned unconditionally.
_FinderFn = Callable[[list[str]], str | None]
_ORIGIN_PREFIX_TABLE: list[tuple[tuple[str, ...], _FinderFn | None, str]] = [
    # libc++ inline namespace __1 — must be checked BEFORE generic _ZNSt
    (("_ZNSt3__1", "_ZNKSt3__1"), _find_libcxx, "libc++.so.1"),
    # C++ stdlib symbols (libstdc++ / libc++)
    (("_ZNSt", "_ZNKSt", "_ZSt", "_ZTI", "_ZTS", "_ZTVN10__cxxabiv"), _find_cxx_stdlib, "libstdc++.so.6"),
    # C++ operator new / delete (Itanium ABI)
    (("_Znwm", "_Znwj", "_Znam", "_Znaj", "_ZdlPv", "_ZdaPv", "_ZnwmSt", "_ZnamSt"), _find_cxx_stdlib, "libstdc++.so.6"),
    # Intel SVML
    (("__svml_",), None, "<intel-compiler-rt>"),
    # Vectorized math functions (libmvec)
    (("_ZGV",), None, "libmvec.so.1"),
    # x87 math helpers (libgcc.a static)
    (("ix86_",), None, "libgcc.a (static)"),
    # libm SIMD helpers
    (("__libm_sse2_", "__libm_avx_"), None, "libm.so.6"),
    # GCC runtime support
    (("__cpu_model", "__cpu_features"), None, "libgcc_s.so.1"),
    # GNU libc internal
    (("__libc_", "__glibc_"), None, "libc.so.6"),
]


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
    for prefixes, finder_fn, default in _ORIGIN_PREFIX_TABLE:
        if name.startswith(prefixes):
            if finder_fn is not None:
                found = finder_fn(needed_libs)
                if found is not None:
                    return found
            return default

    return None  # likely native to this library


def _parse_dynsym(section: SymbolTableSection, meta: ElfMetadata) -> None:
    for sym in section.iter_symbols():
        binding_str = sym.entry.st_info.bind
        type_str = sym.entry.st_info.type
        vis_str = sym.entry.st_other.visibility
        binding = _BINDING_MAP.get(binding_str, SymbolBinding.OTHER)
        name = sym.name

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
        import_idx += 1
    elif _is_export_sym(sym):
        if export_idx < len(meta.symbols):
            meta.symbols[export_idx].version = ver_name
            meta.symbols[export_idx].is_default = not is_hidden
        export_idx += 1

    return export_idx, import_idx
