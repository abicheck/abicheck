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
from .checker_policy import ChangeKind, ReachabilityState, Verdict, compute_verdict
from .diff_helpers import make_change
from .impact.engine import assess_change
from .model import AbiSnapshot, Visibility
from .policy.disposition_close import (
    close_consumer_scope,
    ledger_for,
    record_kept_change,
)
from .policy.disposition_ledger import record_suppressed_change

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .elf_metadata import ElfMetadata
    from .impact.consumer_graph import ConsumerImpactPath
    from .macho_metadata import MachoMetadata
    from .model.source_graph import SourceGraphSummary
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
        for lc, _cmd, data in header.commands:
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


def _promote_scoped_contract(
    changes: list[Change],
    *,
    policy: str | None,
    policy_file: PolicyFile | None,
    diff: DiffResult | None = None,
) -> None:
    """Apply ADR-049 §4.3's explicit-scope evidence to a scoped finding set.

    A finding this module has just decided is relevant to a concrete
    consumer's imports (or to an explicitly required entrypoint) *is* that
    ADR's strongest in-contract evidence -- stronger than, and independent
    of, the header/export-derived relevance ``compare()`` reached. Since
    ADR-049 Phase 7 made relevance authoritative, that has to be applied
    here, at the point the relevance is decided, rather than by a later
    stamping pass: both the CLI and the MCP tool compute their *scoped exit
    code* from these lists immediately, so a promotion that ran afterwards
    left the gate scoring a weaker ``UNKNOWN_UNRESOLVED`` while the same run
    rendered the finding as ``IN_CONTRACT`` with a ``BREAKING`` decision
    (Codex review, confirmed via ``--required-symbol`` under
    ``--contract exports``).

    Deliberately limited to findings that *already carry* a contract
    relevance: that is true exactly when the caller opted into
    ``contract_evaluation=True``, so a run that did not opt in keeps every
    finding unstamped -- and an unstamped finding already gates, per
    ``contract_gating``'s documented default. Synthetic scoped-only findings
    this module creates are likewise left alone here; they gate for the same
    reason, and the later aggregate pass gives them their decision for the
    report.
    """
    from .contract_gating import is_evaluated
    from .contract_scoped_promotion import (
        recompute_verdict_after_promotion,
        stamp_scoped_changes,
    )

    already_classified = [
        c for c in changes if getattr(c, "contract_relevance", None) is not None
    ]
    if not already_classified:
        return
    # Whether this promotion actually moves anything back onto the
    # compatibility axis decides whether the full verdict has gone stale --
    # checked before, since the promotion is what changes the answer.
    promoted_onto_axis = any(not is_evaluated(c) for c in already_classified)
    stamp_scoped_changes(already_classified, policy=policy, policy_file=policy_file)
    if promoted_onto_axis and diff is not None:
        recompute_verdict_after_promotion(
            diff, policy=policy, policy_file=policy_file
        )


def _library_source_graph(
    lib: Path | AbiSnapshot, snapshot: AbiSnapshot | None = None
) -> SourceGraphSummary | None:
    """The L5 source graph for the old library, or ``None``.

    Only an :class:`~abicheck.model.AbiSnapshot` can carry one (``dump
    --sources``/``--build-info``/``--old-sources``, or the always-on
    header-only graph) — a bare library ``Path`` is a real binary this module
    reads an export/version table from, with no graph attached.

    Hence *snapshot*, the ADR-057 follow-up (Codex review, fresh evidence):
    when OLD is a real binary, every caller passes the ``Path`` as *lib* even
    though it is holding the snapshot it just dumped or loaded from that same
    path (``cli_compare_helpers._apply_used_by_scoping``'s
    ``old_input if detect_binary_format(...) else old_snapshot``,
    ``mcp_server``'s identical line, :func:`check_appcompat`'s own ``dump``).
    Reading the graph only off *lib* therefore made the consumer join fire
    **only** when OLD happened to be a saved JSON snapshot — the inverse of
    the primary usage, and it silently skipped exactly the runs that asked for
    the richest evidence (``--old-sources``/``--old-build-info``). *snapshot*
    is consulted first and is for graph lookup only; *lib* keeps owning every
    binary/export/version read, so the two can never disagree about what is
    exported. It must describe the same library as *lib* — at all three call
    sites it is the snapshot of that exact path.
    """
    for candidate in (snapshot, lib):
        build_source = getattr(candidate, "build_source", None)
        if build_source is None:
            continue
        graph: SourceGraphSummary | None = getattr(build_source, "source_graph", None)
        if graph is not None and graph.nodes:
            return graph
    return None


