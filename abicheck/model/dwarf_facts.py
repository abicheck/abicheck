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

"""DWARF debug-information facts as data.

The record/enum layout dataclasses ``abicheck.dwarf_metadata`` produces and the
toolchain/advanced facts ``abicheck.dwarf_advanced`` produces. Both parsers
fill these in; neither shape depends on either parser (ADR-061 Phase 5)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FieldInfo:
    """One field (member) inside a struct/union/class."""

    name: str
    type_name: str  # human-readable type (e.g. "int", "MyStruct *")
    byte_offset: int  # DW_AT_data_member_location
    byte_size: int  # size of the field's type (0 if unknown)
    bit_offset: int = 0  # for bitfields: normalised bit offset from LSB
    bit_size: int = 0  # for bitfields: width in bits (0 = not a bitfield)


@dataclass
class StructLayout:
    """Size and field layout of a struct/class/union."""

    name: str
    byte_size: int  # DW_AT_byte_size
    alignment: int = 0  # DW_AT_alignment (DWARF 5; 0 = unknown)
    fields: list[FieldInfo] = field(default_factory=list)
    is_union: bool = False
    # Defining source header, when the debug info records it. DWARF leaves this
    # None (decl-file is resolved on the DIE path); the PDB pipeline fills it
    # from LF_UDT_SRC_LINE / LF_UDT_MOD_SRC_LINE so provenance (ADR-024 Phase 1)
    # works for Windows binaries.
    decl_file: str | None = None


@dataclass
class EnumInfo:
    """Enum type: underlying integer type + all named members."""

    name: str
    underlying_byte_size: int  # sizeof underlying integer type
    members: dict[str, int] = field(default_factory=dict)  # name → value
    # Defining source header — see StructLayout.decl_file (ADR-024 Phase 1).
    decl_file: str | None = None


@dataclass
class DwarfMetadata:
    """All DWARF-derived ABI-relevant type information from one .so.

    Implements the TypeMetadataSource protocol (see type_metadata.py).
    """

    # name → StructLayout  (structs, classes, unions)
    structs: dict[str, StructLayout] = field(default_factory=dict)
    # name → EnumInfo
    enums: dict[str, EnumInfo] = field(default_factory=dict)
    # DW_TAG_base_type name → DW_AT_byte_size. Captures scalar sizes whose ABI
    # can shift without any signature or mangling change — notably `long double`
    # under -mlong-double-64/-mabi=ibmlongdouble (G23 D2, same-mangling case).
    base_types: dict[str, int] = field(default_factory=dict)
    has_dwarf: bool = False  # False = binary had no DWARF info
    # Provenance for assurance receipts.  ``has_dwarf`` alone deliberately
    # cannot say whether this is a full type parse or binary-depth's cheap
    # section-presence probe, nor whether BTF/CTF was adapted into this
    # DWARF-shaped compatibility model. P1 review: appended after every
    # pre-existing field and marked keyword-only (mirrors
    # AdvancedDwarfMetadata's identical provenance fields below) so an
    # external caller still constructing this dataclass positionally
    # cannot silently bind a value to the wrong field.
    evidence_source: str = field(
        default="dwarf", kw_only=True
    )  # dwarf | btf | ctf | pdb | unknown
    evidence_state: str = field(
        default="not_available", kw_only=True
    )  # parsed | partial | presence_only | failed | not_available
    # Extraction accounting.  These remain zero for formats/producers which
    # cannot expose CU-level progress (and for old serialized snapshots).
    cu_total: int = field(default=0, kw_only=True)
    cu_failed: int = field(default=0, kw_only=True)

    # TypeMetadataSource protocol methods
    @property
    def has_data(self) -> bool:
        return self.has_dwarf

    def get_struct_layout(self, name: str) -> StructLayout | None:
        return self.structs.get(name)

    def get_enum_info(self, name: str) -> EnumInfo | None:
        return self.enums.get(name)


@dataclass
class ToolchainInfo:
    """Parsed DW_AT_producer metadata from a binary."""

    producer_string: str = ""  # raw DW_AT_producer value
    compiler: str = ""  # "GCC", "clang", "ICC" (ICC/ICX/DPC++ family)
    version: str = ""  # e.g. "13.2.1"
    abi_flags: set[str] = field(default_factory=set)  # extracted ABI-affecting flags
    vector_abi_flags: set[str] = field(
        default_factory=set
    )  # vector-function (SIMD clone) ABI flags
    wchar_flags: set[str] = field(
        default_factory=set
    )  # -fshort-wchar / -fno-short-wchar


@dataclass
class AdvancedDwarfMetadata:
    """Sprint 4 metadata extracted from a single .so."""

    has_dwarf: bool = False
    # Normalized target architecture (_normalize_arch): "x86_64", "aarch64",
    # "i386", … Empty string when unknown (e.g. arch-less mock snapshots).
    # Gates the SysV-AMD64-specific aggregate-return-convention classification.
    target_arch: str = ""
    toolchain: ToolchainInfo = field(default_factory=ToolchainInfo)
    # linkage_name (mangled) → CC string for ALL externally-visible functions visited.
    # Storing "normal" explicitly lets the diff distinguish "became normal" from
    # "function was removed/added" (sparse dict would conflate the two cases).
    # NOTE: on Linux x86-64 this dict mostly contains "normal" entries since
    # DW_AT_calling_convention is rarely emitted by GCC/Clang for System V AMD64.
    calling_conventions: dict[str, str] = field(default_factory=dict)
    # linkage_name (mangled) → value ABI trait fingerprint derived from DWARF types.
    # Used as fallback signal when DW_AT_calling_convention is not emitted.
    # Example: "ret:v(trivial)" -> "ret:v(nontrivial)" can imply SysV ABI drift.
    value_abi_traits: dict[str, str] = field(default_factory=dict)
    # linkage_name (mangled) → byte size of a by-value aggregate *return* type.
    # Used only to label a return triviality flip: a SysV AMD64 aggregate is
    # returned in registers only when it is trivial AND <= 16 bytes; a larger
    # struct is memory-returned regardless of triviality, so a triviality change
    # there is a value-ABI (copy-semantics) change, not a register<->sret flip.
    return_value_sizes: dict[str, int] = field(default_factory=dict)
    # linkage_name (mangled) → set membership when the by-value aggregate return
    # is forced to memory (sret) by an unaligned member (e.g. a packed struct).
    # Such a type is memory-returned regardless of size/triviality, so a
    # triviality flip there is never a register<->sret convention change.
    return_memory_classified: set[str] = field(default_factory=set)
    # struct names where any field has a misaligned byte offset → __attribute__((packed))
    packed_structs: set[str] = field(default_factory=set)
    # All struct/class names seen (for cross-referencing in diff to avoid
    # false "packing removed" when a struct was simply deleted)
    all_struct_names: set[str] = field(default_factory=set)
    # linkage_name → CFA register name for exported functions (from .eh_frame / .debug_frame).
    # Typically "rsp" or "rbp" on x86-64; empty string when not present.
    # A change from "rbp" (frame-pointer) to "rsp" (stack-pointer) or vice-versa
    # indicates a calling-convention / frame-layout drift (#117).
    frame_registers: dict[str, str] = field(default_factory=dict)
    # linkage_name → frozenset of callee-saved register names for exported functions.
    # Derived from CFI DW_CFA_offset / DW_CFA_rel_offset rules in the function prologue.
    # On x86-64 SysV ABI the callee-saved set is {rbx,rbp,r12-r15}.
    # On x86-64 ms_abi (Windows x64) it additionally includes {rdi,rsi,r10,r11}.
    # Presence of rdi/rsi in the saved-registers set is a strong ELF-level signal
    # that the function uses ms_abi, even when DW_AT_calling_convention is absent (GCC gap).
    callee_saved_regs: dict[str, frozenset[str]] = field(default_factory=dict)
    # Provenance for assurance receipts.  See DwarfMetadata.evidence_state.
    # BTF/CTF explicitly use ``not_supported``: their basic layouts must
    # never be represented as DWARF calling-convention/value-ABI evidence.
    # Appended after every pre-existing field (rather than interleaved
    # with them) and marked keyword-only so an external caller that still
    # constructs this dataclass positionally (e.g.
    # ``AdvancedDwarfMetadata(True, "x86_64", toolchain)``) cannot silently
    # bind a value to the wrong field.
    evidence_state: str = field(
        default="not_available", kw_only=True
    )  # parsed | partial | presence_only | failed | not_supported | not_available
    # See DwarfMetadata.cu_total/cu_failed.  Advanced and basic walks can
    # fail independently, so each channel owns its accounting.
    cu_total: int = field(default=0, kw_only=True)
    cu_failed: int = field(default=0, kw_only=True)
