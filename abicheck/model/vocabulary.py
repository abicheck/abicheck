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

"""Closed vocabularies every ABI entity is described with.

Visibility, access level, parameter passing kind, and the ADR-024 Origin
axis. Leaf module: no first-party imports, so every other model module and
every consumer package can depend on it without ordering concerns.
"""

from __future__ import annotations

from enum import Enum


class Visibility(str, Enum):
    PUBLIC = "public"  # default visibility / exported
    HIDDEN = "hidden"  # __attribute__((visibility("hidden")))
    ELF_ONLY = "elf_only"  # present in ELF symbol table, not in headers


class ElfVisibility(str, Enum):
    """ELF st_other visibility from .dynsym — separate from API-level Visibility."""

    DEFAULT = "default"  # STV_DEFAULT
    PROTECTED = "protected"  # STV_PROTECTED
    HIDDEN = "hidden"  # STV_HIDDEN
    INTERNAL = "internal"  # STV_INTERNAL


class AccessLevel(str, Enum):
    PUBLIC = "public"
    PROTECTED = "protected"
    PRIVATE = "private"


class ParamKind(str, Enum):
    VALUE = "value"
    POINTER = "pointer"
    REFERENCE = "reference"
    RVALUE_REF = "rvalue_ref"


class ScopeOrigin(str, Enum):
    """Where a declaration's defining header sits relative to the
    user-provided public-header set — the *Origin* axis of the two-axis
    Linkage × Origin surface model (ADR-024 D1, ADR-015 schema v6).

    Classification is opt-in: it is only meaningful when the caller
    supplies a public-header set (``-H``/``--header``; ``scan`` also takes
    ``--public-header-dir``).
    Without one, every declaration is ``UNKNOWN`` and downstream behaviour
    is unchanged.
    """

    PUBLIC_HEADER = "public_header"  # defined in a provided public header
    PRIVATE_HEADER = "private_header"  # project header outside the public set
    SYSTEM_HEADER = "system_header"  # toolchain/system header (/usr/include, ...)
    GENERATED = "generated"  # machine-generated header (moc_*, *.pb.h, generated/ ...)
    EXPORT_ONLY = "export_only"  # exported by the binary but absent from any header
    UNKNOWN = "unknown"  # no public set, or no source location


#: Valid ``Change.evidence_provenance`` entries (G39). A bare
#: ``tuple[str, ...]`` field deliberately stays untyped (see the plan's own
#: Phase 0 section for why: a bespoke enum-per-field would compete with the
#: string-ref shape ``contract_evidence_refs`` already established) -- this
#: frozenset is the single code-level owner the plan's Phase 0 section
#: requires before Phase 1 wires any real call site, so a typo'd or
#: independently-invented tag fails a check instead of silently shipping
#: as an unrecognized value no consumer can key off
#: (docs/contribute/plans/g39-per-finding-evidence-provider-model.md).
#: Extended alongside each new Phase 1 slice -- never hand-invented at a
#: detector call site.
EVIDENCE_PROVENANCE_TAGS: frozenset[str] = frozenset(
    {
        # diff_platform_elf_dynamic._diff_security_hardening's
        # STACK_CANARY_REMOVED/FORTIFY_SOURCE_WEAKENED (G39 Phase 1,
        # first sub-slice): has_stack_canary/has_fortify_source are
        # .dynsym import/symbol-name derived, read identically on both
        # sides.
        "both:l0:elf_symtab",
        # G39 Phase 1, second sub-slice (the remaining hardening kinds
        # in the same detector): ELF `.dynamic` section entries read
        # directly (not via .dynsym) -- already named in the plan's own
        # Phase 0 vocabulary table (ElfMetadata.soname/DT_SONAME, not
        # yet wired to a detector at the time that table was written;
        # PIE_DISABLED/RELRO_WEAKENED are the first real producers).
        "both:l0:elf_dynamic",
        # ELF program-header/segment flags (PT_GNU_RELRO, PT_GNU_STACK,
        # PT_LOAD) -- distinct from l0:elf_dynamic's .dynamic-section
        # reads. New in this sub-slice: RELRO_WEAKENED (combined with
        # l0:elf_dynamic's bind_now flag), WRITABLE_EXECUTABLE_SEGMENT,
        # EXECUTABLE_STACK/EXECUTABLE_STACK_REMOVED.
        "both:l0:elf_program_headers",
        # The ELF file header itself (e.g. e_type/ET_DYN) -- distinct
        # from both the .dynamic section and program headers. New in
        # this sub-slice: PIE_DISABLED (is_pie gates DF_1_PIE on
        # ET_DYN).
        "both:l0:elf_header",
    }
)