def _consumer_impact_explanations(
    app_path: Path,
    app_reqs: AppRequirements,
    old_lib: Path | AbiSnapshot,
    symbols: list[str],
    old_snapshot: AbiSnapshot | None = None,
) -> tuple[SourceGraphSummary | None, dict[str, ConsumerImpactPath]]:
    """Explain each of *symbols* through the joined consumer/source graph
    (G29 Phase 4, ADR-057).

    The *old* library's graph, not the new one: the symbol is missing from the
    new library by definition, so only the old side still carries the
    declaration and call edges that say why the consumer depended on it.

    Returns ``(joined_graph, {symbol: ConsumerImpactPath})`` — both empty/
    ``None`` whenever no graph is available or nothing could be explained, in
    which case every finding keeps exactly the shape it had before this join
    existed.
    """
    library_graph = _library_source_graph(old_lib, old_snapshot)
    if library_graph is None or not symbols:
        return None, {}
    from .impact.consumer_graph import (
        build_consumer_graph,
        explain_required_symbols,
        join_consumer_graph,
    )

    # No `symbols=` narrowing needed: _scope_app_symbols_to_library already
    # reduced app_reqs.undefined_symbols to what this library actually
    # exports, which is exactly the scoping that parameter exists to apply.
    consumer_graph = build_consumer_graph(app_path.name, app_reqs)
    joined = join_consumer_graph(library_graph, consumer_graph)
    return joined, explain_required_symbols(joined, symbols, consumer=app_path.name)


def _format_consumer_impact(
    explained: ConsumerImpactPath,
    graph: SourceGraphSummary,
    *,
    name_consumer: bool = True,
) -> str:
    """The human-readable half of a consumer impact explanation — the string
    that replaces "requires missing symbol X" with why it was required.

    Reuses ``source_graph_findings._format_dependency_path`` for the chain
    itself rather than formatting edges here, so a consumer proof path reads
    identically to an internal-leak one for the same edges.

    *name_consumer* picks which of the two facts the sentence leads with, and
    the distinction is not cosmetic (ADR-057 D8). "``training-service``
    requires X" is a fact about *one* consumer; "X is reachable from public
    entry ``train``" is a fact about the *library*, true of every consumer and
    of no consumer. The first belongs on the
    ``CONSUMER_REQUIRED_SYMBOL_REMOVED`` overlay, which exists per app; the
    second is what may be written onto a shared library-diff finding that the
    unscoped report also renders.
    """
    entry = explained.public_entries[0] if explained.public_entries else "?"
    if explained.is_direct():
        if name_consumer:
            return f"{explained.consumer} requires public entry {entry} directly"
        return f"{explained.symbol} is declared by public entry {entry}"
    from .buildsource.source_graph_findings import _format_dependency_path

    chain = _format_dependency_path(graph, explained.entry_path)
    if name_consumer:
        return (
            f"{explained.consumer} requires {explained.symbol} "
            f"via public entry {entry}: {chain}"
        )
    return f"{explained.symbol} is reachable from public entry {entry}: {chain}"


def _has_impact_evidence(change: Change) -> bool:
    """Whether *change* already carries reachability/impact evidence from one
    of its own producers (``internal_leak``, ``source_graph_findings``,
    ``post_processing``).

    The guard on enriching a *shared* library-diff finding: those `Change`
    objects are the same ones in ``DiffResult.changes``, so overwriting
    evidence a producer already computed would corrupt the unscoped report,
    and ``attach_impact_metadata`` assigns its whole field set
    unconditionally (``None`` included). It also makes the multi-``--used-by``
    case first-writer-wins and therefore deterministic in app order, rather
    than silently last-writer-wins.

    ``impact_assessment.proof_path is not None`` (a *cached assessment that
    actually carries a proof path*), not merely "a cached assessment
    exists" (Codex review, fresh evidence): since ADR-052 Slice 10,
    ``post_processing.MarkReachability`` caches an ``ImpactAssessment`` on
    *every* change it tags -- including a change it leaves
    ``ReachabilityState.UNKNOWN`` with no proof path at all (an ordinary,
    otherwise-unexplained ``FUNC_REMOVED``), and one it tags
    ``PROVEN_REACHABLE`` via a direct-symbol/public-source-ABI-surface match
    with no walked path either (see that step's own two early-continue
    branches). Treating either of those as "evidence of its own" would skip
    :func:`_enrich_covered_changes` for the exact common case this join
    exists to explain, silently dropping ``affected_public_roots``/
    ``impact_proof_path``/the consumer-neutral prose for a covered removed
    export. Reading ``.proof_path`` instead makes this check equivalent to
    the two flat-field checks below regardless of whether the evidence
    reached this change via the cache or directly.
    """
    assessment = getattr(change, "impact_assessment", None)
    return (
        (assessment is not None and assessment.proof_path is not None)
        or getattr(change, "impact_proof_path", None) is not None
        or getattr(change, "reachability_proof_path", None) is not None
    )


