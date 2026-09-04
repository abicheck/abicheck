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

from elftools.elf.elffile import ELFFile

from .dwarf_advanced import (
    AdvancedDwarfMetadata,
    _normalize_arch,
    _parse_frame_registers,
    _process_cu_impl as _adv_process_cu,
)
from .dwarf_metadata import DwarfMetadata, _process_cu_impl as _meta_process_cu
from .dwarf_utils import (
    dwarf_low_memory_mode,
    free_cu_die_cache,
    has_real_dwarf_info,
    is_skeleton_cu as _is_skeleton_cu,
)

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

    On a large binary (``dwarf_low_memory_mode`` -- see ``dwarf_utils.py``),
    each CU's DIE cache is freed as soon as this pass finishes with it,
    trading that reuse for bounded peak memory: a later pass over the same
    session (the snapshot build, the layout backfill) simply re-parses the
    CU's DIEs from the stream on its own first touch, rather than finding
    the whole binary's DIE tree still resident from this one. Output is
    unaffected either way -- see ``free_cu_die_cache``'s docstring.
    """
    meta = DwarfMetadata(has_dwarf=True, evidence_state="parsed")
    adv = AdvancedDwarfMetadata(has_dwarf=True, evidence_state="parsed")
    adv.target_arch = session.arch
    low_memory = dwarf_low_memory_mode(session.dwarf)

    # Per-binary type-resolution cache: (cu_offset, die_offset) → (type_name, byte_size).
    # DIE offsets are only unique within one ELF file — do not share across binaries.
    type_cache: dict[tuple[int, int], tuple[str, int]] = {}
    # P1 review, fresh evidence: same gap as dwarf_metadata._parse's own
    # `incomplete` list -- a per-DIE type-resolution failure inside an
    # otherwise-successful CU (a malformed DW_AT_type reference) previously
    # left cu_failed untouched here too, since this is the unified dump path
    # `dumper.py` actually uses for a real ELF dump, not just the standalone
    # parser. Threaded through `_meta_process_cu` (dwarf_metadata._process_cu)
    # the same way; folded into meta.evidence_state below alongside the
    # cu_failed/skeleton_cus check.
    incomplete: list[bool] = []

    skeleton_cus = 0
    for CU in session.dwarf.iter_CUs():
        meta.cu_total += 1
        adv.cu_total += 1
        if _is_skeleton_cu(CU):
            # -gsplit-dwarf: this CU is a skeleton -- its real layout/
            # calling-convention DIEs live in an unconsumed .dwo/.dwp file
            # (debug_resolver.py can locate one for an EXTERNAL symbol-file
            # search, but nothing in this parse path opens and merges it).
            # iter_CUs() and the per-CU walk below both "succeed" on a
            # skeleton -- there is simply almost nothing under it -- so
            # without this check the channel would be stamped "parsed" while
            # actually carrying none of the real type/CC facts (P1 review:
            # reproduced int->long struct-layout regression missed at
            # NO_CHANGE/exit 0). Fail closed: report the channel as
            # incomplete rather than attempt full DWO/DWP resolution here.
            skeleton_cus += 1
        try:
            _meta_process_cu(CU, meta, type_cache, incomplete=incomplete)
        except Exception as exc:  # noqa: BLE001
            meta.cu_failed += 1
            log.warning("parse_dwarf: meta CU skipped in %s: %s", session.path, exc)
        try:
            _adv_process_cu(CU, adv)
        except Exception as exc:  # noqa: BLE001 - a corrupt CU must not abort the other CUs
            adv.cu_failed += 1
            log.warning("parse_dwarf: adv CU skipped in %s: %s", session.path, exc)
        if low_memory:
            free_cu_die_cache(CU)

    # P1 review: _parse_frame_registers (the sole producer of
    # frame_registers/callee_saved_regs) was previously called only by the
    # standalone dwarf_advanced.parse_advanced_dwarf() entry point, never by
    # this unified single-pass path -- which is what dumper.py's real ELF
    # dumps actually use. A normal comparison's advanced channel could
    # therefore report "parsed" while frame_register_changed/callee-saved
    # facts were never evaluated at all. Run it here too, over the same
    # already-open session. Its own docstring promises "never raises", but
    # its except clause is narrower than that promise (only ELFError/
    # OSError/ValueError) -- wrapped defensively here rather than widened
    # there, since that module is a shared file this fix should not risk
    # editing concurrently. Skipped entirely for a zero-CU parse: nothing
    # to correlate frame data against, and a test double standing in for a
    # truncated/empty .debug_info section carries no real ELF/DWARFInfo.
    cfi_complete = True
    if meta.cu_total:
        try:
            cfi_complete = _parse_frame_registers(session.elf, session.dwarf, adv)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "parse_dwarf: frame-register extraction failed in %s: %s",
                session.path,
                exc,
            )
            cfi_complete = False

    if skeleton_cus:
        log.warning(
            "parse_dwarf: %d/%d compilation unit(s) in %s are split-DWARF "
            "skeletons whose real DIEs live in an unconsumed .dwo/.dwp file "
            "-- treating both DWARF channels as incomplete",
            skeleton_cus,
            meta.cu_total,
            session.path,
        )

    for channel in (meta, adv):
        if channel.cu_total == 0:
            # iter_CUs() yielded nothing -- an empty or truncated
            # .debug_info section still "succeeds" at the iterator level
            # (this branch, not the elf.get_dwarf_info() exception path
            # above), so without this check a real zero-CU parse reads as
            # "parsed" with cu_total/cu_failed both 0 (P1 review, fresh
            # evidence: reproduced with an ELF carrying an empty
            # .debug_info section -- --require-complete-analysis exited 0).
            channel.evidence_state = "failed"
        elif channel.cu_failed or skeleton_cus:
            channel.evidence_state = (
                "failed"
                if channel.cu_failed and channel.cu_failed == channel.cu_total
                else "partial"
            )

    if incomplete and meta.evidence_state == "parsed":
        # Every CU-level try/except succeeded, but at least one per-DIE type
        # reference inside one of them could not be resolved. Only downgrades
        # a clean "parsed" -- an already partial/failed state from the
        # cu_failed/skeleton_cus check above is not overwritten.
        meta.evidence_state = "partial"

    if not cfi_complete and adv.evidence_state == "parsed":
        # P1 review, fresh evidence: mirrors dwarf_advanced.
        # parse_advanced_dwarf's identical CFI-completeness downgrade -- a
        # malformed/unsupported FDE is caught and skipped inside
        # _parse_frame_registers itself, so the pass "succeeds" while
        # frame-register/callee-saved-register facts for that FDE were
        # never extracted. Only downgrades a clean "parsed" -- an already
        # partial/failed state from the CU accounting above is not
        # overwritten either direction.
        adv.evidence_state = "partial"

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
        return (
            DwarfMetadata(evidence_state="failed"),
            AdvancedDwarfMetadata(evidence_state="failed"),
        )

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
