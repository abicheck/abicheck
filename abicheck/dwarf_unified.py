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

"""dwarf_unified.py — single-pass DWARF extraction.

Combines the work of ``dwarf_metadata.parse_dwarf_metadata`` and
``dwarf_advanced.parse_advanced_dwarf`` into one ELF open + one CU
iteration, cutting file I/O and CU-header parsing overhead roughly in half.
Note: each module still performs its own DIE-tree walk per CU; a unified
DIE walker (further ~30-40% CPU gain) is a planned follow-up.

Public API
----------
parse_dwarf(so_path) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]
    Single entry point used by dumper.dump().

Backward-compatible shims (used by existing callers / tests):
    parse_dwarf_metadata(so_path) -> DwarfMetadata
    parse_advanced_dwarf(so_path) -> AdvancedDwarfMetadata

The two legacy modules (dwarf_metadata.py, dwarf_advanced.py) keep their
internal helpers unchanged and are re-exported here so no import sites
outside dumper.py need updating.
"""
# pylint: disable=invalid-name  # CU is the standard DWARF term (Compilation Unit)
from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .dwarf_advanced import AdvancedDwarfMetadata, _normalize_arch
from .dwarf_advanced import _process_cu_impl as _adv_process_cu
from .dwarf_metadata import DwarfMetadata
from .dwarf_metadata import _process_cu_impl as _meta_process_cu
from .dwarf_utils import has_real_dwarf_info

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unified single-pass entry point
# ---------------------------------------------------------------------------

def parse_dwarf(so_path: Path) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Open *so_path* once and extract both DwarfMetadata and AdvancedDwarfMetadata.

    Replaces two separate calls to ``parse_dwarf_metadata(so_path)`` and
    ``parse_advanced_dwarf(so_path)`` that each open the file and iterate
    over all CUs independently.

    Returns (DwarfMetadata(), AdvancedDwarfMetadata()) on any error.
    Never raises.
    """
    empty = DwarfMetadata(), AdvancedDwarfMetadata()

    try:
        with open(so_path, "rb") as f:
            st = os.fstat(f.fileno())
            if not stat.S_ISREG(st.st_mode):
                log.warning("parse_dwarf: not a regular file: %s", so_path)
                return empty

            elf = ELFFile(f)  # type: ignore[no-untyped-call]

            if not has_real_dwarf_info(elf):
                log.debug("parse_dwarf: no DWARF info in %s", so_path)
                return empty

            meta = DwarfMetadata(has_dwarf=True)
            adv = AdvancedDwarfMetadata(has_dwarf=True)
            adv.target_arch = _normalize_arch(elf)

            dwarf = elf.get_dwarf_info()  # type: ignore[no-untyped-call]

            # Per-binary type-resolution cache: (cu_offset, die_offset) → (type_name, byte_size).
            # DIE offsets are only unique within one ELF file — do not share across binaries.
            type_cache: dict[tuple[int, int], tuple[str, int]] = {}

            for CU in dwarf.iter_CUs():
                try:
                    _meta_process_cu(CU, meta, type_cache)
                except Exception as exc:  # noqa: BLE001
                    log.warning("parse_dwarf: meta CU skipped in %s: %s", so_path, exc)
                try:
                    _adv_process_cu(CU, adv)
                except (ELFError, OSError, ValueError, KeyError) as exc:
                    log.warning("parse_dwarf: adv CU skipped in %s: %s", so_path, exc)

            return meta, adv

    except (ELFError, OSError, ValueError) as exc:
        log.warning("parse_dwarf: failed to open/parse %s: %s", so_path, exc)
        return empty


# ---------------------------------------------------------------------------
# Backward-compatible shims
# ---------------------------------------------------------------------------

def parse_dwarf_metadata(so_path: Path) -> DwarfMetadata:
    """Thin shim — delegates to parse_dwarf() and returns only DwarfMetadata.

    .. note::
        If you also need ``AdvancedDwarfMetadata``, call ``parse_dwarf()``
        directly to avoid opening the file twice.
    """
    meta, _ = parse_dwarf(so_path)
    return meta


def parse_advanced_dwarf(so_path: Path) -> AdvancedDwarfMetadata:
    """Thin shim — delegates to parse_dwarf() and returns only AdvancedDwarfMetadata.

    .. note::
        If you also need ``DwarfMetadata``, call ``parse_dwarf()``
        directly to avoid opening the file twice.
    """
    _, adv = parse_dwarf(so_path)
    return adv
