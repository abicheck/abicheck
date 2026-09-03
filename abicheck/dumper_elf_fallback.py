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
from .extract.export_symbol_identity import (
    itanium_export_function as _elf_export_function,
    itanium_export_variable as _elf_export_variable,
)
from .extract.semantic_normalizer import normalize_header_ast
from .model import AbiSnapshot, RecordType
from .model.semantic_ir import SemanticIR

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


def _dwarf_semantic_ir(snap: AbiSnapshot) -> SemanticIR:
    """``AbiSnapshot.semantic_ir`` for a DWARF-only snapshot (ADR-063 Phase 6,
    fifth slice).

    ``dwarf_snapshot.build_snapshot_from_dwarf`` already populates a real
    ``entity_id`` on every ``RecordType``/``EnumType``/``Function``/
    ``Variable``/typedef it produces (ADR-063 Phase 2's "fourteenth slice") --
    this is the same ``normalize_header_ast`` call ``dumper_manifest.
    resolve_header_ast_result`` makes for the header-AST backends, just
    applied post-hoc here rather than inline in ``dwarf_snapshot.py`` itself,
    since that module sits at its own ``architecture/debt.yaml`` no-growth
    line-count baseline. ``constants``/``constant_entity_ids`` are left at
    their ``{}`` defaults: DWARF carries no constexpr-initializer evidence at
    all (see ``AbiSnapshot.constant_entity_ids``'s own docstring) — there is
    nothing here for a constant occurrence to be built from, not merely
    nothing wired up. See ``extract/semantic_normalizer_dwarf.py``'s own
    module docstring for the two producer-specific ``cv_qualification``
    carve-outs a ``producer="dwarf"`` call needs.
    """
    return normalize_header_ast(
        types=snap.types,
        enums=snap.enums,
        typedefs_qualified=snap.typedefs,
        typedef_entity_ids=snap.typedef_entity_ids,
        producer="dwarf",
        functions=snap.functions,
        variables=snap.variables,
    )


def _dwarf_types_semantic_ir(types: list[RecordType]) -> SemanticIR:
    """The :func:`_dwarf_semantic_ir` counterpart for the symbol-only
    fallback (Codex review, PR #1021, fresh evidence): when the DWARF walk
    found real record types but no functions/variables of its own (a DSO
    combining DWARF-bearing C++ objects with assembly-only exported
    symbols), ``_try_dwarf_snapshot`` returns those *types* alone rather
    than a full snapshot, and ``_build_symbol_only_snapshot`` below
    preserves them (see its own docstring) -- but until this function
    existed, it never normalized them, so a headerless dump omitted
    ``semantic_ir`` entirely despite holding DWARF entities with valid
    ``entity_id``s.

    Deliberately narrower than :func:`_dwarf_semantic_ir`: *types* is the
    only DWARF-derived evidence this fallback snapshot carries at all (no
    ``enums``/typedefs are captured on this path, and the snapshot's own
    ``functions``/``variables`` are raw ELF-export-table entries with no
    DWARF backing -- see ``_build_symbol_only_snapshot``'s own docstring --
    so including them here would misattribute a ``producer="dwarf"``
    occurrence to evidence DWARF never actually supplied). Their absence
    from the returned ``SemanticIR`` is honest sparseness, not a gap: a
    caller finding no occurrence for one of their entity IDs correctly
    learns "no structural evidence", not "confirmed empty".
    """
    return normalize_header_ast(
        types=types,
        enums=(),
        typedefs_qualified={},
        typedef_entity_ids={},
        producer="dwarf",
    )


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
        snap.semantic_ir = _dwarf_semantic_ir(snap)
        return snap, []
    # DWARF snapshot had no symbols of its own (often the case when
    # the binary exports only constructors / extern "C" wrappers that
    # the DWARF subprogram filter rejected). Keep the *types* it
    # extracted — they include bases / vtable info that pure-DWARF
    # metadata (DwarfMetadata.structs) does not retain.
    return None, list(snap.types)


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
        # ADR-063 Phase 2 -- see extract.export_symbol_identity's own module
        # docstring for entity_id's mangled-vs-extern_c gate.
        functions=[_elf_export_function(sym) for sym in sorted(exported_dynamic_funcs)],
        variables=[
            _elf_export_variable(sym)
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
    if dwarf_only_types:
        snapshot.semantic_ir = _dwarf_types_semantic_ir(dwarf_only_types)
    return snapshot
