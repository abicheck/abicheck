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

"""Read the export-side facts consumer scoping needs from a library (ADR-005).

A library is either a real binary ``Path`` or an already-loaded
:class:`~abicheck.model.AbiSnapshot`: a saved snapshot's ``elf``/``pe``/
``macho`` fields already carry the SONAME, export table, ELF version list
and PE ordinal table, so a dump-then-compare-later workflow needs no real
OLD/NEW binary for these lookups (ADR-043 follow-up). A raw ``Path`` is
parsed once.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ..model import AbiSnapshot
from ..model.availability import FactStatus
from ..model.consumer_requirements import LibraryExportFacts
from ..model.name_decoration import elf_version
from ..model.surface_facts import is_binary_exported
from .consumer_imports import detect_binary_file_format

if TYPE_CHECKING:
    from ..elf_metadata import ElfMetadata
    from ..macho_metadata import MachoMetadata
    from ..pe_metadata import PeMetadata

log = logging.getLogger(__name__)


def _library_format(lib: Path | AbiSnapshot) -> str | None:
    """*lib*'s binary format, from a snapshot's populated field or a raw file."""
    if isinstance(lib, AbiSnapshot):
        if lib.elf is not None:
            return "elf"
        if lib.pe is not None:
            return "pe"
        if lib.macho is not None:
            return "macho"
        return None
    return detect_binary_file_format(lib)


def _platform_metadata(
    lib: Path | AbiSnapshot, fmt: str | None
) -> tuple[ElfMetadata | None, PeMetadata | None, MachoMetadata | None]:
    """A snapshot's stored metadata, or the one matching parse of a binary."""
    if isinstance(lib, AbiSnapshot):
        return lib.elf, lib.pe, lib.macho
    if fmt == "elf":
        from ..elf_metadata import parse_elf_metadata

        return parse_elf_metadata(lib), None, None
    if fmt == "pe":
        from ..pe_metadata import parse_pe_metadata

        return None, parse_pe_metadata(lib), None
    if fmt == "macho":
        from ..macho_metadata import parse_macho_metadata

        return None, None, parse_macho_metadata(lib)
    return None, None, None


def _export_names(
    fmt: str | None,
    elf_meta: ElfMetadata | None,
    pe_meta: PeMetadata | None,
    macho_meta: MachoMetadata | None,
) -> frozenset[str]:
    """Every exported name, through the shared ``model.export_index`` projections.

    Deliberately :func:`~abicheck.model.export_index.all_export_names`, not the
    default-version-only projection: an application's versioned reference
    (``foo@LIB_1``) binds to a non-default alias, and requirements record
    required versions per library, not per symbol, so dropping aliases here
    would report a still-resolvable symbol as missing.
    """
    from ..model.export_index import (
        all_export_names,
        build_raw_export_index_from_elf,
        build_raw_export_index_from_macho,
        build_raw_export_index_from_pe,
    )

    index = None
    if fmt == "elf" and elf_meta is not None:
        index = build_raw_export_index_from_elf(elf_meta)
    elif fmt == "pe" and pe_meta is not None:
        index = build_raw_export_index_from_pe(pe_meta)
    elif fmt == "macho" and macho_meta is not None:
        index = build_raw_export_index_from_macho(macho_meta)
    return frozenset(all_export_names(index)) if index is not None else frozenset()


def read_library_export_facts(lib: Path | AbiSnapshot) -> LibraryExportFacts:
    """Read *lib*'s SONAME, exports, ELF versions and PE ordinal table."""
    fmt = _library_format(lib)
    if isinstance(lib, AbiSnapshot):
        label, name = lib.library, lib.library
    else:
        label, name = str(lib), lib.name
    if fmt is None:
        return LibraryExportFacts(
            label=label,
            binary_format=None,
            soname=name,
            export_names=frozenset(),
            status=FactStatus.FAILED,
            failure_reason=f"unrecognised library format: {label}",
        )
    elf_meta, pe_meta, macho_meta = _platform_metadata(lib, fmt)
    soname = name
    if fmt == "elf" and elf_meta is not None:
        soname = elf_meta.soname or name
    elif fmt == "macho" and macho_meta is not None:
        soname = macho_meta.install_name or name
    return LibraryExportFacts(
        label=label,
        binary_format=fmt,
        soname=soname,
        export_names=_export_names(fmt, elf_meta, pe_meta, macho_meta),
        unversioned_exports=(
            None
            if elf_meta is None
            else frozenset(
                elf_version.unversioned_name(s.name) for s in elf_meta.symbols
            )
        ),
        versions_defined=(
            None if elf_meta is None else frozenset(elf_meta.versions_defined)
        ),
        exports_by_ordinal=(
            None if pe_meta is None else {e.ordinal: e.name for e in pe_meta.exports}
        ),
    )


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


def dlsym_export_names(snap: AbiSnapshot) -> frozenset[str]:
    """Linker-symbol names a host could resolve from a plugin via ``dlsym``.

    Exported functions and variables, keyed by their mangled (linker) symbol —
    plus the plain source name only for ``extern "C"`` / C symbols where it
    equals the mangled name. A demangled C++ name is deliberately excluded so a
    contract listing it is reported as *missing*, matching ``dlsym`` reality.

    Both header/DWARF-aware and symbols-only exports count: running
    ``plugin-check`` on real stripped binaries without headers is the common
    case, and there every entry is an export-table one.

    Keyed on the *export* fact alone (``is_binary_exported``), never on
    public-contract membership: ``dlsym`` resolves what the export table
    carries and nothing else, so a declaration the headers promise but the
    artifact does not export (a public inline function, or one a version
    script stopped exporting) must read as *missing* here -- counting it
    would let a required entrypoint no consumer can bind to pass as
    satisfied (Codex review, P1). See ``model/surface_facts.py``.
    """
    names: set[str] = set()
    for fn in snap.declarations.functions:
        if is_binary_exported(fn):
            names |= _resolvable_symbol_names(fn.name, fn.mangled)
    for var in snap.declarations.variables:
        if is_binary_exported(var):
            names |= _resolvable_symbol_names(var.name, getattr(var, "mangled", None))
    return frozenset(names)
