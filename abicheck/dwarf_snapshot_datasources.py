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

"""``show_data_sources`` — the human-readable L0-L5 data-source diagnostic.

Split out of ``dwarf_snapshot.py`` to stay under its line-count cap (ADR-063
Phase 0's detector migration pushed it over) -- a genuine leaf module (no
dependency on ``_DwarfSnapshotBuilder`` or anything else in that module).
``dwarf_snapshot.py`` re-exports ``show_data_sources`` (`as`-aliased) so
every existing ``from abicheck.dwarf_snapshot import show_data_sources``
call site (``cli_datasources.py``, ``cli_dump_helpers.py``,
``workflows/extraction.py``, and their tests) is unaffected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from .buildsource.pack import BuildSourcePack
    from .dwarf_metadata import DwarfMetadata
    from .elf_metadata import ElfMetadata


def show_data_sources(
    elf_path: Path,
    elf_meta: ElfMetadata | None,
    dwarf_meta: DwarfMetadata | None,
    has_headers: bool,
    build_source_pack: BuildSourcePack | None = None,
) -> str:
    """Generate human-readable data source diagnostic output.

    Returns a multi-line string describing which data layers are available.
    """
    lines: list[str] = [f"Data sources for {elf_path.name}:"]

    # L0: Binary metadata
    if elf_meta is not None:
        soname = elf_meta.soname or "none"
        n_syms = len(elf_meta.symbols) if elf_meta.symbols else 0
        lines.append(
            f"  L0 Binary metadata: ELF (SONAME={soname}, {n_syms} exported symbols)"
        )
    else:
        lines.append("  L0 Binary metadata: not available")

    # L1: Debug info
    if dwarf_meta is not None and dwarf_meta.has_dwarf:
        n_types = len(dwarf_meta.structs)
        n_enums = len(dwarf_meta.enums)
        lines.append(f"  L1 Debug info:      DWARF ({n_types} types, {n_enums} enums)")
    else:
        lines.append("  L1 Debug info:      not available (no DWARF)")

    # L2: Header AST
    if has_headers:
        lines.append("  L2 Header AST:      available (CastXML/header inputs)")
    else:
        lines.append("  L2 Header AST:      not collected (no -H provided)")

    # L3-L5: Optional build/source pack layers.
    if build_source_pack is None:
        lines.append("  L3 Build context:   not collected (no build-source pack)")
        lines.append("  L4 Source ABI:      not collected (no build-source pack)")
        lines.append("  L5 Source graph:    not collected (no build-source pack)")
    else:
        lines.append(
            _evidence_layer_line(
                build_source_pack, "L3 Build context", "build_evidence"
            )
        )
        lines.append(
            _evidence_layer_line(build_source_pack, "L4 Source ABI", "source_abi")
        )
        lines.append(
            _evidence_layer_line(build_source_pack, "L5 Source graph", "source_graph")
        )

    lines.append("")

    # Mode determination
    if has_headers:
        lines.append("Using: Headers mode (artifact + public header evidence)")
    elif dwarf_meta is not None and dwarf_meta.has_dwarf:
        lines.append("Using: DWARF-only mode (artifact debug evidence)")
        lines.append(
            "Missing: #define constants, default parameter values, header intent"
        )
    else:
        lines.append("Using: Symbols-only mode (artifact symbol evidence)")
        lines.append("Missing: type information, function signatures")

    return "\n".join(lines)


def _evidence_layer_line(
    build_source_pack: BuildSourcePack, label: str, attr: str
) -> str:
    coverage = {
        row.layer: row
        for row in getattr(getattr(build_source_pack, "manifest", None), "coverage", [])
    }
    layer_id = {
        "build_evidence": "L3_build",
        "source_abi": "L4_source_abi",
        "source_graph": "L5_source_graph",
    }[attr]
    row = coverage.get(layer_id)
    payload = getattr(build_source_pack, attr, None)
    payload_summary = (
        _evidence_payload_summary(payload) if payload is not None else None
    )
    if row is not None:
        if row.status.value != "present" or payload_summary is None:
            return f"  {label}: {_coverage_row_summary(row)}"
        return f"  {label}: {payload_summary}"
    if payload_summary is not None:
        return f"  {label}: {payload_summary}"
    return f"  {label}: not collected"


def _coverage_row_summary(row: object) -> str:
    status = getattr(getattr(row, "status", None), "value", None) or str(
        getattr(row, "status", "not_collected")
    )
    detail = getattr(row, "detail", "")
    suffix = f" ({detail})" if detail else ""
    return f"{status}{suffix}"


def _evidence_payload_summary(payload: object) -> str | None:
    counts: list[str] = []
    for attr, label in (
        ("compile_units", "compile units"),
        ("targets", "targets"),
        ("reachable_declarations", "declarations"),
        ("reachable_types", "types"),
        ("reachable_macros", "macros"),
        ("nodes", "nodes"),
        ("edges", "edges"),
    ):
        items = getattr(payload, attr, None)
        if items:
            counts.append(f"{len(items)} {label}")
    if counts:
        return "present (" + ", ".join(counts[:3]) + ")"
    return None
