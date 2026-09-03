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

"""First of three (1/3) of ChangeKind's (name, value) pairs (ADR-061 D9 / model-vs-policy split).

Split purely by original declaration-order position -- 1 of 3 roughly-equal
chunks by line count, never by taxonomy category -- specifically so the
assembled enum's member order is byte-identical to the single-class
definition this replaces (`model/change_catalog/kinds.py` concatenates all
three IN THIS ORDER via the functional `Enum()` API). A taxonomy-based split
was considered and rejected: nothing here needs it (this is the enum's own
declaration, not `ChangeKindMeta` metadata, which is already correctly
taxonomy-split across `symbols.py`/`types.py`/`platform.py`/`build.py`/
`source.py`), and reordering by taxonomy risks changing `list(ChangeKind)`
iteration order for no benefit.

Exists only because a single ~950-line class body exceeds this repository's
800-line production file-size cap once physically moved under `model/`
(`scripts/check_architecture.py`'s `new-file-size` check, which applies to
every file under `abicheck/` regardless of classification, and which
explicitly forbids adding a *new* file to `architecture/debt.yaml`'s
adoption-debt ledger -- confirmed by reading that check directly, not
assumed -- so a debt-ledger exemption was never an available shortcut here).
Leaf module: no internal imports, matching `model/change_catalog/registry.py`'s
own leaf contract.

Each entry is `(member_name, member_value, comment_or_None)`; `kinds.py`
reassembles them with `Enum(..., type=str)`. A `None` comment means the
original declaration carried no inline documentation for that member.
"""

from __future__ import annotations