def _enrich_covered_changes(
    changes: list[Change],
    explanations: dict[str, ConsumerImpactPath],
    graph: SourceGraphSummary,
) -> None:
    """Attach the graph explanation to library-diff findings that already
    cover a missing symbol (ADR-057 D8, Codex review).

    Without this the join reached only *uncovered* symbols: an ordinary
    removed export produces its own ``FUNC_REMOVED``, which
    :func:`uncovered_missing_symbols` then excludes from the overlay — so the
    common case, including the internal-dispatcher one this join was built
    for, got no proof path at all.

    These `Change` objects are shared with ``DiffResult.changes``, so the
    prose written here is deliberately consumer-neutral (see
    :func:`_format_consumer_impact`) and only findings with no evidence of
    their own are touched.

    Refreshes ``change.impact_assessment`` after attaching (Codex review,
    fresh evidence): a change reaching this point with a *cached but
    pathless* assessment (``MarkReachability``'s blanket Slice 10 cache —
    the case :func:`_has_impact_evidence` now lets through) still has that
    stale, path-less object sitting on ``change.impact_assessment`` after
    :func:`_attach_consumer_impact` sets the flat proof-path fields —
    ``impact.engine.assess_change`` prefers any non-``None`` cached
    assessment over re-deriving from those flat fields, so without this
    refresh the newly attached consumer explanation would never actually
    reach a JSON/SARIF render. Mirrors the overlay-change path a few lines
    below (``overlay_change.impact_assessment = assess_change(overlay_change)``),
    which already does this correctly for the *uncovered*-symbol case
    because that ``Change`` is always freshly constructed with no
    pre-existing cache to go stale — this shared-``Change`` counterpart
    must clear the stale cache *first*: ``assess_change`` reads
    ``change.impact_assessment`` as its own cache, so recomputing while the
    old object is still assigned would just hand back that same stale
    object unchanged.

    Aggregates *every* matching explanation, not just the first
    (:func:`_merge_consumer_impact_paths`) — a single shared ``Change`` can
    cover more than one missing export at once via ``affected_symbols``
    (e.g. one type-size change breaking several removed functions), and
    :func:`_change_covers_symbol` treats all of them as covered. Keeping
    only the first match's ``next(...)`` pick (Codex review, fresh
    evidence) silently discarded every other symbol's own public root and
    proof path, reporting a narrower explanation than the same logic
    already claims this finding accounts for.
    """
    for change in changes:
        if _has_impact_evidence(change):
            continue
        matches = [
            e for sym, e in explanations.items() if _change_covers_symbol(change, sym)
        ]
        if not matches:
            continue
        explained = _merge_consumer_impact_paths(matches)
        _attach_consumer_impact(change, explained, graph, name_consumer=False)
        change.impact_assessment = None
        change.impact_assessment = assess_change(change)


