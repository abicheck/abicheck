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


"""Ordinal part 1 of ``platform.py``'s entry list -- not a separate owner.

``platform.py`` remains the taxonomy and the single public name
(``PLATFORM_ENTRIES``); see its docstring for this taxonomy's scope, its
boundary against the other four, and the methodology the entries were
categorized by. This file holds a contiguous slice of that one list and
claims no responsibility of its own, so nothing should import it directly.

The split is by declaration-order line position, not by concern, purely so
each file stays under ADR-061's 800-line ceiling -- the same reason and the
same shape as ``kind_names_{1,2,3}.py``, whose own docstring records that an
ordinal split is the right tool when the content is a data table rather than
behavior. Partitioning *this* list by a named sub-concern would be a
different change: it would move the D9 ownership boundary that
``symbols``/``types``/``platform``/``build``/``source`` already draws, and
the "Adding a new ChangeKind" procedure names those five modules by name.
"""

from __future__ import annotations

from .observability import ObservabilityCondition, Platform, observable
from .registry import (
    ChangeEntity,
    ChangeKindMeta,
    ChangeOperation,
    ContractSubject,
    Verdict,
)

_OBS_B_ELF = observable(
    ("B",), condition=ObservabilityCondition(platform=frozenset({Platform.ELF}))
)
_OBS_D_PRESENCE = observable(
    ("B", "D"),
    rationale="DWARF presence on the old side: only a debug-info read sees it",
)
_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta
_ENT = ChangeEntity
_OP = ChangeOperation
_CS = ContractSubject

