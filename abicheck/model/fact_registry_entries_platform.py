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

"""Whole-snapshot and binary-format fact entries for the registry (ADR-063
D7, Phase 5) -- every ``FactDefinition`` whose owner is ``AbiSnapshot``,
``ElfMetadata``, ``PeMetadata`` or ``MachoMetadata``. See
``fact_registry_entries_types.py`` for the split's own rationale."""

from __future__ import annotations

from .fact_registry_schema import FactDefinition, FactLifecycle

__all__ = ["PLATFORM_FACTS"]

_E = FactDefinition

PLATFORM_FACTS: list[FactDefinition] = [
    # ── Phase 5's sixth batch: AbiSnapshot's own remaining case-(b) field ──
    _E(
        owner="AbiSnapshot",
        field="ast_resolved_standard",
        value_type="str | None",
        producing_backends=("castxml", "clang"),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "The resolved C/C++ standard actually used for a header-AST "
            "parse (explicit -std=/--std=/std:, or a forced gnu++20 "
            "when the requires/concept heuristic triggered it) -- None "
            "already unambiguously means 'not captured' (no header-AST "
            "backend ran, or no standard was pinned/forced). Only "
            "populated via dumper_toolchain._ast_compile_provenance(), "
            "shared by dumper.py's ELF/PE/Mach-O snapshot constructors, "
            "so it is never set on a DWARF/symbols-only dump. Plain "
            "case (b) conversion -- the last one outside the four "
            "declaration dataclasses (RecordType, EnumType, Variable, "
            "Function)."
        ),
    ),
    # ── Phase 5's seventh batch: the three binary-format dataclasses' ──
    # ── own case-(b) fields (schema-version-driven, not backend- ──
    # ── driven -- ElfMetadata/PeMetadata/MachoMetadata are parsed by ──
    # ── exactly one backend each, so producing_backends is a ──
    # ── singleton rather than a choice among header/DWARF backends) ──
    _E(
        owner="ElfMetadata",
        field="dynamic_flags",
        value_type="frozenset[str] | None",
        producing_backends=("elf",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "DT_FLAGS/DT_FLAGS_1 symbolic names from PT_DYNAMIC -- None "
            "already unambiguously means 'not captured' (legacy "
            "snapshot written before this field existed); an empty "
            "frozenset means 'parsed ELF carrying no dynamic flags'. "
            "Plain case (b) conversion, same shape as the other two "
            "ElfMetadata siblings below."
        ),
    ),
    _E(
        owner="ElfMetadata",
        field="has_init",
        value_type="bool | None",
        producing_backends=("elf",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Whether the ELF carries a DT_INIT/.init_array constructor "
            "entry point -- None means 'not captured', False means "
            "'parsed ELF confirmed to have none'. Plain case (b) "
            "conversion."
        ),
    ),
    _E(
        owner="ElfMetadata",
        field="has_fini",
        value_type="bool | None",
        producing_backends=("elf",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "Whether the ELF carries a DT_FINI/.fini_array destructor "
            "entry point -- same shape as has_init_fact above."
        ),
    ),
    _E(
        owner="PeMetadata",
        field="delay_imports",
        value_type="dict[str, list[str]] | None",
        producing_backends=("pe",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT contents -- None means "
            "'not captured' (legacy snapshot), an empty dict means "
            "'parsed PE with no delay-load directory'. Plain case (b) "
            "conversion, the PE analogue of ElfMetadata.dynamic_flags_"
            "fact above."
        ),
    ),
    _E(
        owner="MachoMetadata",
        field="rpaths",
        value_type="list[str] | None",
        producing_backends=("macho",),
        persisted=True,
        identity_relevant=False,
        comparable=True,
        suppressible=False,
        reportable=True,
        lifecycle=FactLifecycle.PERSISTED,
        notes=(
            "LC_RPATH runtime search paths -- the Mach-O analogue of "
            "ELF's DT_RUNPATH. None means 'not captured' (legacy "
            "snapshot), an empty list means 'parsed Mach-O carrying no "
            "LC_RPATH commands'. Plain case (b) conversion."
        ),
    ),
]