def _merge_consumer_impact_paths(
    matches: list[ConsumerImpactPath],
) -> ConsumerImpactPath:
    """Combine every symbol-level explanation one shared ``Change`` covers
    (via ``affected_symbols`` naming more than one missing export) into a
    single :class:`~abicheck.impact.consumer_graph.ConsumerImpactPath`,
    instead of silently keeping only the first and discarding the rest
    (Codex review, fresh evidence).

    All of ``consumer``/``symbol``/``entry_path``/the primary (first)
    ``public_entries`` entry come from the **same** match — the one
    :func:`_choose_primary_match` picks — never from independently-chosen
    fields (Codex review, fresh evidence): an earlier version picked
    ``symbol``/``public_entries`` from ``matches[0]`` but ``entry_path``
    from the first match that happened to carry a non-empty (indirect)
    path, which are not necessarily the same match when the first match is
    itself *direct* (``entry_path == []``) and a later one is indirect.
    ``_format_consumer_impact`` reads ``explained.symbol``/
    ``explained.public_entries[0]`` together with ``explained.entry_path``
    as one coherent story ("*symbol* is reachable from public entry
    *entry*: *chain*"); mixing fields from two different matches produced
    an internally contradictory sentence — symbol A "reachable from" A's
    own entry, followed by a chain that actually starts at (and explains)
    a completely different symbol B's entry and target.

    ``public_entries``/``declarations`` are still the order-preserving
    deduped union across every match (so a finding covering several
    requirements reports every public root/declaration that explains it,
    not just the primary's), just with the primary's own values ordered
    first. A *same-rooted* other match's ``entry_path``/
    ``alternative_entry_paths`` is folded into the merged
    ``alternative_entry_paths`` instead of dropped outright, so no
    explanation is lost even though only one match can be primary
    (``attach_impact_metadata`` caps how many of these are actually kept).
    A *differently-rooted* other match's own path is deliberately left out
    (Codex review, fresh evidence) — ``impact.engine._build_alternative_path``
    stamps every merged ``GraphProofPath`` with the SAME single primary
    root, so including a path that actually starts at a different public
    entry would misrepresent it in JSON/SARIF as a variant of the primary's
    own root; that entry is still reported via ``public_entries``, just not
    smuggled in as a same-rooted alternative.

    Deliberately does **not** early-return ``matches[0]`` unchanged when
    ``len(matches) == 1`` (Codex review, fresh evidence): the single-symbol
    case is the *ordinary* one, not a degenerate special case that can skip
    the root filter below.
    :func:`~abicheck.impact.consumer_graph.explain_required_symbols` can
    itself return one ``ConsumerImpactPath`` whose own
    ``alternative_entry_paths`` already mixes candidates from more than one
    consumer-compiled entry (every entry that reaches the target
    declaration, not just the ``select_preferred_graph_path``-chosen one) —
    so a lone match can carry a differently-rooted alternative exactly like
    the primary-match case below, and skipping this function's filtering
    for it would let ``impact.engine._build_proof_path``'s single
    ``affected_public_roots[0]`` mislabel that alternative's root the same
    way an unfiltered multi-match merge would.
    """
    primary = _choose_primary_match(matches)
    ordered = [primary, *(m for m in matches if m is not primary)]
    entries: list[str] = []
    seen_entries: set[str] = set()
    declarations: list[str] = []
    seen_decls: set[str] = set()
    for m in ordered:
        for e in m.public_entries:
            if e not in seen_entries:
                seen_entries.add(e)
                entries.append(e)
        for d in m.declarations:
            if d not in seen_decls:
                seen_decls.add(d)
                declarations.append(d)
    # A non-primary match's own entry_path/alternative_entry_paths is only
    # folded in when it shares the primary's own root (Codex review, fresh
    # evidence): impact.engine._build_alternative_path stamps every merged
    # GraphProofPath.root from the SAME single primary root
    # (impact.engine._build_proof_path's affected_roots[0]) -- it has no
    # per-alternative root field at all. Including a differently-rooted
    # match's own path here would have it serialized in JSON/SARIF as
    # though it started at the primary's entry, when it actually starts at
    # (and explains) a different public entry entirely -- the exact
    # "confident but wrong" shape this module otherwise never allows, so a
    # differently-rooted match's own path is left out of the merged
    # alternatives rather than mislabeled.
    #
    # Compares by the actual graph node id (entry_path[0].src) whenever BOTH
    # sides have a walked path, not by display label (Codex review, fresh
    # evidence): distinct public-entry nodes can share one display label --
    # C++ overloads are the common case -- so a label match alone does not
    # prove m's path starts at the SAME node primary's does. Falls back to
    # the label comparison only when one side has no entry_path at all (a
    # *direct* match, whose declaration-is-the-entry "path" has no edge to
    # read a node id from) -- the label is the only signal available then,
    # same as before this fix.
    primary_root = primary.public_entries[0] if primary.public_entries else None

    def _same_root(m: ConsumerImpactPath) -> bool:
        if m.entry_path and primary.entry_path:
            return m.entry_path[0].src == primary.entry_path[0].src
        return bool(m.public_entries) and m.public_entries[0] == primary_root

    # A non-primary match's OWN alternative_entry_paths need the identical
    # per-alt filter the primary's own alternatives get below (Codex
    # review, fresh evidence): _same_root(m) only proves m's PREFERRED
    # entry_path shares the primary's root -- explain_required_symbols
    # builds m.alternative_entry_paths from every candidate path across
    # every consumer-compiled entry that reached the target, not just the
    # entry m's own preferred path happens to start at, so one of m's own
    # alternatives can start at yet another, third entry. Bulk-extending
    # all of them once m's preferred path passes the root check would
    # still let a differently-rooted path through and have it serialized
    # under the primary's single root. Filtered against m's own verified
    # entry_path[0].src (equal to primary's by construction once same_root
    # is True) rather than re-deriving it from primary directly, so the
    # comparison reads as "does this alt start where m's OWN already-
    # verified path starts" -- the same question the primary-side filter
    # answers about primary's own alternatives.
    alternatives = []
    for m in matches:
        if m is primary:
            continue
        same_root = _same_root(m)
        if m.entry_path and same_root:
            alternatives.append(m.entry_path)
            m_start = m.entry_path[0].src
            alternatives.extend(
                alt for alt in m.alternative_entry_paths if alt and alt[0].src == m_start
            )
    # primary's OWN alternative_entry_paths need the identical guard (Codex
    # review, fresh evidence): explain_required_symbols builds these from
    # every candidate path across every consumer-compiled entry the walk
    # reached, not just ones starting at the SAME entry select_preferred_graph_path
    # chose as primary -- so an entry in here can legitimately start at a
    # different node than primary.entry_path's own first hop. There is no
    # per-alternative root field to compare by label (unlike the
    # cross-match case above), so this compares each alternative's actual
    # first-edge source node against primary.entry_path's own first-edge
    # source directly -- the same underlying question ("does this
    # alternative start where the primary path starts"), answered without
    # needing graph/label access this function doesn't have.
    if primary.entry_path:
        primary_start = primary.entry_path[0].src
        alternatives.extend(
            alt
            for alt in primary.alternative_entry_paths
            if alt and alt[0].src == primary_start
        )
    from .impact.consumer_graph import ConsumerImpactPath as _ConsumerImpactPath

    return _ConsumerImpactPath(
        consumer=primary.consumer,
        symbol=primary.symbol,
        declarations=tuple(declarations),
        public_entries=tuple(entries),
        entry_path=primary.entry_path,
        alternative_entry_paths=alternatives,
    )