KIND_NAMES_1: tuple[tuple[str, str, str | None], ...] = (
    ("FUNC_REMOVED", "func_removed", "public symbol removed → BREAKING"),
    (
        "FUNC_REMOVED_ELF_ONLY",
        "func_removed_elf_only",
        "exported ELF-only function removed -> binary break",
    ),
    ("FUNC_ADDED", "func_added", "new public symbol → COMPATIBLE"),
    ("FUNC_RETURN_CHANGED", "func_return_changed", "return type changed → BREAKING"),
    (
        "FUNC_PARAMS_CHANGED",
        "func_params_changed",
        "parameter types changed → BREAKING",
    ),
    (
        "FUNC_NOEXCEPT_ADDED",
        "func_noexcept_added",
        "noexcept added → BREAKING (C++17 P0012R1: noexcept is part of function type)",
    ),
    (
        "FUNC_NOEXCEPT_REMOVED",
        "func_noexcept_removed",
        "noexcept removed → COMPATIBLE_WITH_RISK (C++17: part of fn-pointer/template mangling; source risk)",
    ),
    (
        "FUNC_VIRTUAL_ADDED",
        "func_virtual_added",
        "became virtual → vtable change → BREAKING",
    ),
    ("FUNC_VIRTUAL_REMOVED", "func_virtual_removed", "→ BREAKING"),
    (
        "VIRTUAL_METHOD_ADDED",
        "virtual_method_added",
        'a brand-new virtual *method* added to a class that already exists across -- versions → grows/relayouts the vtable, breaking derived classes (and the -- vptr if the class had none). Catches the KDE "add a virtual to a non-leaf -- class" rule when the vtable array itself is not diff-able (DWARF/symbol-only -- snapshots), where it would otherwise be mistaken for a compatible func_added. -- → BREAKING',
    ),
    ("VAR_REMOVED", "var_removed", None),
    ("VAR_ADDED", "var_added", None),
    ("VAR_TYPE_CHANGED", "var_type_changed", None),
    (
        "TYPE_SIZE_CHANGED",
        "type_size_changed",
        "Type changes -- struct/class layout change → BREAKING",
    ),
    ("TYPE_ALIGNMENT_CHANGED", "type_alignment_changed", "alignment change → BREAKING"),
    ("TYPE_FIELD_REMOVED", "type_field_removed", "→ BREAKING"),
    ("TYPE_FIELD_ADDED", "type_field_added", "if in non-final class, may be BREAKING"),
    ("TYPE_FIELD_OFFSET_CHANGED", "type_field_offset_changed", "→ BREAKING"),
    ("TYPE_FIELD_TYPE_CHANGED", "type_field_type_changed", "→ BREAKING"),
    ("TYPE_BASE_CHANGED", "type_base_changed", "inheritance change → BREAKING"),
    ("TYPE_VTABLE_CHANGED", "type_vtable_changed", "→ BREAKING"),
    ("TYPE_ADDED", "type_added", "new type → COMPATIBLE"),
    ("TYPE_REMOVED", "type_removed", "type removed → BREAKING if used in API"),
    (
        "TYPE_FIELD_ADDED_COMPATIBLE",
        "type_field_added_compatible",
        "appended to standard-layout non-polymorphic type",
    ),
    ("ENUM_MEMBER_REMOVED", "enum_member_removed", "Enum changes"),
    (
        "ENUM_MEMBER_ADDED",
        "enum_member_added",
        "BREAKING (closed enums / value shift risk)",
    ),
    ("ENUM_MEMBER_VALUE_CHANGED", "enum_member_value_changed", None),
    (
        "ENUM_LAST_MEMBER_VALUE_CHANGED",
        "enum_last_member_value_changed",
        "sentinel changed",
    ),
    ("TYPEDEF_REMOVED", "typedef_removed", "placed here for logical grouping"),
    ("FUNC_STATIC_CHANGED", "func_static_changed", "Method qualifier changes"),
    ("FUNC_CV_CHANGED", "func_cv_changed", "const/volatile on this"),
    (
        "FUNC_VISIBILITY_CHANGED",
        "func_visibility_changed",
        "default→hidden: symbol gone from ABI",
    ),
    (
        "FUNC_VISIBILITY_PROTECTED_CHANGED",
        "func_visibility_protected_changed",
        "default↔protected: interposition semantics changed, symbol still exported",
    ),
    ("FUNC_PURE_VIRTUAL_ADDED", "func_pure_virtual_added", "Virtual changes"),
    ("FUNC_VIRTUAL_BECAME_PURE", "func_virtual_became_pure", None),
    ("UNION_FIELD_ADDED", "union_field_added", "Union field changes"),
    ("UNION_FIELD_REMOVED", "union_field_removed", None),
    ("UNION_FIELD_TYPE_CHANGED", "union_field_type_changed", None),
    ("TYPEDEF_BASE_CHANGED", "typedef_base_changed", "Typedef changes"),
    ("FIELD_BITFIELD_CHANGED", "field_bitfield_changed", "Bitfield changes"),
    (
        "SONAME_CHANGED",
        "soname_changed",
        "── ELF-only (Sprint 2) ────────────────────────────────────────────── -- Dynamic section contract",
    ),
    ("SONAME_MISSING", "soname_missing", "old library had no SONAME — bad practice"),
    (
        "VISIBILITY_LEAK",
        "visibility_leak",
        "library exports internal symbols without -fvisibility=hidden",
    ),
    ("NEEDED_ADDED", "needed_added", "new DT_NEEDED dep"),
    ("NEEDED_REMOVED", "needed_removed", "dep dropped"),
    ("RPATH_CHANGED", "rpath_changed", None),
    ("RUNPATH_CHANGED", "runpath_changed", None),
    (
        "COMPAT_VERSION_CHANGED",
        "compat_version_changed",
        "── Mach-O specific ────────────────────────────────────────────────── -- LC_ID_DYLIB compat_version changed → BREAKING",
    ),
    (
        "MACHO_CPU_TYPE_CHANGED",
        "macho_cpu_type_changed",
        "Mach-O header CPU type/arch changed → BREAKING",
    ),
    (
        "PE_FORWARDER_CHANGED",
        "pe_forwarder_changed",
        "── PE/COFF specific (binary-only, no PDB needed) ──────────────────── -- export forwarder target repointed",
    ),
    ("PE_MACHINE_CHANGED", "pe_machine_changed", "PE machine/architecture drift"),
    (
        "EXECUTABLE_STACK",
        "executable_stack",
        "ELF security / bad practice -- PT_GNU_STACK gains PF_X — NX disabled (regression; gateable)",
    ),
    (
        "EXECUTABLE_STACK_REMOVED",
        "executable_stack_removed",
        "PT_GNU_STACK loses PF_X — hardening improvement (informational)",
    ),
    (
        "RELRO_WEAKENED",
        "relro_weakened",
        "checksec-equivalent hardening regressions (see G12). RISK by default; -- gateable to break via the shipped security policy. -- full→partial / →none RELRO",
    ),
    ("PIE_DISABLED", "pie_disabled", "PIE executable → non-PIE"),
    ("STACK_CANARY_REMOVED", "stack_canary_removed", "-fstack-protector dropped"),
    ("FORTIFY_SOURCE_WEAKENED", "fortify_source_weakened", "_FORTIFY_SOURCE dropped"),
    (
        "WRITABLE_EXECUTABLE_SEGMENT",
        "writable_executable_segment",
        "W^X violation introduced",
    ),
    (
        "SYMBOL_BINDING_CHANGED",
        "symbol_binding_changed",
        "Symbol metadata drift (ELF .dynsym) -- GLOBAL→WEAK (breaking)",
    ),
    (
        "SYMBOL_BINDING_STRENGTHENED",
        "symbol_binding_strengthened",
        "WEAK→GLOBAL (compatible)",
    ),
    ("SYMBOL_TYPE_CHANGED", "symbol_type_changed", "FUNC→OBJECT, etc."),
    ("SYMBOL_SIZE_CHANGED", "symbol_size_changed", "st_size changed"),
    (
        "SYMBOL_SIZE_CHANGED_INTERNAL",
        "symbol_size_changed_internal",
        "st_size changed on an internal-looking (reserved/underscore-prefixed) -- exported data symbol; exported data size drift is breaking by default.",
    ),
    (
        "SYMBOL_SIZE_CHANGED_CONST_OBJECT",
        "symbol_size_changed_const_object",
        "st_size changed on a public const string-like object, e.g. -- extern char const version[]. Old non-PIE executables can still carry copy -- relocations sized from the old DSO symbol, so this remains breaking.",
    ),
    ("IFUNC_INTRODUCED", "ifunc_introduced", "→ STT_GNU_IFUNC"),
    ("IFUNC_REMOVED", "ifunc_removed", "STT_GNU_IFUNC →"),
    ("COMMON_SYMBOL_RISK", "common_symbol_risk", "STT_COMMON exported"),
    (
        "SYMBOL_VERSION_DEFINED_REMOVED",
        "symbol_version_defined_removed",
        "Symbol versioning contract",
    ),
    (
        "SYMBOL_VERSION_DEFINED_ADDED",
        "symbol_version_defined_added",
        "versioning introduced",
    ),
    (
        "SYMBOL_VERSION_REQUIRED_ADDED",
        "symbol_version_required_added",
        "new GLIBC_X — newer than old max (BREAKING)",
    ),
    (
        "SYMBOL_VERSION_REQUIRED_ADDED_COMPAT",
        "symbol_version_required_added_compat",
        "added but older than old max (COMPATIBLE)",
    ),
    ("SYMBOL_VERSION_REQUIRED_REMOVED", "symbol_version_required_removed", None),
    (
        "DWARF_INFO_MISSING",
        "dwarf_info_missing",
        "DWARF layout (Sprint 3) -- new binary stripped of -g",
    ),
    (
        "EVIDENCE_COVERAGE_ASYMMETRIC",
        "layer_coverage_asymmetric",
        "base scanned with evidence the target lacks",
    ),
    (
        "EVIDENCE_REQUIRED_MISSING",
        "evidence_required_missing",
        "policy require_evidence layer absent (ADR-033 D7)",
    ),
    (
        "VERSIONED_SYMBOL_SCHEME_DETECTED",
        "versioned_symbol_scheme_detected",
        "bulk removed↔added differ only by a version token (ICU u_*_NN / GNU symver); advisory",
    ),
    (
        "SUPPRESSION_WOULD_HIDE_PUBLIC_BREAK",
        "suppression_would_hide_public_break",
        "a suppression rule matched but was withheld because the change is public-reachable (ADR-044 D4); advisory",
    ),
    (
        "SUPPRESSION_REACHABILITY_UNKNOWN",
        "suppression_reachability_unknown",
        "a suppression rule using reachability: proven-unreachable-only matched but was withheld because graph coverage could not prove the change unreachable (impact-analysis-layer P0); advisory",
    ),
    ("STRUCT_SIZE_CHANGED", "struct_size_changed", "sizeof(T) changed"),
    ("STRUCT_FIELD_OFFSET_CHANGED", "struct_field_offset_changed", "field moved"),
    ("STRUCT_FIELD_REMOVED", "struct_field_removed", "field deleted"),
    (
        "STRUCT_FIELD_TYPE_CHANGED",
        "struct_field_type_changed",
        "field type/size changed",
    ),
    ("STRUCT_ALIGNMENT_CHANGED", "struct_alignment_changed", "alignof(T) changed"),
    ("ENUM_UNDERLYING_SIZE_CHANGED", "enum_underlying_size_changed", "int→long"),
    (
        "CALLING_CONVENTION_CHANGED",
        "calling_convention_changed",
        "DWARF advanced (Sprint 4) -- DW_AT_calling_convention drift",
    ),
    (
        "VALUE_ABI_TRAIT_CHANGED",
        "value_abi_trait_changed",
        "DWARF triviality-based calling conv heuristic",
    ),
    (
        "STRUCT_PACKING_CHANGED",
        "struct_packing_changed",
        "__attribute__((packed)) added/removed",
    ),
    (
        "TYPE_VISIBILITY_CHANGED",
        "type_visibility_changed",
        "typeinfo/vtable visibility changed",
    ),
    (
        "TOOLCHAIN_FLAG_DRIFT",
        "toolchain_flag_drift",
        "-fshort-enums/-fpack-struct drift",
    ),
    (
        "FRAME_REGISTER_CHANGED",
        "frame_register_changed",
        "CFA/frame-pointer convention changed (#117)",
    ),
    (
        "VECTOR_ABI_CHANGED",
        "vector_abi_changed",
        "Vector-function (SIMD clone) ABI selection drifted between versions: -- the vectorized call variants of a function resolve to a different -- ABI. Detected from vector-ABI compiler flags in DW_AT_producer -- (-mveclibabi= GCC, -fveclib= clang, -vecabi= Intel-style).",
    ),
    (
        "FUNC_DELETED",
        "func_deleted",
        "Sprint 2 — gap detectors -- = delete added → BREAKING (was callable)",
    ),
    ("VAR_BECAME_CONST", "var_became_const", "non-const → const: writes → SIGSEGV"),
    (
        "VAR_LOST_CONST",
        "var_lost_const",
        "const → non-const: BREAKING (ODR / inlining)",
    ),
    (
        "TYPE_BECAME_OPAQUE",
        "type_became_opaque",
        "complete → forward-decl only → BREAKING",
    ),
    (
        "TYPE_BECAME_FINAL",
        "type_became_final",
        "`final` class-key specifier transitions (header/castxml only — DWARF and -- the binary carry no `final` information). Source-level: gaining `final` -- breaks any consumer that derives from the class. -- gained `final` → derivation no longer compiles → API_BREAK",
    ),
    (
        "TYPE_LOST_FINAL",
        "type_lost_final",
        "lost `final` → devirtualization desync risk on old binaries → COMPATIBLE_WITH_RISK",
    ),
    (
        "TYPE_BECAME_ABSTRACT",
        "type_became_abstract",
        "`abstract` (>=1 pure virtual) transitions (header/castxml only — DWARF -- and the binary carry no such trait directly). -- gained a pure virtual → direct instantiation no longer compiles → API_BREAK",
    ),
    (
        "TYPE_LOST_ABSTRACT",
        "type_lost_abstract",
        "lost all pure virtuals → newly instantiable, purely additive → COMPATIBLE",
    ),
    (
        "BASE_CLASS_POSITION_CHANGED",
        "base_class_position_changed",
        "base reorder → this-ptr offset change",
    ),
    (
        "BASE_CLASS_VIRTUAL_CHANGED",
        "base_class_virtual_changed",
        "base became virtual or non-virtual",
    ),
    (
        "ENUM_MEMBER_RENAMED",
        "enum_member_renamed",
        "── Sprint 7 — Full ABICC parity + beyond ──────────────────────────── -- Source-level breaks (not binary ABI, but API contract) -- same value, different name → API_BREAK",
    ),
    (
        "PARAM_DEFAULT_VALUE_CHANGED",
        "param_default_value_changed",
        "default arg changed",
    ),
    (
        "PARAM_DEFAULT_VALUE_REMOVED",
        "param_default_value_removed",
        "default arg removed → API_BREAK",
    ),
    ("FIELD_RENAMED", "field_renamed", "same offset+type, different name"),
    ("PARAM_RENAMED", "param_renamed", "parameter name changed"),
    ("FIELD_BECAME_CONST", "field_became_const", "Field qualifier changes"),
    ("FIELD_LOST_CONST", "field_lost_const", None),
    ("FIELD_BECAME_VOLATILE", "field_became_volatile", None),
    ("FIELD_LOST_VOLATILE", "field_lost_volatile", None),
    ("FIELD_BECAME_MUTABLE", "field_became_mutable", None),
    ("FIELD_LOST_MUTABLE", "field_lost_mutable", None),
    (
        "FIELD_DEFAULT_INITIALIZER_REMOVED",
        "field_default_initializer_removed",
        "Default member initializer changes (header/castxml only). Gaining one is -- not tracked (matches PARAM_DEFAULT_VALUE_*'s convention: an added default -- is purely additive, never itself flagged). -- lost implicit init → uninitialized-read risk → COMPATIBLE_WITH_RISK",
    ),
    (
        "FIELD_DEFAULT_INITIALIZER_CHANGED",
        "field_default_initializer_changed",
        "value changed → silent behavior change → COMPATIBLE",
    ),
    (
        "PARAM_POINTER_LEVEL_CHANGED",
        "param_pointer_level_changed",
        "Pointer level changes -- T* → T** or T** → T*",
    ),
    ("RETURN_POINTER_LEVEL_CHANGED", "return_pointer_level_changed", "return T* → T**"),
    (
        "METHOD_ACCESS_CHANGED",
        "method_access_changed",
        "Access level changes -- public→protected/private",
    ),
    ("FIELD_ACCESS_CHANGED", "field_access_changed", "public→private field"),
    (
        "ANON_FIELD_CHANGED",
        "anon_field_changed",
        "Anonymous struct/union -- anon struct/union member changed",
    ),
    (
        "VAR_VALUE_CHANGED",
        "var_value_changed",
        "── ABICC full parity — remaining gaps ───────────────────────────────── -- Global data value -- global data initial value changed",
    ),
    (
        "TYPE_KIND_CHANGED",
        "type_kind_changed",
        "Aggregate kind change -- union-involving transition (struct→union, union→struct, class→union, union→class)",
    ),
    (
        "SOURCE_LEVEL_KIND_CHANGED",
        "source_level_kind_changed",
        "struct↔class transition (non-breaking, source-only)",
    ),
    (
        "USED_RESERVED_FIELD",
        "used_reserved_field",
        "Reserved field -- __reserved field put into use",
    ),
    (
        "REMOVED_CONST_OVERLOAD",
        "removed_const_overload",
        "Const overload removal -- const method overload removed",
    ),
    (
        "PARAM_RESTRICT_CHANGED",
        "param_restrict_changed",
        "Parameter restrict qualifier -- restrict qualifier added/removed",
    ),
    (
        "PARAM_BECAME_VA_LIST",
        "param_became_va_list",
        "Parameter va_list -- fixed param → va_list",
    ),
    ("PARAM_LOST_VA_LIST", "param_lost_va_list", "va_list → fixed param"),
    (
        "CONSTANT_CHANGED",
        "constant_changed",
        "Preprocessor constants -- #define value changed",
    ),
    ("CONSTANT_ADDED", "constant_added", "new #define"),
    ("CONSTANT_REMOVED", "constant_removed", "#define removed"),
    (
        "VAR_ACCESS_CHANGED",
        "var_access_changed",
        "Global data access level -- public→private/protected variable (narrowing)",
    ),
    (
        "VAR_ACCESS_WIDENED",
        "var_access_widened",
        "private/protected→public variable (widening)",
    ),
)
