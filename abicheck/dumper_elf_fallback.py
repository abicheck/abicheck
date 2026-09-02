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

"""ELF no-headers fallback snapshot builders for :mod:`dumper`.

Relocated out of ``dumper.py`` (P0/P1 toolchain-profile audit merge
follow-up) to free line budget — ``dumper.py`` sits exactly at the
AI-readiness file-size hard cap, so any net-positive addition there needs
an equal-or-greater reduction elsewhere first. This is a pure relocation,
not a rewrite; ``dumper.py`` re-exports both names so existing bare-name
calls and test patches (``patch.object(dumper, "_try_dwarf_snapshot",
...)`` etc.) keep working unchanged.
"""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING

from .dumper_elf_symbols import _populate_elf_visibility
from .dumper_toolchain import _safe_mtime, _safe_size
from .model import AbiSnapshot, Function, RecordType, Variable, Visibility
from .model.identity import entity_id_for_function, entity_id_for_variable

if TYPE_CHECKING:
    from pathlib import Path

    from .dwarf_advanced import AdvancedDwarfMetadata
    from .dwarf_metadata import DwarfMetadata
    from .dwarf_unified import DwarfSession
    from .elf_metadata import ElfMetadata

# Logs under the original "abicheck.dumper" name (not __name__) since this
# is a pure relocation: callers/tests scoped to that logger (e.g. caplog)
# must keep seeing these records unchanged.
log = logging.getLogger("abicheck.dumper")


def _try_dwarf_snapshot(
    so_path: Path,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    version: str,
    profile_hint: str | None,
    headers: list[Path],
    dwarf_only: bool,
    session: DwarfSession | None = None,
) -> tuple[AbiSnapshot | None, list[RecordType]]:
    """Attempt to build a snapshot from DWARF debug info.

    Returns ``(snapshot, dwarf_only_types)``.  When the snapshot should be
    used directly, *snapshot* is non-None.  When DWARF produced no symbols
    (and *dwarf_only* is False), *snapshot* is None and *dwarf_only_types*
    carries the partial type list for the symbol-only fallback path.

    *session*, when provided, is the open :class:`DwarfSession` from the
    metadata parse; the snapshot DIE walk reuses it instead of re-opening
    ``so_path`` (F5b). The caller retains ownership and closes it.
    """
    from .dwarf_snapshot import build_snapshot_from_dwarf

    if dwarf_only and headers:
        warnings.warn(
            "--dwarf-only: ignoring provided headers; using DWARF as primary data source.",
            UserWarning,
            stacklevel=3,
        )

    snap = build_snapshot_from_dwarf(
        so_path,
        elf_meta,
        dwarf_meta,
        dwarf_adv,
        version=version,
        language_profile=profile_hint,
        session=session,
    )
    # If DWARF produced functions (or was explicitly forced), use it.
    if snap.functions or snap.variables or dwarf_only:
        if not headers and not dwarf_only:
            # Advisory, not a problem: header-less dump is a legitimate mode (a
            # stripped/binary-only library). Demoted from UserWarning to an
            # info log so it does not spam stderr on every run; visible under
            # `-v` (ADR-035 P6). The genuine "headers passed but unusable"
            # cases below stay UserWarnings.
            log.info(
                "No headers provided — using DWARF debug info as primary data source. "
                "#define constants and default parameter values will be unavailable."
            )
        _populate_elf_visibility(snap)
        return snap, []
    # DWARF snapshot had no symbols of its own (often the case when
    # the binary exports only constructors / extern "C" wrappers that
    # the DWARF subprogram filter rejected). Keep the *types* it
    # extracted — they include bases / vtable info that pure-DWARF
    # metadata (DwarfMetadata.structs) does not retain.
    return None, list(snap.types)


def _elf_fallback_mangled_name(sym: str) -> str | None:
    """The genuine ``mangled_name`` to offer ``entity_id_for_function``/
    ``entity_id_for_variable`` for a raw ELF dynamic-symbol-table export
    (ADR-063 Phase 2).

    Symbol-table-only evidence carries no signal beyond the bare symbol
    string itself, unlike DWARF's own ``DW_AT_linkage_name``
    presence/absence (see ``dwarf_scope.function_entity_id``'s docstring)
    -- so a genuine plain-C export (e.g. ``add``) and a real, explicitly-
    linked non-Itanium C++ export (e.g. an ``asm("custom_name")``-labeled
    function) are structurally indistinguishable here: both are just an
    identifier with no ``_Z`` prefix. Gating on the ``_Z`` prefix, as this
    producer's own ``is_extern_c`` field does, is therefore not a
    correctness bug to "fix" either direction picks a real trade-off, not
    a clean win (Codex review, PR #1015, two rounds): treating every
    export as genuinely mangled correctly identifies the rare asm-labeled
    case but disagrees with the two header-AST/DWARF backends' own
    ``("extern_c",)`` tagging for the far more common plain-C case, and
    vice versa. This function defaults to matching that more common case
    -- ``None`` (no genuine mangling) whenever *sym* lacks the ``_Z``
    prefix, an accepted, documented residual gap for the asm-labeled
    case rather than a heuristic tuned to "fix" it at the common case's
    expense.
    """
    return sym if sym.startswith("_Z") else None