def _choose_primary_match(
    matches: list[ConsumerImpactPath],
) -> ConsumerImpactPath:
    """The one match :func:`_merge_consumer_impact_paths` derives
    ``consumer``/``symbol``/``entry_path``/the primary public entry from.

    Prefers the first match that carries an actual walked ``entry_path``
    (an indirect, graph-proven chain) over a direct one — a direct match's
    own ``entry_path`` is ``[]`` by construction (its "path" is the trivial
    zero-hop declaration-is-the-entry case), so preferring it as primary
    when a genuinely indirect match also exists would silently discard the
    only real path this merged explanation could have shown. Falls back to
    the first match when every match is direct — there is no path to
    prefer among them, so display order is the only remaining tiebreaker.
    """
    return next((m for m in matches if m.entry_path), matches[0])


def _attach_consumer_impact(
    change: Change,
    explained: ConsumerImpactPath,
    graph: SourceGraphSummary,
    *,
    name_consumer: bool = True,
) -> None:
    """Attach one :class:`ConsumerImpactPath` to *change* in place.

    Enrichment only — never constructs a finding, never changes a verdict or
    a severity. The overlay call site already stamps ``PROVEN_REACHABLE``/
    ``consumer_proven`` on its freshly-built ``Change`` before calling this
    (see its own comment) — this function stamps the identical three fields
    itself too (Codex review, fresh evidence), a no-op there, but load-bearing
    for :func:`_enrich_covered_changes`'s *shared*-``Change`` caller: that
    change's ``reachability_state``/``public_reachable`` are whatever
    ``MarkReachability`` left them (``UNKNOWN``/``False`` by default when no
    reachability-aware suppression rule is even configured, so that step never
    ran at all) — attaching a concrete proof path here without also raising
    those fields would otherwise leave the *same* internal contradiction the
    overlay's own comment already reasons its way out of: a real consumer
    requirement resolving to a real, walked call chain is not a "maybe
    internal, maybe not" ambiguity, by construction, for either caller.
    """
    from .buildsource.graph_impact import attach_impact_metadata

    attach_impact_metadata(
        change,
        affected_public_roots=list(explained.public_entries),
        path=explained.entry_path,
        graph=graph,
        alternative_paths=explained.alternative_entry_paths,
    )
    change.reachability_proof_path = _format_consumer_impact(
        explained, graph, name_consumer=name_consumer
    )
    change.public_reachable = True
    change.reachability_kind = "consumer_proven"
    # Unconditionally overrides whatever reachability_state MarkReachability
    # (or nothing) already left here, including a prior PROVEN_UNREACHABLE
    # from its own layout/type-graph closure walk (CodeRabbit review): that
    # walk reasons about *potential* reachability from the public API
    # surface as the library's own source declares it, while this evidence
    # is a real --used-by consumer binary's own undefined-symbol resolution
    # -- an empirically stronger, more direct signal than a structural
    # closure inference, and this function only ever runs when that real
    # requirement was found. Deliberately not treated as a recorded
    # conflict between two disagreeing verdicts: an actual observed
    # dependency is not "in tension" with an inference about the surface
    # that dependency turned out to route around (an alias, a compiler
    # intrinsic call, or any other path the closure walk doesn't model) --
    # it settles the question the walk could only approximate.
    change.reachability_state = ReachabilityState.PROVEN_REACHABLE


