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

import logging
import warnings
from pathlib import Path
from typing import Any

from ..dwarf_advanced import AdvancedDwarfMetadata
from ..errors import SnapshotError
from ..model.dwarf_facts import AdvancedExtractionStatus
from .debug_dump import _resolve_debug_metadata
from .extraction_limitations import record_extraction_limitation

_log = logging.getLogger(__name__)

__all__ = [
    "ADJACENT_DSYM_UNREADABLE_LIMITATION",
    "MACHO_MAGICS",
    "macho_dwarf_kwargs",
]

#: The fixed limitation recorded when an adjacent dSYM exists but cannot be read.
#: Fixed text (no path, no exception) so the stored bytes are deterministic, and
#: so the dump cache can recognise the transient-failure case (it must not store
#: a snapshot that a retry with a readable dSYM would have answered differently).
ADJACENT_DSYM_UNREADABLE_LIMITATION = "adjacent dSYM is unreadable and not used"

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
    presence_only = bool(
        debug_kwargs.get("symbols_only") or debug_kwargs.get("debug_presence_only")
    )
    explicit = debug_kwargs.get("debug_info_path")
    try:
        dwarf_meta, dwarf_adv = _resolve_debug_metadata(
            dylib_path, debug_format, dwarf_source=explicit
        )
    except SnapshotError as exc:
        if explicit is not None:
            raise  # an explicit bundle stays fail-closed
        # An adjacent dSYM that cannot be read is absent evidence with a note.
        _log.debug("adjacent dSYM of %s is unreadable: %s", dylib_path, exc)
        record_extraction_limitation(ADJACENT_DSYM_UNREADABLE_LIMITATION)
        return {}
    if explicit is None and dwarf_adv.extraction is AdvancedExtractionStatus.FAILED:
        # The adjacent bundle exists but its DWARF could not be read: the same
        # transient, non-fatal gap as an unreadable dSYM, and recorded the same
        # way so the snapshot is not cached as if the bundle were absent.
        record_extraction_limitation(ADJACENT_DSYM_UNREADABLE_LIMITATION)
        return {}
    if presence_only:
        # ELF's contract: presence is recorded, type expansion is not run.
        if not dwarf_meta.has_dwarf:
            return {}
        return {
            "dwarf": dwarf_meta,
            "dwarf_advanced": AdvancedDwarfMetadata(
                has_dwarf=True, extraction=AdvancedExtractionStatus.NOT_RUN
            ),
        }
    if dwarf_adv.extraction is not AdvancedExtractionStatus.COMPLETED:
        return {}
    return {"dwarf": dwarf_meta, "dwarf_advanced": dwarf_adv}


def warn_dwarf_only(dwarf_only: bool) -> None:
    """Mach-O has no DWARF-only build: say so and fall back to normal extraction."""
    if dwarf_only:
        warnings.warn(
            "dwarf_only=True is not supported for Mach-O; "
            "falling back to normal extraction.",
            UserWarning,
            stacklevel=3,
        )