def _build_symbol_only_snapshot(
    so_path: Path,
    version: str,
    elf_meta: ElfMetadata,
    dwarf_meta: DwarfMetadata,
    dwarf_adv: AdvancedDwarfMetadata,
    exported_dynamic_funcs: set[str],
    exported_dynamic_objects: set[str],
    exported_dynamic_tls: set[str],
    dwarf_only_types: list[RecordType],
    profile_hint: str | None,
) -> AbiSnapshot:
    """Build a symbol-only :class:`AbiSnapshot` when no headers are available.

    Issues the appropriate ``UserWarning`` based on whether DWARF-derived
    types are present, then assembles the snapshot from ELF-exported symbols.
    """
    # No headers → symbol-only fallback. When the DWARF snapshot
    # builder produced types but no functions, we still preserve
    # those types (see *dwarf_only_types*), so the warning is
    # narrowed to reflect what's actually missing.
    # Advisory (ADR-035 P6): a header-less dump is a legitimate mode, so this is
    # an info log (suppressed by default, shown under `-v`), not a stderr-spamming
    # UserWarning on every run.
    if dwarf_only_types:
        log.info(
            "No headers provided — using ELF-exported symbols for "
            "functions/variables; DWARF-derived type information "
            "preserved."
        )
    elif dwarf_meta.has_dwarf:
        log.info(
            "No headers provided — using ELF-exported symbols only; DWARF "
            "debug info is present but was not expanded into the ABI surface."
        )
    else:
        log.info(
            "No headers provided and no DWARF debug info — only ELF-exported "
            "symbols will be captured; type information will be missing."
        )
    _so_mtime, _so_mtime_epoch = _safe_mtime(so_path)
    snapshot = AbiSnapshot(
        library=so_path.name,
        version=version,
        source_path=str(so_path.resolve()),
        source_mtime=_so_mtime,
        source_mtime_epoch=_so_mtime_epoch,
        source_size=_safe_size(so_path),
        functions=[
            Function(
                name=sym,
                mangled=sym,
                return_type="?",
                visibility=Visibility.ELF_ONLY,
                # Absence of Itanium _Z prefix is strong evidence of C linkage
                is_extern_c=not sym.startswith("_Z"),
                # ADR-063 Phase 2 (ELF-symbol-only slice). `_elf_fallback_
                # mangled_name`'s own docstring has the full "why": unlike
                # DWARF (a real DW_AT_linkage_name presence/absence signal
                # -- see dwarf_scope.function_entity_id), symbol-table-only
                # evidence cannot distinguish a genuine plain-C export from
                # a real, explicitly-linked non-Itanium C++ export (e.g. an
                # `asm("custom_name")`-labeled function) at all -- both are
                # just a bare identifier string with no `_Z` prefix. This
                # producer defaults to matching the two header-AST/DWARF
                # backends' own extern-"C" convention for the (far more
                # common) plain-C case, an accepted, documented residual
                # gap for the rarer asm-labeled case (Codex review, PR
                # #1015, two rounds: the first fix traded this common-case
                # regression for that rare-case fix, which review correctly
                # judged the wrong direction to default).
                entity_id=entity_id_for_function(
                    (),
                    sym,
                    mangled_name=_elf_fallback_mangled_name(sym),
                    is_extern_c=not sym.startswith("_Z"),
                ),
            )
            for sym in sorted(exported_dynamic_funcs)
        ],
        variables=[
            Variable(
                name=sym,
                mangled=sym,
                type="?",
                visibility=Visibility.ELF_ONLY,
                entity_id=entity_id_for_variable(
                    (),
                    sym,
                    mangled_name=_elf_fallback_mangled_name(sym),
                    is_extern_c=not sym.startswith("_Z"),
                ),
            )
            for sym in sorted(exported_dynamic_objects | exported_dynamic_tls)
        ],
        # Preserve DWARF-derived types (with bases / vtable) when the
        # symbol-only fallback is taken. Pure DwarfMetadata loses
        # inheritance info; retaining the partially-populated DWARF
        # snapshot's types lets downstream detectors (e.g. internal
        # leak detection) still see the relationships.
        types=dwarf_only_types,
        elf=elf_meta,
        dwarf=dwarf_meta,
        dwarf_advanced=dwarf_adv,
        elf_only_mode=True,
        platform="elf",
        language_profile=profile_hint,
    )
    _populate_elf_visibility(snapshot)
    return snapshot
