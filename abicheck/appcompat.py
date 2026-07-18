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

"""Application compatibility checking — ADR-005.

Answers: "Will my application still work with the new library version?"
by intersecting the app's required symbols with the library diff.

See docs/adr/005-application-compat-check.md for the full design.
"""
from __future__ import annotations

import logging
import os
import stat
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .checker import Change, DiffResult
from .checker_policy import ChangeKind, Verdict, compute_verdict
from .diff_helpers import make_change
from .model import AbiSnapshot, Visibility

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .elf_metadata import ElfMetadata
    from .macho_metadata import MachoMetadata
    from .pe_metadata import PeMetadata
    from .policy_file import PolicyFile
    from .suppression import SuppressionList

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class AppRequirements:
    """Symbols and versions an application binary requires from a library."""

    needed_libs: list[str] = field(default_factory=list)
    undefined_symbols: set[str] = field(default_factory=set)
    required_versions: dict[str, str] = field(default_factory=dict)


@dataclass
class AppCompatResult:
    """Result of checking app compatibility with a library update."""

    app_path: str
    old_lib_path: str
    new_lib_path: str

    # App's requirements
    required_symbols: set[str] = field(default_factory=set)
    required_symbol_count: int = 0

    # Filtered results
    breaking_for_app: list[Change] = field(default_factory=list)
    irrelevant_for_app: list[Change] = field(default_factory=list)
    missing_symbols: list[str] = field(default_factory=list)
    missing_versions: list[str] = field(default_factory=list)

    # Full library diff (for reference)
    full_diff: DiffResult | None = None

    # App-specific verdict
    verdict: Verdict = Verdict.COMPATIBLE

    # Coverage
    symbol_coverage: float = 100.0  # % of app's required symbols present in new lib


@dataclass
class PluginHostContractResult:
    """Result of checking a plugin upgrade against a host's load contract.

    The dlopen() failure mode is two-sided: a host resolves a fixed set of
    entry-point symbols (``dlsym``) from each plugin it loads. This is the
    plugin-load direction of :class:`AppCompatResult` — "does plugin v2 still
    satisfy host H's required entrypoints?" (ADR-005 / gap G5).
    """

    old_plugin: str
    new_plugin: str

    #: entry-point symbols the host resolves from the plugin (the contract).
    required_entrypoints: set[str] = field(default_factory=set)
    #: required entrypoints the *new* plugin no longer exports → host load break.
    missing_entrypoints: list[str] = field(default_factory=list)
    #: library diff changes that touch a required entrypoint.
    breaking_for_host: list[Change] = field(default_factory=list)
    #: full plugin v1→v2 diff (for reference / reporting).
    full_diff: DiffResult | None = None
    #: host-scoped verdict (BREAKING when an entrypoint is dropped/incompatible).
    verdict: Verdict = Verdict.COMPATIBLE
    #: % of the host's required entrypoints still provided by the new plugin.
    coverage: float = 100.0


# ---------------------------------------------------------------------------
# Binary format detection
# ---------------------------------------------------------------------------


