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

A :class:`DwarfSession` lets a *third* pass — ``dwarf_snapshot``'s snapshot
build — reuse the same open ``DWARFInfo`` instead of opening the ELF again.
pyelftools caches parsed DIEs, so that reuse turns the snapshot's full-tree
walk from a cold re-parse into cache hits (F5b, pvxs validation) with
byte-for-byte identical output. ``dumper._dump_elf`` opens one session, runs
the metadata passes, hands it to the snapshot build, then closes it.

Public API
----------
parse_dwarf(so_path) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]
    Single entry point used by dumper.dump().
open_dwarf_session(so_path) -> DwarfSession | None
    Open the ELF/DWARFInfo once for reuse across passes (caller closes).
parse_dwarf_from_session(session) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]
    Run the metadata passes over an already-open session.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from .dwarf_advanced import (
    AdvancedDwarfMetadata,
    _normalize_arch,
    _process_cu_impl as _adv_process_cu,
)
from .dwarf_metadata import DwarfMetadata, _process_cu_impl as _meta_process_cu
from .dwarf_utils import has_real_dwarf_info

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared DWARF session — one ELF open + one DWARFInfo, reusable across passes
# ---------------------------------------------------------------------------

@dataclass
class DwarfSession:
    """An open ELF file and its ``DWARFInfo``, shareable across parse passes.

    pyelftools caches parsed DIEs *inside* each ``CompileUnit`` (and caches the
    CU objects across ``iter_CUs()``), so a second full-tree walk over the same
    ``DWARFInfo`` is served from that cache instead of re-parsing every DIE.
    The three DWARF passes (basic metadata, advanced metadata, snapshot build)
    each walk every DIE, and when they open the file independently they build
    three *separate* ``DWARFInfo`` objects that share no cache — the redundant
    re-parse that F5b (pvxs validation) measured. Threading one session through
    all three collapses that to a single parse; the later passes hit the cache
    the first warmed, byte-for-byte identical output.

    The caller owns the lifetime: call :meth:`close` (or reuse it and close it)
    exactly once when done.
    """

    path: Path
    _file: BinaryIO
    elf: Any  # elftools.elf.elffile.ELFFile
    dwarf: Any  # elftools.dwarf.dwarfinfo.DWARFInfo
    arch: str

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass


def open_dwarf_session(so_path: Path) -> DwarfSession | None:
    """Open *so_path* and return a :class:`DwarfSession`, or ``None``.

    Returns ``None`` (having released any handle) when the path is not a
    regular file, carries no real DWARF, or cannot be opened/parsed — the same
    conditions under which :func:`parse_dwarf` yields empty metadata. Never
    raises. The caller must :meth:`~DwarfSession.close` a non-``None`` result.
    """
    try:
        f = open(so_path, "rb")
    except OSError as exc:
        log.warning("parse_dwarf: failed to open/parse %s: %s", so_path, exc)
        return None
    try:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode):
            log.warning("parse_dwarf: not a regular file: %s", so_path)
            f.close()
            return None

        elf = ELFFile(f)  # type: ignore[no-untyped-call]

        if not has_real_dwarf_info(elf):
            log.debug("parse_dwarf: no DWARF info in %s", so_path)
            f.close()
            return None

        dwarf = elf.get_dwarf_info()  # type: ignore[no-untyped-call]
        return DwarfSession(
            path=Path(so_path),
            _file=f,
            elf=elf,
            dwarf=dwarf,
            arch=_normalize_arch(elf),
        )
    except Exception as exc:  # noqa: BLE001 - never raise; always release the handle
        # pyelftools can raise beyond (ELFError, OSError, ValueError) on corrupt
        # DWARF (struct.error, KeyError, …). The legacy parse_dwarf used a
        # ``with open()`` block that closed on *any* exception; match that here
        # so the "never raises" contract holds and no descriptor leaks.
        log.warning("parse_dwarf: failed to open/parse %s: %s", so_path, exc)
        f.close()
        return None


def parse_dwarf_from_session(
    session: DwarfSession,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Run the basic + advanced metadata passes over an open *session*.

    Behaviourally identical to the DWARF branch of :func:`parse_dwarf`; split
    out so the snapshot build can reuse the same session (and its warm DIE
    cache) instead of opening the ELF a second time.
    """
    meta = DwarfMetadata(has_dwarf=True)
    adv = AdvancedDwarfMetadata(has_dwarf=True)
    adv.target_arch = session.arch

    # Per-binary type-resolution cache: (cu_offset, die_offset) → (type_name, byte_size).
    # DIE offsets are only unique within one ELF file — do not share across binaries.
    type_cache: dict[tuple[int, int], tuple[str, int]] = {}

    for CU in session.dwarf.iter_CUs():
        try:
            _meta_process_cu(CU, meta, type_cache)
        except Exception as exc:  # noqa: BLE001
            log.warning("parse_dwarf: meta CU skipped in %s: %s", session.path, exc)
        try:
            _adv_process_cu(CU, adv)
        except (ELFError, OSError, ValueError, KeyError) as exc:
            log.warning("parse_dwarf: adv CU skipped in %s: %s", session.path, exc)

    return meta, adv


# ---------------------------------------------------------------------------
# Unified single-pass entry point
# ---------------------------------------------------------------------------

def parse_dwarf(
    so_path: Path,
    *,
    _session_out: list[DwarfSession] | None = None,
) -> tuple[DwarfMetadata, AdvancedDwarfMetadata]:
    """Open *so_path* once and extract both DwarfMetadata and AdvancedDwarfMetadata.

    Replaces two separate calls to ``parse_dwarf_metadata(so_path)`` and
    ``parse_advanced_dwarf(so_path)`` that each open the file and iterate
    over all CUs independently.

    Returns (DwarfMetadata(), AdvancedDwarfMetadata()) on any error.
    Never raises.

    ``_session_out`` (internal): when a list is supplied and real DWARF is
    present, the still-open :class:`DwarfSession` is appended to it for the
    caller to reuse (e.g. the DWARF snapshot build) and then close. When it is
    ``None`` (the default, and every external caller) the session is closed
    before returning, so behaviour is unchanged.
    """
    session = open_dwarf_session(so_path)
    if session is None:
        return DwarfMetadata(), AdvancedDwarfMetadata()

    try:
        meta, adv = parse_dwarf_from_session(session)
    except Exception as exc:  # noqa: BLE001 - never raise; mirror the legacy top-level guard
        # parse_dwarf_from_session guards each CU, but iter_CUs() itself can
        # raise on malformed/truncated CU headers before the per-CU try runs.
        # Close the session and fall back to empty metadata (the caller then
        # degrades to symbol-only) rather than leaking the handle / aborting.
        log.warning("parse_dwarf: failed to parse CUs in %s: %s", so_path, exc)
        session.close()
        return DwarfMetadata(), AdvancedDwarfMetadata()

    if _session_out is not None:
        _session_out.append(session)
    else:
        session.close()
    return meta, adv


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
