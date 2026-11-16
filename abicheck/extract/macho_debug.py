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

"""Mach-O debug information for ``dump``: DWARF from a library's dSYM (ADR-021).

The Mach-O snapshot builder asks :func:`macho_dwarf_kwargs` for the snapshot's
``dwarf``/``dwarf_advanced`` inputs. Resolution is the ELF path's: an explicit
``debug_info_path`` first (a refused bundle fails the dump), then the adjacent
``<lib>.dSYM`` bundle (a refused bundle is a non-fatal limitation), else absent.
Absent evidence yields no keyword at all, so a library without DWARF builds the
same snapshot it did before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..model.dwarf_facts import AdvancedExtractionStatus
from .debug_dump import _resolve_debug_metadata

__all__ = ["MACHO_MAGICS", "macho_dwarf_kwargs"]

#: Mach-O and universal (fat) container magics, both byte orders.
MACHO_MAGICS: tuple[bytes, ...] = (
    b"\xfe\xed\xfa\xce",
    b"\xce\xfa\xed\xfe",
    b"\xfe\xed\xfa\xcf",
    b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",
    b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf",
    b"\xbf\xba\xfe\xca",
)


def macho_dwarf_kwargs(
    dylib_path: Path, debug_format: str | None, debug_kwargs: dict[str, Any]
) -> dict[str, Any]:
    """The ``dwarf``/``dwarf_advanced`` snapshot inputs for a Mach-O library.

    ``debug_kwargs`` carries the other debug options the format handler passes
    (``debug_info_path``, ``symbols_only``, ``debug_presence_only``). Returns
    ``{}`` when no DWARF was read, so the snapshot is built exactly as it was
    without this path.
    """
    if debug_kwargs.get("symbols_only") or debug_kwargs.get("debug_presence_only"):
        return {}
    dwarf_meta, dwarf_adv = _resolve_debug_metadata(
        dylib_path,
        debug_format,
        dwarf_source=debug_kwargs.get("debug_info_path"),
    )
    if dwarf_adv.extraction is not AdvancedExtractionStatus.COMPLETED:
        return {}
    return {"dwarf": dwarf_meta, "dwarf_advanced": dwarf_adv}
