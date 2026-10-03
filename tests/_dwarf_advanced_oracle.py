"""Standalone advanced-DWARF parse: the separate-open path the unified
``dwarf_unified.parse_dwarf`` replaced, kept as the comparison side of the
"unified equals separate" and session-reuse timing tests.

It was ``abicheck.dwarf_advanced.parse_advanced_dwarf`` until production
stopped calling it (dead-code plan, Stage D). It opens the ELF on its own and
walks the CUs with a fresh ``AdvancedDwarfMetadata``, so it shares no
``DwarfSession`` or DIE cache with the unified pass it is compared against.
"""

from __future__ import annotations

from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from abicheck.dwarf_advanced import (
    AdvancedDwarfMetadata,
    _normalize_arch,
    _process_cu,
)
from abicheck.dwarf_utils import has_real_dwarf_info
from abicheck.extract import dwarf_subtree_index as _dsi


def parse_advanced_dwarf_separately(so_path: Path) -> AdvancedDwarfMetadata:
    """Advanced DWARF facts of *so_path* from its own ELF open."""
    try:
        with open(so_path, "rb") as f:
            elf = ELFFile(f)  # type: ignore[no-untyped-call]
            if not has_real_dwarf_info(elf):
                return AdvancedDwarfMetadata()
            meta = AdvancedDwarfMetadata(has_dwarf=True)
            meta.target_arch = _normalize_arch(elf)
            dwarf = _dsi.open_indexed_dwarf_info(elf)
            for cu in dwarf.iter_CUs():
                try:
                    _process_cu(cu, meta)
                except (ELFError, OSError, ValueError, KeyError):
                    continue
            return meta
    except (ELFError, OSError, ValueError):
        return AdvancedDwarfMetadata()