PLATFORM_ENTRIES_1: list[ChangeKindMeta] = [
    _E(
        "abi_surface_explosion",
        _C,
        impact="Public ABI surface grew dramatically (e.g. lost "
        "-fvisibility=hidden). This is a configuration/packaging signal, not "
        "a per-symbol break, but may indicate an unintended visibility regression. "
        "A large shrink is not reported under this kind; each removed export is "
        "already its own finding.",
        description_template="ABI surface {detail} dramatically: {old} → {new} exported symbols ({name}); check -fvisibility=hidden and version scripts",
        entity=_ENT.ANALYSIS,
        contract_subject=_CS.ANALYSIS,
        operation=_OP.MODIFIED,
    ),
    _E(
        "common_symbol_risk",
        _C,
        observable_at=(_OBS_B_ELF,),
        impact="An exported symbol is a tentative definition (STT_COMMON); "
        "its final address and merge behavior across translation "
        "units is decided by the linker, which can differ across "
        "toolchains/link orders. Not itself a break, but a source of "
        "non-determinism worth being aware of.",
        description_template="Exported STT_COMMON symbol: {name} (resolution depends on linker/loader)",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "compat_version_changed",
        _B,
        impact="Mach-O compatibility version changed; dylibs linked against old version may fail to load.",
        description_template="compatibility version changed: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "dwarf_info_missing",
        _C,
        observable_at=(_OBS_D_PRESENCE,),
        impact="The new binary carries no DWARF debug info — this detector "
        "only checks whether DWARF is present, not why it's absent, "
        "so this fires whether the binary was never compiled with "
        "-g or was compiled with -g and then stripped. Either way, "
        "DWARF-derived layout comparisons couldn't run for it — but "
        "layout isn't necessarily unchecked entirely: a separate "
        "detector compares layout from header-AST evidence "
        "independently of DWARF and still runs when both sides "
        "carry it, so a normal header-plus-binary comparison can "
        "still catch a layout change here. Only an ELF/DWARF-only "
        "comparison (no header evidence) loses coverage entirely. "
        "Not itself an ABI break; ensure debug info is present and "
        "re-scan to restore full DWARF-derived coverage.",
        description_template="New binary has no DWARF debug info — struct/enum layout comparison was skipped. Ensure debug info is present (compiled in or supplied separately) to enable.",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
    ),
    _E(
        "exported_object_alignment_reduced",
        _R,
        impact="An exported data object's address alignment dropped. Consumers "
        "that copy-relocate the object (non-PIC executables) allocated "
        "space with the old alignment guarantee, and code compiled "
        "against the old headers may use aligned loads (SIMD) that now "
        "fault or fall back to slow paths.",
        description_template="Exported object alignment reduced: {name} ({old} → {new} bytes)",
        entity=_ENT.VARIABLE,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "func_deleted_elf_fallback",
        _B,
        impact="The exported symbol vanished from the dynamic symbol table "
        "with no explicit `= delete`/removal marker in the header "
        "the diff could otherwise attribute it to; an already-"
        "linked consumer calling it fails to resolve the symbol at "
        "load time.",
        description_template="Symbol disappeared from ELF .dynsym without explicit deletion marker: {name} — was exported in old library, absent in new library's dynamic symbol table while header still declares it",
        entity=_ENT.FUNCTION,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
    ),
    _E(
        "inline_namespace_moved",
        _B,
        impact="Symbols moved to a different inline namespace (e.g. v1:: → v2::); "
        "mangled names change so old binaries fail to resolve the symbols.",
        description_template="Inline namespace move detected: {detail} symbols appear to have moved between inline namespace versions (e.g. ::v1:: → ::v2::); mangled names changed",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "kabi_crc_changed",
        _B,
        impact="A kernel-exported symbol's genksyms CRC changed. Even though the "
        "symbol still exists, CONFIG_MODVERSIONS embeds the old CRC in "
        "out-of-tree modules and the loader rejects the module ('disagrees "
        "about version of symbol') — the type signature behind the symbol "
        "changed.",
        description_template="Kernel symbol CRC changed: {name} ({old} → {new}) — modversions will reject the module",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "kabi_export_type_changed",
        _A,
        impact="A kernel-exported symbol changed between EXPORT_SYMBOL and "
        "EXPORT_SYMBOL_GPL. A non-GPL module that used a symbol now marked "
        "GPL-only can no longer link against it — a license-gated "
        "availability break for that class of consumer.",
        description_template="Kernel symbol export type changed: {name} ({old} → {new})",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "kabi_symbol_added",
        _C,
        is_addition=True,
        impact="A new kernel-exported symbol appeared; existing modules are unaffected.",
        description_template="New kernel-exported symbol: {name}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.ADDED,
    ),
    _E(
        "kabi_symbol_namespace_changed",
        _B,
        impact="A kernel-exported symbol gained or moved its export namespace "
        "(EXPORT_SYMBOL_NS*). A module that does not declare the matching "
        "MODULE_IMPORT_NS() fails to load, so a gained/changed namespace is a "
        "load-time break for existing modules.",
        description_template="Kernel symbol namespace changed: {name} ({old} → {new})",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.MODIFIED,
    ),
    _E(
        "kabi_symbol_removed",
        _B,
        impact="A kernel-exported symbol (EXPORT_SYMBOL*) was removed from "
        "Module.symvers. Out-of-tree modules that reference it fail to load "
        "with 'Unknown symbol'.",
        description_template="Kernel-exported symbol removed: {name}",
        entity=_ENT.BINARY,
        contract_subject=_CS.DECLARATION,
        operation=_OP.REMOVED,
    ),
    _E(
        "library_version_downgraded",
        _R,
        impact="The embedded library version regressed (PE VS_FIXEDFILEINFO "
        "FileVersion or Mach-O LC_ID_DYLIB current_version). Installers "
        "and side-by-side logic that compare file versions may refuse "
        "to replace the file or silently keep the older copy, and a "
        "downgrade usually signals a mispackaged artifact.",
        description_template="Library version downgraded: {old} → {new}",
        entity=_ENT.BINARY,
        contract_subject=_CS.BINARY_WIDE,
        operation=_OP.MODIFIED,
    ),
]
