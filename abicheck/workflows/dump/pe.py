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

"""PE primary extraction: the ``pe`` binary-format adapter's body.

Moved verbatim from ``service_dump_native_pe._dump_pe`` (lane B, stage B1b;
that module is deleted). :class:`abicheck.workflows.dump.formats.PeAdapter`
calls :func:`extract_pe`.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ...errors import SnapshotError, ValidationError
from ...extract.surface_fact_producers import export_table_surface_facts
from ...model import AbiSnapshot, EnumType, Function, RecordType, Visibility
from ..snapshot_factory import new_snapshot
from .scoped import try_header_scoped_dump

if TYPE_CHECKING:
    from ...compile_context import CompileContext
    from ...dwarf_advanced import AdvancedDwarfMetadata
    from ...dwarf_metadata import DwarfMetadata

_logger = logging.getLogger("abicheck.service")


def extract_pdb_debug(
    path: Path, pdb_path: Path | None
) -> tuple[DwarfMetadata | None, AdvancedDwarfMetadata | None]:
    """Locate and parse a PDB for *path*.

    Returns ``(dwarf_meta, dwarf_adv)`` or ``(None, None)`` when no PDB is found
    or parsing fails.  PDB extraction is best-effort and never fatal.
    """
    try:
        from ...pdb_metadata import parse_pdb_debug_info
        from ...pdb_utils import locate_pdb

        pdb_file = locate_pdb(path, pdb_path_override=pdb_path, allow_network=False)
        if pdb_file is not None:
            meta, adv = parse_pdb_debug_info(pdb_file)
            _logger.info("PDB debug info loaded from %s", pdb_file)
            return meta, adv
        _logger.debug("No PDB file found for %s", path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("PDB parsing failed for %s: %s", path, exc)
    return None, None


def extract_pe(
    path: Path,
    version: str,
    *,
    headers: list[Path] | None = None,
    includes: list[Path] | None = None,
    lang: str = "c++",
    lang_explicit: bool = False,
    pdb_path: Path | None = None,
    header_backend: str = "auto",
    compile: CompileContext | None = None,
    public_headers: list[Path] | None = None,
    public_header_dirs: list[Path] | None = None,
    include_labels: dict[Path, str] | None = None,
) -> AbiSnapshot:
    """Dump a PE binary (Windows DLL) to an ABI snapshot.

    When *headers* are supplied the ABI surface is scoped to declarations in
    those public headers via castxml (mirroring ``abidw --headers-dir``).  If
    castxml is unavailable or no header declaration matches an exported symbol,
    scoping is skipped (with a warning) and the full export table is used.
    """
    from ...pe_metadata import parse_pe_metadata

    try:
        pe_meta = parse_pe_metadata(path)
    except ImportError as exc:
        raise SnapshotError(str(exc)) from exc
    except (RuntimeError, OSError, ValueError) as exc:
        raise SnapshotError(f"Failed to parse PE '{path}': {exc}") from exc

    if not pe_meta.machine:
        raise SnapshotError(
            f"Failed to extract PE metadata from '{path}'. "
            "The file may be corrupt or not a valid PE binary."
        )
    if not pe_meta.exports:
        raise ValidationError(
            f"PE file '{path}' has no exports (named or ordinal). "
            "Verify the file is a valid DLL."
        )

    dwarf_meta, dwarf_adv = extract_pdb_debug(path, pdb_path)

    scope_fallback: str | None = None
    if headers:
        scoped, scope_fallback = try_header_scoped_dump(
            "pe",
            path,
            headers,
            includes or [],
            version,
            lang,
            lang_explicit=lang_explicit,
            header_backend=header_backend,
            compile=compile,
            public_headers=public_headers,
            public_header_dirs=public_header_dirs,
            include_labels=include_labels,
        )
        if scoped is not None:
            # Preserve any PDB debug info alongside the header-scoped surface.
            if dwarf_meta is not None:
                scoped.declarations.debug_layout = dwarf_meta
                scoped.declarations.debug_advanced = dwarf_adv
            return scoped

    funcs = [
        Function(
            name=(exp.name or f"ordinal:{exp.ordinal}"),
            mangled=(exp.name or f"ordinal:{exp.ordinal}"),
            return_type="?",
            visibility=Visibility.PUBLIC,
            # An export-table entry: (c) confirmed, (a)/(b) never looked at
            # on this path -- see model/surface_facts.py.
            **export_table_surface_facts(),
            is_extern_c=not (exp.name or "").startswith("?"),
        )
        for exp in pe_meta.exports
    ]

    # ADR-024 Phase 1 (PDB provenance): when header scoping was requested but
    # castxml could not resolve a surface (commonly the MSVC C++-mangling
    # gap), recover declared types -- with their defining source header --
    # from PDB debug info, so public-header scoping still has a provenance
    # signal to classify against. Bounded so default PE diffs are unaffected.
    pdb_types: list[RecordType] = []
    pdb_enums: list[EnumType] = []
    pdb_ir = None
    if headers and dwarf_meta is not None:
        from ...pdb_model import model_types_from_dwarf_metadata, pdb_semantic_ir

        pdb_types, pdb_enums = model_types_from_dwarf_metadata(dwarf_meta)
        if pdb_types or pdb_enums:
            pdb_ir = pdb_semantic_ir(pdb_types, pdb_enums)

    return new_snapshot(
        library=path.name,
        version=version,
        functions=funcs,
        types=pdb_types,
        enums=pdb_enums,
        pe=pe_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        platform="pe",
        scope_fallback=scope_fallback,
        semantic_ir=pdb_ir,
    )
