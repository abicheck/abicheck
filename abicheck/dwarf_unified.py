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
    # P1 review, fresh evidence (Codex): when *path* is a detached-debug
    # sidecar resolved via --debug-root/--debuginfod (objcopy
    # --only-keep-debug), its own .eh_frame/.debug_frame retain only
    # SHT_NOBITS section headers -- the real unwind data (and, often, the
    # only intact .dynsym/.symtab for address-to-symbol correlation) lives
    # in the *primary* stripped binary alongside it. cfi_elf/cfi_dwarf, when
    # set, are a second ELFFile/DWARFInfo opened against that primary
    # binary specifically for CFI extraction -- DIE-based analysis (structs,
    # calling conventions, packed-typedef checks) still reads from
    # elf/dwarf (the sidecar) as before. ``None`` (the default, and every
    # caller not resolving a detached-debug artifact) means "use elf/dwarf
    # for CFI too", unchanged from before this field existed.
    cfi_elf: Any | None = None
    cfi_dwarf: Any | None = None
    _cfi_file: BinaryIO | None = None

    def close(self) -> None:
        try:
            self._file.close()
        except OSError:
            pass
        if self._cfi_file is not None:
            try:
                self._cfi_file.close()
            except OSError:
                pass


def open_dwarf_session(
    so_path: Path, *, cfi_source_path: Path | None = None
) -> DwarfSession | None:
    """Open *so_path* and return a :class:`DwarfSession`, or ``None``.

    Returns ``None`` (having released any handle) when the path is not a
    regular file, carries no real DWARF, or cannot be opened/parsed — the same
    conditions under which :func:`parse_dwarf` yields empty metadata. Never
    raises. The caller must :meth:`~DwarfSession.close` a non-``None`` result.

    ``cfi_source_path`` (P1 review, fresh evidence): the *primary* binary a
    detached-debug *so_path* was resolved from (``--debug-root``/
    ``--debuginfod``), when they differ. Opened as a second ELF file whose
    ``.eh_frame``/``.debug_frame`` and symbol table back CFI extraction
    instead of the sidecar's own (typically ``SHT_NOBITS``) copies. Best-
    effort: any failure to open it is logged and leaves
    ``cfi_elf``/``cfi_dwarf`` unset (falling back to the sidecar's own
    ``elf``/``dwarf``, the pre-existing behaviour) rather than failing the
    whole DWARF session over an enhancement.
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
        session = DwarfSession(
            path=Path(so_path),
            _file=f,
            elf=elf,
            dwarf=dwarf,
            arch=_normalize_arch(elf),
        )
        if cfi_source_path is not None and Path(cfi_source_path) != Path(so_path):
            _attach_cfi_source(session, cfi_source_path)
        return session
    except Exception as exc:  # noqa: BLE001 - never raise; always release the handle
        # pyelftools can raise beyond (ELFError, OSError, ValueError) on corrupt
        # DWARF (struct.error, KeyError, …). The legacy parse_dwarf used a
        # ``with open()`` block that closed on *any* exception; match that here
        # so the "never raises" contract holds and no descriptor leaks.
        log.warning("parse_dwarf: failed to open/parse %s: %s", so_path, exc)
        f.close()
        return None


def _attach_cfi_source(session: DwarfSession, cfi_source_path: Path) -> None:
    """Best-effort: open *cfi_source_path* and attach it to *session* as the
    CFI-extraction source. Never raises; any failure just leaves
    ``session.cfi_elf``/``session.cfi_dwarf`` unset."""
    try:
        cfi_f = open(cfi_source_path, "rb")
    except OSError as exc:
        log.warning(
            "parse_dwarf: failed to open CFI source %s for %s: %s",
            cfi_source_path,
            session.path,
            exc,
        )
        return
    try:
        st = os.fstat(cfi_f.fileno())
        if not stat.S_ISREG(st.st_mode):
            log.warning(
                "parse_dwarf: CFI source not a regular file: %s", cfi_source_path
            )
            cfi_f.close()
            return
        cfi_elf = ELFFile(cfi_f)  # type: ignore[no-untyped-call]
        session.cfi_elf = cfi_elf
        session.cfi_dwarf = cfi_elf.get_dwarf_info()  # type: ignore[no-untyped-call]
        session._cfi_file = cfi_f
    except Exception as exc:  # noqa: BLE001 - best-effort enhancement, never fatal
        log.warning(
            "parse_dwarf: failed to parse CFI source %s for %s: %s",
            cfi_source_path,
            session.path,
            exc,
        )
        cfi_f.close()


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
    # P1 review, fresh evidence: sibling gap in the advanced channel -- a
    # malformed DW_AT_type on an exported function's return/parameter type,
    # caught deep inside the value-ABI-trait walk (resolve_type_die/
    # _unwrap_qualifiers/_is_nontrivial_aggregate/_type_unaligned_at, each
    # returning a placeholder rather than raising), previously left cu_failed
    # untouched -- silently omitting that function's value_abi_traits/
    # return_value_sizes/return_memory_classified entries with no
    # completeness signal on this unified path. Mirrors dwarf_advanced.
    # parse_advanced_dwarf's identical standalone-entry-point fix.
    adv_incomplete: list[bool] = []

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
            _adv_process_cu(CU, adv, incomplete=adv_incomplete)
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
        # P1 review, fresh evidence: a detached-debug sidecar's own
        # .eh_frame/.debug_frame are typically SHT_NOBITS -- read CFI (and
        # its address-to-symbol correlation) from the primary binary when
        # one was attached, per DwarfSession.cfi_elf/cfi_dwarf's own
        # docstring.
        cfi_elf = session.cfi_elf if session.cfi_elf is not None else session.elf
        cfi_dwarf = (
            session.cfi_dwarf if session.cfi_dwarf is not None else session.dwarf
        )
        try:
            cfi_complete = _parse_frame_registers(cfi_elf, cfi_dwarf, adv)
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

    if adv_incomplete and adv.evidence_state == "parsed":
        # Sibling of the basic channel's identical incomplete-list check
        # above: at least one value-ABI-trait type reference inside an
        # otherwise-successful CU could not be resolved. Only downgrades a
        # clean "parsed" -- an already partial/failed state from the
        # cu_failed/skeleton_cus check above is not overwritten.
        adv.evidence_state = "partial"

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
    cfi_source_path: Path | None = None,
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

    ``cfi_source_path`` (P1 review, fresh evidence): forwarded to
    :func:`open_dwarf_session` -- the primary binary a detached-debug
    *so_path* was resolved from, when they differ, so CFI extraction reads
    real (not ``SHT_NOBITS``) ``.eh_frame``/``.debug_frame`` data. See
    ``DwarfSession.cfi_elf``'s own docstring.
    """
    session = open_dwarf_session(so_path, cfi_source_path=cfi_source_path)
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