def scope_diff_to_app(
    diff: DiffResult,
    app_path: Path,
    old_lib: Path | AbiSnapshot,
    new_lib: Path | AbiSnapshot,
    *,
    policy: str = "strict_abi",
    policy_file: PolicyFile | None = None,
    suppression: SuppressionList | None = None,
    old_snapshot: AbiSnapshot | None = None,
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

    *suppression* (ADR-044 P2, Codex review): the same rule set already used
    to compute *diff* — evaluated again here because
    :data:`ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED` is synthesized fresh
    from ``missing_symbols`` *after* the pipeline's own suppression pass has
    already run, so it would otherwise be unsuppressible even by an exact
    ``symbol:``/``change_kind:`` rule that matches it precisely.

    *old_snapshot* (ADR-057) is used **only** to find the old library's L5
    source graph for the consumer-impact join, and only matters when *old_lib*
    is a real binary ``Path``: a caller that resolved OLD to both a path and a
    snapshot should pass the snapshot here so the join can explain *why* a
    consumer required a removed symbol. It never affects which symbols,
    exports, or versions are read — see :func:`_library_source_graph`.
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
    # Compute coverage from the raw (pre-suppression) missing count -- it is
    # an objective fact about the export table, not a gate, so a suppressed
    # overlay finding should not make the reported coverage number lie.
    required_count = len(app_reqs.undefined_symbols)
    coverage = _compute_symbol_coverage(new_exports, required_count, len(missing_symbols))

    # ADR-067 C-S1: the consumer overlay is the one recording call site that
    # runs *after* `compare()` closed the ledger, so it resolves the diff's own
    # ledger once here and attaches it when the diff carries none (a caller
    # that built the DiffResult itself). `ledger_for` deliberately never
    # attaches on its own -- a report projection must not mutate what it
    # renders -- so this engine-side assignment is what makes the overlay's
    # records reach the same object every projection reads.
    overlay_ledger = ledger_for(diff)
    if getattr(diff, "disposition_ledger", None) is None:
        diff.disposition_ledger = overlay_ledger

    suppressed_missing: set[str] = set()
    uncovered = list(uncovered_missing_symbols(missing_symbols, breaking_for_app))
    # G29 Phase 4 (ADR-057): one joined consumer/source-graph walk for every
    # uncovered symbol, computed before the loop so the restricted BFS runs
    # once per comparison rather than once per missing symbol. Both values are
    # empty when no L5 graph is available on the old side, which is the
    # unchanged pre-ADR-057 behavior.
    # Every missing symbol, not just the uncovered ones: a removed export
    # normally produces its own FUNC_REMOVED, which uncovered_missing_symbols
    # then excludes from the overlay below -- so scoping the walk to
    # `uncovered` left the common case (the internal-dispatcher one this join
    # was built for) with no explanation at all (ADR-057 D8, Codex review).
    joined_graph, consumer_impact = _consumer_impact_explanations(
        app_path, app_reqs, old_lib, missing_symbols, old_snapshot
    )
    for sym in uncovered:
        # public_reachable=True (Codex review, fresh evidence): this overlay
        # only ever exists because a real --used-by consumer binary's own
        # undefined-symbol requirement genuinely resolved to nothing in the
        # new library -- there is no "maybe internal, maybe not" ambiguity
        # the way there is for an ordinary internal-namespace Change. Left at
        # its dataclass default (False), a broad namespace/source_location
        # suppression rule's default "unreachable-only" reachability would
        # read it as unreachable and silently suppress a break that is, by
        # construction, always consumer-proven real.
        overlay_change = make_change(
            ChangeKind.CONSUMER_REQUIRED_SYMBOL_REMOVED,
            symbol=sym,
            name=app_path.name,
            public_reachable=True,
            reachability_kind="consumer_proven",
            reachability_state=ReachabilityState.PROVEN_REACHABLE,
        )
        # Attached *before* assess_change below, not after: the cached
        # assessment is built from these very fields, so enriching afterwards
        # would leave the cache describing the pre-enrichment change and the
        # proof path would never reach the report.
        explained = consumer_impact.get(sym)
        if explained is not None and joined_graph is not None:
            _attach_consumer_impact(overlay_change, explained, joined_graph)
        # ADR-052 D2 follow-up (G29 Phase 3, scoped implementation): cache
        # this overlay's ImpactAssessment right away. Safe here because
        # suppression.evaluate() below is a pure read of change's fields
        # (Suppression.matches/would_withhold/would_withhold_unknown_reachability
        # never assign to *change*) -- nothing between this point and the
        # cache read in impact.engine.assess_change touches the evidence
        # fields just set above.
        overlay_change.impact_assessment = assess_change(overlay_change)
        if suppression is None:
            record_kept_change(
                overlay_ledger,
                overlay_change,
                diff,
                application_point="consumer_overlay",
            )
            breaking_for_app.append(overlay_change)
            continue
        # evaluate() (not the cheaper is_suppressed) so a broad rule whose
        # selectors matched but was withheld by the reachability/
        # allow_public_break gate still emits the same
        # SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK diagnostic ApplySuppression
        # produces for changes it sees directly (mirrors checker.py's
        # _filter_suppressed_changes / post_processing.py's
        # _merge_findings_respecting_suppression).
        outcome = suppression.evaluate(overlay_change)
        if outcome.suppressed:
            # Codex review (fresh evidence): missing_symbols independently
            # forces Verdict.BREAKING in _compute_appcompat_verdict below
            # regardless of breaking_for_app, and feeds the scoped exit-code
            # floor / missing-label text output the same way -- dropping only
            # the synthesized Change left the suppression cosmetic. This
            # overlay IS the suppressible representation of a missing symbol
            # (that was the point of promoting it out of a bespoke string),
            # so a suppressed overlay must also remove its raw string from
            # every one of those consumers.
            # ADR-067 C-S1's fourth application point. This overlay's *input*
            # shape is a raw ``missing_symbols`` string rather than a detected
            # change, but what suppression acts on here is a real ``Change``,
            # so it records through the identical primitive the library-diff
            # points use -- one record type, one query surface (D2), with the
            # consumer overlay named as its own application point.
            record_suppressed_change(
                overlay_ledger,
                overlay_change,
                rule=outcome.matched_rule,
                application_point="consumer_overlay",
                suppression=suppression,
            )
            suppressed_missing.add(sym)
            continue
        # Recorded on *both* branches, not only when a rule fires (ADR-067
        # D1): the overlay is an atomically detected consumer finding either
        # way, so recording it only when suppressed would make adding a
        # matching rule change the *detected* total rather than move the
        # finding between dispositions -- exactly the conservation the audit
        # exists to make checkable.
        record_kept_change(
            overlay_ledger,
            overlay_change,
            diff,
            application_point="consumer_overlay",
        )
        breaking_for_app.append(overlay_change)
        # outcome.withheld_unknown_rule is never set here: overlay_change is
        # always constructed with reachability_state=PROVEN_REACHABLE above
        # (it is by construction consumer-proven), and
        # would_withhold_unknown_reachability only ever fires on UNKNOWN.
        if outcome.withheld_rule is not None:
            from .post_processing import _build_suppression_overreach_change

            breaking_for_app.append(
                _build_suppression_overreach_change(overlay_change, outcome.withheld_rule)
            )
    # After the overlay loop: an overlay already carries its own (consumer-
    # named) explanation and a cached ImpactAssessment, so _has_impact_evidence
    # skips it here and only the shared library-diff findings are considered.
    if joined_graph is not None and consumer_impact:
        _enrich_covered_changes(breaking_for_app, consumer_impact, joined_graph)

    if suppressed_missing:
        missing_symbols = [s for s in missing_symbols if s not in suppressed_missing]

    verdict = _compute_appcompat_verdict(
        missing_symbols, missing_versions, breaking_for_app,
        required_count, policy, policy_file,
    )
    _promote_scoped_contract(
        breaking_for_app, policy=policy, policy_file=policy_file, diff=diff
    )

    # ADR-067: the ledger is *not* closed here, deliberately. This function
    # runs once per `--used-by` consumer, and `apply_scope` only ever
    # demotes -- so closing per consumer would intersect the consumers'
    # relevant sets instead of unioning them, and a finding only the second
    # consumer uses would be excluded by the first one's call. The whole
    # scoped gate is resolved by `cli_helpers_compare._apply_used_by_scoping`
    # (and its `--required-symbol` sibling), which owns the union and makes
    # the single `close_consumer_scope` call.

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
    from .dumper_clang_streaming import suppress_streaming_prune

    # Resolve per-side headers: old_headers/new_headers override shared headers
    _old_h = old_headers if old_headers is not None else (headers or [])
    _new_h = new_headers if new_headers is not None else (headers or [])
    _old_inc = old_includes if old_includes is not None else (includes or [])
    _new_inc = new_includes if new_includes is not None else (includes or [])

    # public-header set for provenance tagging (ADR-024/ADR-015). Neither
    # call below goes through `service.run_dump`'s dependency-scope wrapper,
    # so there is no later post-hoc filter to retain what the opt-in
    # streaming pruner would drop -- suppressed here the same way a
    # full/unscoped request is elsewhere (Codex review, PR #840, thread
    # bdSMk).
    with suppress_streaming_prune():
        old_snap = dump(
            so_path=old_lib_path,
            headers=_old_h,
            extra_includes=_old_inc,
            version=old_version,
            compiler="c++" if lang == "c++" else "cc",
            lang="c" if lang == "c" else None,
            public_headers=list(_old_h),
            # _old_inc/_new_inc are this caller's own genuine, explicit -I
            # list (never auto-derived) -- mirror perform_elf_dump's/
            # _dump_elf's own public_include_search_dirs wiring so a
            # declaration reached through an explicit include root is
            # promoted to PUBLIC_HEADER here too, not just on the CLI's
            # compare/dump/scan frontends.
            public_include_search_dirs=list(_old_inc),
        )
        new_snap = dump(
            so_path=new_lib_path,
            headers=_new_h,
            extra_includes=_new_inc,
            version=new_version,
            compiler="c++" if lang == "c++" else "cc",
            lang="c" if lang == "c" else None,
            public_headers=list(_new_h),
            public_include_search_dirs=list(_new_inc),
        )

    # Route through the Tier-2 service (lazy import avoids a
    # service→cli→appcompat import cycle); ADR-037 D1.
    from .service import compare_snapshots
    diff = compare_snapshots(old_snap, new_snap, suppression=suppression, policy=policy, policy_file=policy_file, scope_to_public_surface=scope_to_public_surface)

    scoped = scope_diff_to_app(
        diff, app_path, old_lib_path, new_lib_path,
        policy=policy, policy_file=policy_file, suppression=suppression,
        # ADR-057: old_lib_path is a real binary, so the graph the dump above
        # already attached is only reachable through the snapshot itself.
        old_snapshot=old_snap,
    )
    # ADR-067: `scope_diff_to_app` leaves the ledger open (the `--used-by`
    # path calls it once per consumer and only the orchestrator knows the
    # union); this entry point *is* that orchestrator for its single consumer,
    # so the one closing call is here. `also_detected` is the whole relevant
    # set rather than a finding-id-filtered one: it holds the very
    # `diff.changes` objects, so `record`'s identity keying no-ops on those,
    # newly recording only the scoped-only findings.
    relevant = scoped.breaking_for_app
    close_consumer_scope(
        ledger_for(diff), diff, gating=relevant, also_detected=relevant
    )
    return scoped


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
    _promote_scoped_contract(
        breaking_for_host, policy=policy, policy_file=policy_file, diff=diff
    )

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