def _detect_app_format(app_path: Path) -> str | None:
    """Detect binary format of an application: 'elf', 'pe', or 'macho'.

    Includes an ``S_ISREG`` guard (application paths may be symlinks or
    pipes) and reads the magic bytes from the same open file descriptor
    to avoid a TOCTOU race.
    """
    from .binary_utils import classify_magic

    try:
        with open(app_path, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                return None
            magic = f.read(4)
    except OSError:
        return None
    return classify_magic(magic)


# ---------------------------------------------------------------------------
# ELF: parse app requirements
# ---------------------------------------------------------------------------

def _collect_needed_libs(elf: object, reqs: AppRequirements) -> None:
    """Read DT_NEEDED entries from the ELF dynamic section."""
    from elftools.elf.dynamic import DynamicSection

    for section in elf.iter_sections():
        if isinstance(section, DynamicSection):
            for tag in section.iter_tags():
                if tag.entry.d_tag == "DT_NEEDED":
                    reqs.needed_libs.append(tag.needed)


def _build_version_index(
    elf: object, reqs: AppRequirements, library_soname: str,
) -> dict[int, str]:
    """Build version-index -> library SONAME map from .gnu.version_r.

    Each vernaux entry has vna_other (the version index used in
    .gnu.version) and the parent verneed names the source library.
    Also populates ``reqs.required_versions`` for the target library.
    """
    from elftools.elf.gnuversions import GNUVerNeedSection

    ver_idx_to_lib: dict[int, str] = {}
    for section in elf.iter_sections():
        if isinstance(section, GNUVerNeedSection):
            for verneed, vernaux_iter in section.iter_versions():
                lib = verneed.name
                if not lib:
                    continue
                for vernaux in vernaux_iter:
                    ver_idx = vernaux.entry.vna_other
                    ver_idx_to_lib[ver_idx] = lib
                    ver = vernaux.name
                    # Collect required version tags for the target library
                    if ver and library_soname and lib == library_soname:
                        reqs.required_versions[ver] = lib
    return ver_idx_to_lib


def _symbol_version_index(versym_section: object | None, idx: int) -> int:
    """Return the .gnu.version index for symbol *idx* (1 = unversioned/global)."""
    if versym_section is None:
        return 1
    try:
        ver_entry = versym_section.get_symbol(idx)
        ver_ndx = ver_entry.entry["ndx"]
        if isinstance(ver_ndx, str):
            return 0 if ver_ndx == "VER_NDX_LOCAL" else 1
        return int(ver_ndx) & 0x7FFF  # Mask off hidden bit.
    except (IndexError, KeyError):
        return 1


def _symbol_from_target_library(
    sym_name: str,
    binding: str,
    ver_ndx: int,
    reqs: AppRequirements,
    library_soname: str,
    ver_idx_to_lib: dict[int, str],
    versym_section: object | None,
) -> bool:
    """Return whether an undefined symbol is imported from the target library."""
    if not library_soname:
        return True
    from .elf_metadata import _guess_symbol_origin

    # With versioning, a concrete version index (>= 2) maps directly to a lib.
    if versym_section is not None and ver_ndx >= 2:
        return ver_idx_to_lib.get(ver_ndx, "") == library_soname
    # Otherwise fall back to a heuristic on the symbol name / weak binding.
    origin = _guess_symbol_origin(sym_name, reqs.needed_libs)
    if origin is not None:
        return origin == library_soname
    return binding != "STB_WEAK"


def _collect_undefined_symbols(
    elf: object,
    reqs: AppRequirements,
    library_soname: str,
    ver_idx_to_lib: dict[int, str],
    versym_section: object | None,
) -> None:
    """Read undefined symbols from .dynsym, filtered by target library."""
    from elftools.elf.sections import SymbolTableSection

    for section in elf.iter_sections():
        if not (isinstance(section, SymbolTableSection) and section.name == ".dynsym"):
            continue
        for idx, sym in enumerate(section.iter_symbols()):
            if sym.entry.st_shndx != "SHN_UNDEF" or not sym.name:
                continue
            binding = sym.entry.st_info.bind
            if binding not in ("STB_GLOBAL", "STB_WEAK"):
                continue
            ver_ndx = _symbol_version_index(versym_section, idx)
            if not _symbol_from_target_library(
                sym.name,
                binding,
                ver_ndx,
                reqs,
                library_soname,
                ver_idx_to_lib,
                versym_section,
            ):
                continue
            reqs.undefined_symbols.add(sym.name)


def _parse_elf_app_requirements(
    app_path: Path, library_soname: str,
) -> AppRequirements:
    """Extract app requirements for a specific library from an ELF binary.

    Reads .dynsym for UNDEF symbols, correlates with .gnu.version and
    .gnu.version_r to filter symbols to those imported from ``library_soname``.
    """
    from elftools.common.exceptions import ELFError
    from elftools.elf.elffile import ELFFile
    from elftools.elf.gnuversions import GNUVerSymSection

    reqs = AppRequirements()

    try:
        with open(app_path, "rb") as f:
            elf = ELFFile(f)

            # 1. Read DT_NEEDED entries
            _collect_needed_libs(elf, reqs)

            # 2. Build version-index → library SONAME map from .gnu.version_r
            ver_idx_to_lib = _build_version_index(elf, reqs, library_soname)

            # 3. Read .gnu.version section (per-symbol version indices)
            versym_section: GNUVerSymSection | None = None
            for section in elf.iter_sections():
                if isinstance(section, GNUVerSymSection):
                    versym_section = section
                    break

            # 4. Read undefined symbols from .dynsym, filtered by target library
            _collect_undefined_symbols(elf, reqs, library_soname, ver_idx_to_lib, versym_section)

    except (ELFError, OSError, ValueError) as exc:
        log.warning("Failed to parse ELF app requirements from %s: %s", app_path, exc)

    return reqs


# ---------------------------------------------------------------------------
# PE: parse app requirements
# ---------------------------------------------------------------------------

def _parse_pe_app_requirements(
    app_path: Path, library_name: str,
) -> AppRequirements:
    """Extract app requirements for a specific DLL from a PE binary."""
    import pefile

    reqs = AppRequirements()
    library_name_lower = library_name.lower() if library_name else ""

    try:
        pe = pefile.PE(str(app_path), fast_load=True)
        try:
            pe.parse_data_directories(
                directories=[
                    pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_IMPORT"],
                ]
            )

            if hasattr(pe, "DIRECTORY_ENTRY_IMPORT"):
                for entry in pe.DIRECTORY_ENTRY_IMPORT:
                    dll_name = entry.dll.decode("utf-8", errors="replace") if entry.dll else ""
                    reqs.needed_libs.append(dll_name)

                    # Only collect symbols for the target DLL
                    if library_name_lower and dll_name.lower() != library_name_lower:
                        continue

                    for imp in entry.imports:
                        if imp.name:
                            reqs.undefined_symbols.add(
                                imp.name.decode("utf-8", errors="replace")
                            )
                        elif getattr(imp, "import_by_ordinal", False):
                            reqs.undefined_symbols.add(f"ordinal:{imp.ordinal}")
        finally:
            pe.close()

    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to parse PE app requirements from %s: %s", app_path, exc)

    return reqs


# ---------------------------------------------------------------------------
# Mach-O: parse app requirements
# ---------------------------------------------------------------------------

def _find_target_ordinal(reqs: AppRequirements, library_name: str) -> int | None:
    """Determine 1-based index of target library in LC_LOAD_DYLIB list.

    In Mach-O two-level namespace, the library ordinal stored in
    n_desc bits [15:8] is a 1-based index into the load-dylib list.
    """
    if not library_name:
        return None
    lib_lower = library_name.lower()
    for idx, lib in enumerate(reqs.needed_libs, start=1):
        # Match by exact path, basename, or install_name
        if (lib.lower() == lib_lower
                or os.path.basename(lib).lower() == lib_lower
                or lib_lower in lib.lower()):
            return idx
    return None


def _collect_macho_undefined_symbols(
    macho: object, header: object, reqs: AppRequirements, target_ordinal: int | None,
) -> None:
    """Read undefined symbols from a Mach-O header, filtered by target library ordinal."""
    from macholib.mach_o import N_EXT, N_TYPE, N_UNDF
    from macholib.SymbolTable import SymbolTable

    symtab = SymbolTable(macho, header=header)
    # Check undefsyms first (available when LC_DYSYMTAB is present)
    symbols = getattr(symtab, "undefsyms", None) or symtab.nlists
    for nlist_entry, name_bytes in symbols:
        n_type = int(nlist_entry.n_type)

        # For undefsyms, they're already filtered. For nlists, filter manually.
        if symbols is symtab.nlists:
            if not (n_type & N_EXT):
                continue
            if (n_type & N_TYPE) != N_UNDF:
                continue

        # Filter by library ordinal when target is known
        if target_ordinal is not None:
            n_desc = int(nlist_entry.n_desc)
            ordinal = (n_desc >> 8) & 0xFF
            # Reject special ordinals: 0 = SELF, 0xFE = EXECUTABLE, 0xFF = DYNAMIC_LOOKUP
            if ordinal in (0, 0xFE, 0xFF) or ordinal != target_ordinal:
                continue

        name = name_bytes.decode("utf-8", errors="replace") if name_bytes else ""
        # Strip leading underscore (Mach-O C symbol convention)
        if name.startswith("_"):
            name = name[1:]
        if name:
            reqs.undefined_symbols.add(name)


def _parse_macho_app_requirements(
    app_path: Path, library_name: str,
) -> AppRequirements:
    """Extract app requirements for a specific dylib from a Mach-O binary."""
    from macholib.mach_o import LC_LOAD_DYLIB
    from macholib.MachO import MachO

    reqs = AppRequirements()

    try:
        macho = MachO(str(app_path))
        if not macho.headers:
            return reqs

        header = macho.headers[0]

        # 1. Read dependent libraries
        for lc, cmd, data in header.commands:
            if lc.cmd == LC_LOAD_DYLIB:
                if data:
                    end = data.find(b"\x00")
                    if end < 0:
                        end = len(data)
                    name = data[:end].decode("utf-8", errors="replace")
                    reqs.needed_libs.append(name)

        # 2. Determine index of target library
        target_ordinal = _find_target_ordinal(reqs, library_name)

        # 3. Read undefined symbols, filtered by target library ordinal
        try:
            _collect_macho_undefined_symbols(macho, header, reqs, target_ordinal)
        except Exception as exc:  # noqa: BLE001
            log.debug("SymbolTable failed for %s: %s", app_path, exc)

    except (OSError, ValueError, struct.error) as exc:
        log.warning("Failed to parse Mach-O app requirements from %s: %s", app_path, exc)

    return reqs


# ---------------------------------------------------------------------------
# Public API: parse_app_requirements
# ---------------------------------------------------------------------------

def parse_app_requirements(
    app_path: Path, library_name: str,
) -> AppRequirements:
    """Extract app's requirements for a specific library.

    Args:
        app_path: Path to the application binary (ELF, PE, or Mach-O).
        library_name: SONAME/DLL name/dylib path to filter by.

    Returns:
        AppRequirements with the app's needed libs, undefined symbols,
        and required versions.

    Raises:
        ValueError: If the binary format cannot be detected.
    """
    fmt = _detect_app_format(app_path)
    if fmt == "elf":
        return _parse_elf_app_requirements(app_path, library_name)
    if fmt == "pe":
        return _parse_pe_app_requirements(app_path, library_name)
    if fmt == "macho":
        return _parse_macho_app_requirements(app_path, library_name)
    raise ValueError(
        f"Cannot detect binary format of '{app_path}'. "
        "Expected: ELF, PE, or Mach-O executable."
    )


# ---------------------------------------------------------------------------
# Filtering: is a change relevant to the app?
# ---------------------------------------------------------------------------

def _is_relevant_to_app(change: Change, app: AppRequirements) -> bool:
    """Does this change affect a symbol the application uses?

    FIX-A Part 3: handles two symbol format mismatches:
    1. change.symbol may be C++-mangled while app uses plain C names
    2. change.affected_symbols now includes both mangled and demangled names
    """
    # Direct symbol match
    if change.symbol in app.undefined_symbols:
        return True

    # Demangled fallback for change.symbol (FIX-A Part 3, Mismatch 1):
    # change.symbol may be C++-mangled (e.g. "_Z3addii") while app uses
    # the plain C linker name (e.g. "add").
    from .demangle import demangle as _demangle_symbol
    plain = _demangle_symbol(change.symbol)
    if plain and plain != change.symbol and plain in app.undefined_symbols:
        return True

    # Type change affecting app's symbols (via affected_symbols enrichment).
    # affected_symbols now contains both demangled and mangled names (FIX-A Part 3).
    if change.affected_symbols:
        if app.undefined_symbols & set(change.affected_symbols):
            return True

    # ELF SONAME changes affect consumers that record the old SONAME in
    # DT_NEEDED: even with the same exported symbols, the dynamic loader may
    # fail unless the old SONAME remains available.
    if change.kind == ChangeKind.SONAME_CHANGED:
        return bool(change.old_value and change.old_value in app.needed_libs)

    # Mach-O compat version change affects all consumers
    if change.kind == ChangeKind.COMPAT_VERSION_CHANGED:
        return True

    # Symbol version removal for a version the app requires.
    # change.symbol is the version tag (e.g. "FOO_1.0"); app.required_versions
    # maps version_tag → library_soname.  If the tag is in the map, the app
    # depends on it and the removal is relevant.
    if change.kind == ChangeKind.SYMBOL_VERSION_DEFINED_REMOVED:
        if change.symbol in app.required_versions:
            return True

    return False


def _change_covers_symbol(change: Change, symbol: str) -> bool:
    """Does *change* already account for *symbol* (exact, demangled, or via
    ``affected_symbols``)? Mirrors :func:`_is_relevant_to_app`'s matching in
    reverse -- symbol-name lookup, not app-requirements lookup."""
    if change.symbol == symbol:
        return True
    from .demangle import demangle as _demangle_symbol

    plain = _demangle_symbol(change.symbol)
    if plain and plain == symbol:
        return True
    return bool(change.affected_symbols and symbol in change.affected_symbols)


def uncovered_missing_symbols(
    missing: Iterable[str], relevant_changes: Iterable[Change],
) -> list[str]:
    """*missing* entries not already represented by a *relevant_changes* Change.

    A required symbol that was removed shows up twice in a scoped result:
    once in ``missing_symbols``/``missing_entrypoints`` (absent from the new
    export table) and once as the diff Change that actually removed it (e.g.
    ``FUNC_REMOVED``) in ``breaking_for_app``/``breaking_for_host``. Callers
    that derive a severity-scheme finding count from both must not count
    that as two ABI breaks — this is the missing-symbol side of that dedup
    (Codex review): only symbols with no matching Change are genuinely
    "extra" (e.g. a symbol dropped for a reason the diff itself never
    surfaced as a Change, such as a versioned-symbol default retarget).
    """
    changes = list(relevant_changes)
    return [
        m for m in missing
        if not any(_change_covers_symbol(c, m) for c in changes)
    ]


# ---------------------------------------------------------------------------
# Get new library exported symbols
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# OLD/NEW library data access: a saved JSON snapshot (AbiSnapshot) already
# carries the SONAME/exports/versions/PE-ordinal-table data below, so
# --used-by need not re-parse a real binary for these lookups when a
# snapshot is what the caller has (ADR-043 follow-up: dump-then-compare-later
# workflows previously required a real OLD/NEW binary purely for this). A raw
# Path is still parsed directly. The app binary itself is untouched by this —
# it always needs a real ELF/PE/Mach-O file to read DT_NEEDED/import tables.
# ---------------------------------------------------------------------------


def _lib_fmt(lib: Path | AbiSnapshot) -> str | None:
    """Detect *lib*'s binary format, from a snapshot's populated field or a raw file."""
    if isinstance(lib, AbiSnapshot):
        if lib.elf is not None:
            return "elf"
        if lib.pe is not None:
            return "pe"
        if lib.macho is not None:
            return "macho"
        return None
    return _detect_app_format(lib)


def _lib_name(lib: Path | AbiSnapshot) -> str:
    """A display name for *lib* -- the snapshot's library field, or the file name."""
    return lib.library if isinstance(lib, AbiSnapshot) else lib.name


def _lib_elf_meta(lib: Path | AbiSnapshot) -> ElfMetadata | None:
    if isinstance(lib, AbiSnapshot):
        return lib.elf
    if _detect_app_format(lib) != "elf":
        return None
    from .elf_metadata import parse_elf_metadata
    return parse_elf_metadata(lib)


def _lib_pe_meta(lib: Path | AbiSnapshot) -> PeMetadata | None:
    if isinstance(lib, AbiSnapshot):
        return lib.pe
    if _detect_app_format(lib) != "pe":
        return None
    from .pe_metadata import parse_pe_metadata
    return parse_pe_metadata(lib)


def _lib_macho_meta(lib: Path | AbiSnapshot) -> MachoMetadata | None:
    if isinstance(lib, AbiSnapshot):
        return lib.macho
    if _detect_app_format(lib) != "macho":
        return None
    from .macho_metadata import parse_macho_metadata
    return parse_macho_metadata(lib)


def _get_new_lib_exports(new_lib: Path | AbiSnapshot) -> set[str]:
    """Get the set of exported symbol names from the new library."""
    fmt = _lib_fmt(new_lib)
    if fmt == "elf":
        elf_meta = _lib_elf_meta(new_lib)
        return {s.name for s in elf_meta.symbols} if elf_meta is not None else set()
    if fmt == "pe":
        pe_meta = _lib_pe_meta(new_lib)
        return {e.name for e in pe_meta.exports if e.name} if pe_meta is not None else set()
    if fmt == "macho":
        macho_meta = _lib_macho_meta(new_lib)
        return (
            {e.name for e in macho_meta.exports if e.name}
            if macho_meta is not None
            else set()
        )
    return set()


def _normalize_elf_symbol_name(name: str) -> str:
    """Normalize ELF symbol name for cross-source matching.

    Strips GNU version suffixes (``@VER`` / ``@@VER``) when present.
    pyelftools usually returns plain names, but runtime/linker sources may
    include suffixes, so this keeps matching robust.
    """
    return name.split("@", 1)[0]


def _get_old_lib_exports_for_scoping(old_lib: Path | AbiSnapshot) -> set[str]:
    """Best-effort export set for the old library (ELF-only).

    Used to scope app-required symbols to the target DSO and avoid false
    positives from unrelated dependencies in large consumer binaries.
    """
    try:
        elf_meta = _lib_elf_meta(old_lib)
    except Exception as exc:  # noqa: BLE001
        log.debug("Failed to read old-lib exports for appcompat scoping: %s", exc)
        return set()
    if elf_meta is None:
        return set()
    return {_normalize_elf_symbol_name(s.name) for s in elf_meta.symbols}


def _get_lib_soname(lib: Path | AbiSnapshot) -> str:
    """Get the SONAME/install_name/DLL name from a library."""
    fmt = _lib_fmt(lib)
    name = _lib_name(lib)
    if fmt == "elf":
        elf_meta = _lib_elf_meta(lib)
        return (elf_meta.soname if elf_meta is not None else None) or name
    if fmt == "macho":
        macho_meta = _lib_macho_meta(lib)
        return (macho_meta.install_name if macho_meta is not None else None) or name
    return name


# ---------------------------------------------------------------------------
# Core: appcompat check
# ---------------------------------------------------------------------------

def _scope_app_symbols_to_library(
    app_reqs: AppRequirements, old_lib: Path | AbiSnapshot, app_path: Path,
) -> None:
    """Scope app-required symbols to those actually exported by the target library.

    For ELF binaries, normalises symbol names and intersects with the old
    library's exports to avoid false positives from unrelated dependencies.
    Modifies ``app_reqs.undefined_symbols`` in place.
    """
    if _detect_app_format(app_path) != "elf" or _lib_fmt(old_lib) != "elf":
        return

    # Normalize app symbols to keep matching robust when version suffixes
    # appear in one data source but not the other.
    app_reqs.undefined_symbols = {
        _normalize_elf_symbol_name(s) for s in app_reqs.undefined_symbols
    }

    old_exports = _get_old_lib_exports_for_scoping(old_lib)
    old_label = old_lib if isinstance(old_lib, Path) else old_lib.library
    if old_exports:
        before = len(app_reqs.undefined_symbols)
        app_reqs.undefined_symbols = {
            s for s in app_reqs.undefined_symbols if s in old_exports
        }
        dropped = before - len(app_reqs.undefined_symbols)
        if dropped > 0:
            log.debug(
                "appcompat scoped %d symbols to target library exports (%s)",
                dropped,
                old_label,
            )
    else:
        log.debug(
            "appcompat scoping skipped: no exports parsed for target library (%s)",
            old_label,
        )


def _compute_appcompat_verdict(
    missing_symbols: list[str],
    missing_versions: list[str],
    breaking_for_app: list[Change],
    required_count: int,
    policy: str,
    policy_file: PolicyFile | None,
) -> Verdict:
    """Determine the app-specific compatibility verdict."""
    if missing_symbols or missing_versions:
        return Verdict.BREAKING
    if breaking_for_app:
        if policy_file is not None:
            return policy_file.compute_verdict(breaking_for_app)
        return compute_verdict(breaking_for_app, policy=policy)
    return Verdict.COMPATIBLE if required_count > 0 else Verdict.NO_CHANGE


def _missing_app_versions(
    new_lib: Path | AbiSnapshot, app_reqs: AppRequirements,
) -> list[str]:
    """Return ELF version tags required by the app but absent from the new library."""
    elf_meta = _lib_elf_meta(new_lib)
    if elf_meta is None:
        return []
    new_defined_versions = set(elf_meta.versions_defined)
    return [
        ver_tag
        for ver_tag in app_reqs.required_versions
        if ver_tag not in new_defined_versions
    ]


def _check_pe_ordinal_imports(
    old_lib: Path | AbiSnapshot, new_lib: Path | AbiSnapshot, app_reqs: AppRequirements,
) -> tuple[set[str], list[Change], set[str]]:
    """Resolve the app's ordinal-only PE imports against old/new export tables.

    A PE consumer that imports by ordinal (``import_by_ordinal``) never names
    its target function — ``parse_app_requirements`` records it as
    ``"ordinal:N"``. ``_get_new_lib_exports`` returns names only, so such a
    requirement always reads as "missing" from the generic check even when
    the ordinal still resolves. This cross-references the ordinal against
    both DLLs' export directories:

    * ordinal still exists and names the SAME function → satisfied; excluded
      from the generic missing-symbols check.
    * ordinal still exists but now names a DIFFERENT function → the app
      silently calls the wrong function with no link/load error — reported
      as PE_ORDINAL_RETARGETED rather than a generic missing symbol.
    * ordinal no longer exists at all → left in the generic missing-symbols
      set (genuinely dropped).
    * ordinal did not exist in the OLD library either → not attributable to
      this version change; left alone.

    Returns (resolved_requirement_strings, retargeted_changes,
    resolved_export_names). ``resolved_export_names`` carries the old/new
    export name(s) behind each resolved ordinal so the caller can fold them
    into the relevance check: an ordinal-only consumer has no name of its own
    in ``app_reqs.undefined_symbols`` to match against, so a library diff
    finding for the *named* export it silently resolves to (e.g. a signature
    change to the export the ordinal has always pointed at) would otherwise
    be misclassified as ``irrelevant_for_app``.
    """
    ordinal_reqs = {s for s in app_reqs.undefined_symbols if s.startswith("ordinal:")}
    if not ordinal_reqs or _lib_fmt(old_lib) != "pe":
        return set(), [], set()

    try:
        old_pe = _lib_pe_meta(old_lib)
        new_pe = _lib_pe_meta(new_lib)
        if old_pe is None or new_pe is None:
            return set(), [], set()
        old_by_ordinal = {e.ordinal: e.name for e in old_pe.exports}
        new_by_ordinal = {e.ordinal: e.name for e in new_pe.exports}
    except Exception as exc:  # noqa: BLE001
        log.debug("Failed to resolve PE ordinal exports for appcompat: %s", exc)
        return set(), [], set()

    resolved: set[str] = set()
    retargeted: list[Change] = []
    export_names: set[str] = set()
    for req in sorted(ordinal_reqs):
        try:
            ordinal = int(req.split(":", 1)[1])
        except ValueError:
            continue
        old_name = old_by_ordinal.get(ordinal)
        if old_name is None or ordinal not in new_by_ordinal:
            continue
        new_name = new_by_ordinal[ordinal]
        resolved.add(req)
        if old_name:
            export_names.add(old_name)
        if new_name:
            export_names.add(new_name)
        if new_name != old_name:
            retargeted.append(
                make_change(
                    ChangeKind.PE_ORDINAL_RETARGETED,
                    symbol=req,
                    name=f"ordinal {ordinal}",
                    old=old_name or "(unnamed)",
                    new=new_name or "(unnamed)",
                )
            )
    return resolved, retargeted, export_names


def _partition_app_changes(
    diff: DiffResult, app_reqs: AppRequirements,
) -> tuple[list[Change], list[Change]]:
    """Split diff changes into (relevant-to-app, irrelevant-to-app)."""
    breaking_for_app: list[Change] = []
    irrelevant_for_app: list[Change] = []
    for change in diff.changes:
        target = breaking_for_app if _is_relevant_to_app(change, app_reqs) else irrelevant_for_app
        target.append(change)
    return breaking_for_app, irrelevant_for_app


def _compute_symbol_coverage(
    new_exports: set[str], required_count: int, missing_count: int,
) -> float:
    """Percentage of required app symbols still available in the new library."""
    if not new_exports:
        return 0.0 if required_count > 0 else 100.0
    if required_count == 0:
        return 100.0
    return (required_count - missing_count) / required_count * 100.0


def scope_diff_to_app(
    diff: DiffResult,
    app_path: Path,
    old_lib: Path | AbiSnapshot,
    new_lib: Path | AbiSnapshot,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
) -> AppCompatResult:
    """Scope an already-computed library diff to one application's actual usage.

    This is the generic-scoped-comparison core ``compare --used-by`` calls
    (ADR-043): the full old/new library comparison runs exactly once (by the
    caller, typically ``compare``'s own pipeline); this function only parses
    the application's requirements and intersects them with that diff. It does
    NOT re-dump or re-compare the libraries — see :func:`check_appcompat` for
    the standalone (single-app, no pre-existing diff) convenience wrapper.

    *old_lib*/*new_lib* may be a real library binary path, or an already-
    loaded :class:`~abicheck.model.AbiSnapshot` (e.g. from a saved JSON dump)
    -- a snapshot's ``elf``/``pe``/``macho`` fields already carry the SONAME,
    export table, ELF version list, and PE ordinal table this function needs,
    so no re-parse of a real binary is required for those lookups. *app_path*
    is unaffected: the application itself always needs a real binary to read
    its DT_NEEDED/import table from.
    """
    library_soname = _get_lib_soname(old_lib)
    app_reqs = parse_app_requirements(app_path, library_soname)

    # Guard against over-collection in ELF consumers with many dependencies:
    # keep only symbols that are actually exported by the target old library.
    _scope_app_symbols_to_library(app_reqs, old_lib, app_path)

    # Check symbol availability in new library
    new_exports = _get_new_lib_exports(new_lib)
    # Ordinal-only PE imports never match a name in new_exports; resolve them
    # against both DLLs' export directories so they aren't reported as
    # generically "missing" when the ordinal still (possibly differently)
    # resolves.
    resolved_ordinals, ordinal_retargets, resolved_export_names = _check_pe_ordinal_imports(
        old_lib, new_lib, app_reqs
    )
    missing_symbols = sorted(
        sym for sym in app_reqs.undefined_symbols
        if sym not in new_exports and sym not in resolved_ordinals
    )

    # Check version availability
    missing_versions = _missing_app_versions(new_lib, app_reqs)

    # Filter diff by app usage. An ordinal-only import carries no name of its
    # own in app_reqs.undefined_symbols, so a diff finding for the named export
    # the ordinal resolves to would otherwise read as irrelevant; layer the
    # resolved name(s) into a relevance-only view without perturbing app_reqs
    # (used above for missing/coverage and below in the result).
    relevance_reqs = app_reqs
    if resolved_export_names:
        relevance_reqs = AppRequirements(
            needed_libs=app_reqs.needed_libs,
            undefined_symbols=app_reqs.undefined_symbols | resolved_export_names,
            required_versions=app_reqs.required_versions,
        )
    breaking_for_app, irrelevant_for_app = _partition_app_changes(diff, relevance_reqs)
    breaking_for_app = breaking_for_app + ordinal_retargets

    # ADR-044 P2 item 1: promote a missing symbol not already represented by a
    # library-diff Change (e.g. FUNC_REMOVED) into a first-class, suppressible
    # CONSUMER_REQUIRED_SYMBOL_REMOVED finding, instead of leaving it as a
    # bespoke string only special-cased by reporter.py/sarif.py/junit_report.py.
    # Scoped to the genuinely-uncovered subset via uncovered_missing_symbols
    # (the same dedup _scoped_severity_summary/cli_compare_helpers.py already
    # use) so a symbol already covered by a real diff Change is never
    # double-reported as both that Change and this overlay.
    for sym in uncovered_missing_symbols(missing_symbols, breaking_for_app):
        breaking_for_app.append(
            make_change(
                ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
                symbol=sym,
                name=app_path.name,
            )
        )

    # Compute app-specific verdict
    required_count = len(app_reqs.undefined_symbols)
    coverage = _compute_symbol_coverage(new_exports, required_count, len(missing_symbols))

    verdict = _compute_appcompat_verdict(
        missing_symbols, missing_versions, breaking_for_app,
        required_count, policy, policy_file,
    )

    return AppCompatResult(
        app_path=str(app_path),
        old_lib_path=str(old_lib) if isinstance(old_lib, Path) else old_lib.library,
        new_lib_path=str(new_lib) if isinstance(new_lib, Path) else new_lib.library,
        required_symbols=app_reqs.undefined_symbols,
        required_symbol_count=required_count,
        breaking_for_app=breaking_for_app,
        irrelevant_for_app=irrelevant_for_app,
        missing_symbols=missing_symbols,
        missing_versions=missing_versions,
        full_diff=diff,
        verdict=verdict,
        symbol_coverage=coverage,
    )


def check_appcompat(
    app_path: Path,
    old_lib_path: Path,
    new_lib_path: Path,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    old_headers: list[Path] | None = None,
    new_headers: list[Path] | None = None,
    old_includes: list[Path] | None = None,
    new_includes: list[Path] | None = None,
    old_version: str = "old",
    new_version: str = "new",
    lang: str = "c++",
    suppression: SuppressionList | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    scope_to_public_surface: bool = True,
) -> AppCompatResult:
    """Check application compatibility with a library update (standalone).

    Dumps and compares the two libraries itself, then delegates the app-usage
    scoping to :func:`scope_diff_to_app`. When a diff already exists (e.g.
    inside ``compare``'s own pipeline), call :func:`scope_diff_to_app` directly
    instead of re-dumping/re-comparing through this wrapper.
    """
    # Run standard library comparison
    from .dumper import dump

    # Resolve per-side headers: old_headers/new_headers override shared headers
    _old_h = old_headers if old_headers is not None else (headers or [])
    _new_h = new_headers if new_headers is not None else (headers or [])
    _old_inc = old_includes if old_includes is not None else (includes or [])
    _new_inc = new_includes if new_includes is not None else (includes or [])

    # appcompat's -H/--header is documented as "Public header file or
    # directory" (like compare's), so the same paths double as the
    # public-header set for provenance tagging (ADR-024/ADR-015).
    old_snap = dump(
        so_path=old_lib_path,
        headers=_old_h,
        extra_includes=_old_inc,
        version=old_version,
        compiler="c++" if lang == "c++" else "cc",
        lang="c" if lang == "c" else None,
        public_headers=list(_old_h),
    )
    new_snap = dump(
        so_path=new_lib_path,
        headers=_new_h,
        extra_includes=_new_inc,
        version=new_version,
        compiler="c++" if lang == "c++" else "cc",
        lang="c" if lang == "c" else None,
        public_headers=list(_new_h),
    )

    # Route through the Tier-2 service (lazy import avoids a
    # service→cli→appcompat import cycle); ADR-037 D1.
    from .service import compare_snapshots
    diff = compare_snapshots(old_snap, new_snap, suppression=suppression, policy=policy, policy_file=policy_file, scope_to_public_surface=scope_to_public_surface)

    return scope_diff_to_app(
        diff, app_path, old_lib_path, new_lib_path,
        policy=policy, policy_file=policy_file,
    )


# ---------------------------------------------------------------------------
# Weak mode: check-against (no old library needed)
# ---------------------------------------------------------------------------

def check_against(
    app_path: Path,
    new_lib_path: Path,
) -> AppCompatResult:
    """Check if a library provides everything the app needs (weak mode).

    No old library required — just checks symbol availability.
    """
    library_name = _get_lib_soname(new_lib_path)
    app_reqs = parse_app_requirements(app_path, library_name)

    new_exports = _get_new_lib_exports(new_lib_path)
    missing_symbols = sorted(
        sym for sym in app_reqs.undefined_symbols
        if sym not in new_exports
    )

    # Check version availability for ELF
    missing_versions = _missing_app_versions(new_lib_path, app_reqs)

    required_count = len(app_reqs.undefined_symbols)
    coverage = _compute_symbol_coverage(new_exports, required_count, len(missing_symbols))

    verdict = Verdict.BREAKING if (missing_symbols or missing_versions) else Verdict.COMPATIBLE

    return AppCompatResult(
        app_path=str(app_path),
        old_lib_path="",
        new_lib_path=str(new_lib_path),
        required_symbols=app_reqs.undefined_symbols,
        required_symbol_count=required_count,
        breaking_for_app=[],
        irrelevant_for_app=[],
        missing_symbols=missing_symbols,
        missing_versions=missing_versions,
        full_diff=None,
        verdict=verdict,
        symbol_coverage=coverage,
    )


# ---------------------------------------------------------------------------
# Plugin host↔plugin load contract (the dlopen direction) — gap G5
# ---------------------------------------------------------------------------

def _resolvable_symbol_names(name: str, mangled: str | None) -> set[str]:
    """The names ``dlsym`` could actually resolve for one exported entity.

    ``dlsym`` resolves the *linker* symbol — the mangled name. The source
    ``name`` is only resolvable when it *is* the linker symbol (``extern "C"``
    or C, where ``mangled == name``); a demangled C++ name like ``foo(int)`` is
    NOT a dlsym key and must not count as satisfying a host contract. When no
    mangled name is recorded, fall back to ``name`` as the best available key.
    """
    if mangled:
        names = {mangled}
        if name == mangled:
            names.add(name)
        return names
    return {name}


#: Visibilities that correspond to a symbol actually exported from the binary.
#: PUBLIC is the header/DWARF-aware default; ELF_ONLY is how a symbols-only dump
#: (a stripped binary with no headers/DWARF — the common `plugin-check old.so
#: new.so` case) represents an exported `.dynsym` entry. HIDDEN is not exported.
_EXPORTED_VISIBILITIES: frozenset[Visibility] = frozenset(
    {Visibility.PUBLIC, Visibility.ELF_ONLY}
)


def _snapshot_export_names(snap: AbiSnapshot) -> set[str]:
    """Linker-symbol names a host could resolve from a plugin via ``dlsym``.

    Exported functions and variables, keyed by their mangled (linker) symbol —
    plus the plain source name only for ``extern "C"`` / C symbols where it
    equals the mangled name. A demangled C++ name is deliberately excluded so a
    contract listing it is reported as *missing*, matching ``dlsym`` reality.

    Both header/DWARF-aware (``PUBLIC``) and symbols-only (``ELF_ONLY``) exports
    count: running ``plugin-check`` on real stripped binaries without headers is
    the common case, and there every export is ``ELF_ONLY``.
    """
    names: set[str] = set()
    for fn in snap.functions:
        if fn.visibility in _EXPORTED_VISIBILITIES:
            names |= _resolvable_symbol_names(fn.name, fn.mangled)
    for var in snap.variables:
        if var.visibility in _EXPORTED_VISIBILITIES:
            names |= _resolvable_symbol_names(var.name, getattr(var, "mangled", None))
    return names


def scope_diff_to_required_symbols(
    diff: DiffResult,
    old_plugin: AbiSnapshot,
    new_plugin: AbiSnapshot,
    required_entrypoints: Iterable[str],
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
) -> PluginHostContractResult:
    """Scope an already-computed diff to an explicit required-symbol contract.

    This is the generic-scoped-comparison core ``compare --required-symbol(s)``
    calls (ADR-043) — the plugin-host mirror of :func:`scope_diff_to_app`. The
    full old/new comparison runs exactly once (by the caller); this function
    only intersects the given ``required_entrypoints`` with that diff. See
    :func:`check_plugin_host_contract` for the standalone convenience wrapper
    that also runs the comparison itself.
    """
    required = set(required_entrypoints)
    new_exports = _snapshot_export_names(new_plugin)
    missing = sorted(e for e in required if e not in new_exports)

    # Reuse the app-scoping machinery: the contract is a set of required
    # ("undefined") symbols, identical in shape to an app's symbol needs.
    host_reqs = AppRequirements(undefined_symbols=set(required))
    breaking_for_host, _ = _partition_app_changes(diff, host_reqs)

    verdict = _compute_appcompat_verdict(
        missing, [], breaking_for_host, len(required), policy, policy_file,
    )
    coverage = _compute_symbol_coverage(new_exports, len(required), len(missing))

    return PluginHostContractResult(
        old_plugin=old_plugin.library or "old",
        new_plugin=new_plugin.library or "new",
        required_entrypoints=required,
        missing_entrypoints=missing,
        breaking_for_host=breaking_for_host,
        full_diff=diff,
        verdict=verdict,
        coverage=coverage,
    )


def check_plugin_host_contract(
    old_plugin: AbiSnapshot,
    new_plugin: AbiSnapshot,
    required_entrypoints: Iterable[str],
    *,
    suppression: SuppressionList | None = None,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
) -> PluginHostContractResult:
    """Check whether a plugin upgrade still satisfies a host's load contract.

    Given two snapshots of a plugin (old/new) and the set of entry-point
    symbols a *host* resolves from it (a manifest, or symbols a host binary
    exports back to the plugin), report whether the new plugin still satisfies
    the host — the plugin-load mirror of :func:`check_appcompat`. Runs the
    comparison itself; when a diff already exists, call
    :func:`scope_diff_to_required_symbols` directly instead.
    """
    # Route through the Tier-2 service (lazy import avoids a
    # service→cli→appcompat import cycle); ADR-037 D1.
    from .service import compare_snapshots
    diff = compare_snapshots(
        old_plugin, new_plugin,
        suppression=suppression, policy=policy, policy_file=policy_file,
    )

    return scope_diff_to_required_symbols(
        diff, old_plugin, new_plugin, required_entrypoints,
        policy=policy, policy_file=policy_file,
    )
