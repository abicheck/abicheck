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

"""Read what an application binary imports from one library (ADR-005).

Produces :class:`~abicheck.model.consumer_requirements.ConsumerImportFacts`
from an ELF, PE, or Mach-O executable: its needed libraries, the undefined
symbols it resolves from the target library, and (ELF) the version tags it
requires from it. An unrecognised format or a parser error is reported as a
``FAILED`` fact with a reason, never as a bare empty requirement set.
Evaluating those requirements is ``policy.consumer_requirements``' job.
"""

from __future__ import annotations

import logging
import os
import stat
import struct
from pathlib import Path

from ..model.availability import FactStatus
from ..model.consumer_requirements import (
    PE_ORDINAL_IMPORT_PREFIX,
    AppRequirements,
    ConsumerImportFacts,
)

log = logging.getLogger(__name__)


def detect_binary_file_format(app_path: Path) -> str | None:
    """Detect binary format of an application: 'elf', 'pe', or 'macho'.

    Includes an ``S_ISREG`` guard (application paths may be symlinks or
    pipes) and reads the magic bytes from the same open file descriptor
    to avoid a TOCTOU race.
    """
    from ..binary_utils import classify_magic

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
    elf: object,
    reqs: AppRequirements,
    library_soname: str,
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
    from ..elf_metadata import _guess_symbol_origin

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
    app_path: Path,
    library_soname: str,
) -> tuple[AppRequirements, str | None]:
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
            _collect_undefined_symbols(
                elf, reqs, library_soname, ver_idx_to_lib, versym_section
            )

    except (ELFError, OSError, ValueError) as exc:
        log.warning("Failed to parse ELF app requirements from %s: %s", app_path, exc)
        return reqs, f"ELF import table unreadable: {exc}"

    return reqs, None


# ---------------------------------------------------------------------------
# PE: parse app requirements
# ---------------------------------------------------------------------------


def _parse_pe_app_requirements(
    app_path: Path,
    library_name: str,
) -> tuple[AppRequirements, str | None]:
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
                    dll_name = (
                        entry.dll.decode("utf-8", errors="replace") if entry.dll else ""
                    )
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
                            reqs.undefined_symbols.add(
                                f"{PE_ORDINAL_IMPORT_PREFIX}{imp.ordinal}"
                            )
        finally:
            pe.close()

    except Exception as exc:  # noqa: BLE001
        log.warning("Failed to parse PE app requirements from %s: %s", app_path, exc)
        return reqs, f"PE import table unreadable: {exc}"

    return reqs, None


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
        if (
            lib.lower() == lib_lower
            or os.path.basename(lib).lower() == lib_lower
            or lib_lower in lib.lower()
        ):
            return idx
    return None


def _collect_macho_undefined_symbols(
    macho: object,
    header: object,
    reqs: AppRequirements,
    target_ordinal: int | None,
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
    app_path: Path,
    library_name: str,
) -> tuple[AppRequirements, str | None]:
    """Extract app requirements for a specific dylib from a Mach-O binary."""
    from macholib.mach_o import LC_LOAD_DYLIB
    from macholib.MachO import MachO

    reqs = AppRequirements()

    try:
        macho = MachO(str(app_path))
        if not macho.headers:
            return reqs, "Mach-O file has no headers"

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
            return reqs, f"Mach-O symbol table unreadable: {exc}"

    except (OSError, ValueError, struct.error) as exc:
        log.warning(
            "Failed to parse Mach-O app requirements from %s: %s", app_path, exc
        )
        return reqs, f"Mach-O load commands unreadable: {exc}"

    return reqs, None


_PARSERS = {
    "elf": _parse_elf_app_requirements,
    "pe": _parse_pe_app_requirements,
    "macho": _parse_macho_app_requirements,
}


def read_consumer_imports(app_path: Path, library_name: str) -> ConsumerImportFacts:
    """Read *app_path*'s imports from the library named *library_name*.

    *library_name* is the SONAME/DLL name/dylib install name to filter by
    (empty: keep every import). Never raises for an unreadable file: the
    returned fact carries ``binary_format=None``/``FAILED`` instead, and the
    caller decides whether that is fatal.
    """
    fmt = detect_binary_file_format(app_path)
    if fmt is None:
        return ConsumerImportFacts(
            path=app_path,
            binary_format=None,
            target_library=library_name,
            requirements=AppRequirements(),
            status=FactStatus.FAILED,
            failure_reason=(
                f"Cannot detect binary format of '{app_path}'. "
                "Expected: ELF, PE, or Mach-O executable."
            ),
        )
    reqs, failure = _PARSERS[fmt](app_path, library_name)
    return ConsumerImportFacts(
        path=app_path,
        binary_format=fmt,
        target_library=library_name,
        requirements=reqs,
        status=FactStatus.PRESENT if failure is None else FactStatus.FAILED,
        failure_reason=failure,
    )
