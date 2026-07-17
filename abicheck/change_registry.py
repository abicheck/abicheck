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

"""Single-declaration ChangeKind registry — colocated metadata.

Each ChangeKind declares ALL its metadata in one place:
  - default_verdict (BREAKING / API_BREAK / COMPATIBLE / COMPATIBLE_WITH_RISK)
  - impact text (human-readable explanation of what goes wrong)
  - is_addition flag (for ADDITION_KINDS subset of COMPATIBLE)
  - policy_overrides (per-policy verdict downgrades)

The classification sets (BREAKING_KINDS, COMPATIBLE_KINDS, etc.) and the
IMPACT_TEXT / POLICY_REGISTRY dicts are all DERIVED from this registry.
Adding a new ChangeKind = adding one entry here — no shotgun surgery.

Architecture review: Problem A — eliminates scattered metadata across 5+ locations.
"""
from __future__ import annotations

from .change_registry_buildsource import BUILDSOURCE_EXTENSION_ENTRIES
from .change_registry_castxml import CASTXML_EXTENSION_ENTRIES
from .change_registry_composition import COMPOSITION_EXTENSION_ENTRIES
from .change_registry_coverage import COVERAGE_EXTENSION_ENTRIES
from .change_registry_numpy import NUMPY_EXTENSION_ENTRIES
from .change_registry_suppression import SUPPRESSION_EXTENSION_ENTRIES
from .change_registry_types import (  # noqa: F401
    ChangeKindMeta as ChangeKindMeta,
    ChangeKindRegistry as ChangeKindRegistry,
    Verdict as Verdict,
)
from .change_registry_wheel import WHEEL_DEPLOYMENT_EXTENSION_ENTRIES

# ---------------------------------------------------------------------------
# Registry entries — single source of truth for all ChangeKind metadata
# ---------------------------------------------------------------------------

_B = Verdict.BREAKING
_C = Verdict.COMPATIBLE
_A = Verdict.API_BREAK
_R = Verdict.COMPATIBLE_WITH_RISK
_E = ChangeKindMeta

REGISTRY = ChangeKindRegistry([
    # ── Function / variable changes ────────────────────────────────────────
    _E("func_removed", _B,
       impact="Old binaries call a symbol that no longer exists; dynamic linker will refuse to load or crash at call site."),
    _E("func_removed_elf_only", _B,
       impact="Exported function symbol removed from the binary; old binaries that link or dlsym() it can fail even without header evidence."),
    _E("func_added", _C, is_addition=True,
       impact="New function available; existing binaries are unaffected.",
       description_template="New public function: {new}"),
    _E("func_return_changed", _B,
       impact="Callers expect the old return type layout in registers/stack; misinterpretation causes data corruption.",
       description_template="Return type changed: {name}"),
    _E("func_params_changed", _B,
       impact="Callers push arguments with the old layout; callee reads wrong data from stack/registers.",
       description_template="Parameters changed: {name}"),
    _E("func_noexcept_added", _C,
       impact="In C++17 noexcept is part of the function type; old callers compiled against non-noexcept signature get a different mangled name."),
    _E("func_noexcept_removed", _R,
       impact="`noexcept` removed from a function. Old binaries keep resolving "
              "the symbol, so this is not a binary break — but since C++17 "
              "`noexcept` is part of the function *type*, so it is encoded in "
              "function-pointer and template-argument mangling: consumers that "
              "form a `void(*)() noexcept` pointer or pass the function as a "
              "non-type template argument no longer compile, and code relying on "
              "the guarantee can hit `std::terminate`. KDE's C++ binary-"
              "compatibility policy treats removing `noexcept` as a change to "
              "avoid unless it was `noexcept(false)`. Verdict is policy-"
              "adjustable; raise to API_BREAK under a strict source profile."),
    _E("func_virtual_added", _B,
       impact="Vtable layout changes; old binaries call wrong virtual function slot, leading to crashes or wrong behavior."),
    _E("func_virtual_removed", _B,
       impact="Vtable entry removed; old binaries that dispatch through the vtable call the wrong slot."),
    _E("virtual_method_added", _B,
       impact="A new virtual method was added to a class that already exists across "
              "versions. If the class had no virtuals it gains a hidden vtable pointer "
              "(its size and field offsets shift); if it was already polymorphic the new "
              "slot grows/relayouts the vtable. Either way derived classes compiled "
              "against the old layout dispatch through the wrong slots and old binaries "
              "embedding the type read the wrong offsets. This is the KDE "
              "\"do not add virtuals to a non-leaf class\" rule, caught even when the "
              "snapshot carries no diff-able vtable array (DWARF/symbol-only mode).",
       description_template="New virtual method added to existing class {detail}: {new} — grows/relayouts the vtable, breaking derived classes and old binaries"),
    _E("var_removed", _B,
       impact="Old binaries reference a global variable that no longer exists; link or load failure.",
       description_template="Public variable removed: {name}"),
    _E("var_added", _C, is_addition=True,
       impact="New variable available; existing binaries are unaffected.",
       description_template="New public variable: {name}"),
    _E("var_type_changed", _B,
       impact="Old binaries read/write the variable with wrong size or layout; data corruption or segfault.",
       description_template="Variable type changed: {name}"),

    # ── Type changes ───────────────────────────────────────────────────────
    _E("type_size_changed", _B,
       impact="Old code allocates or copies the type with the old size; heap/stack corruption, out-of-bounds access.",
       description_template="Size changed: {name} ({old} → {new} bits)"),
    _E("type_alignment_changed", _B,
       impact="Misaligned access can cause bus errors on strict architectures or silent data corruption with SIMD.",
       description_template="Alignment changed: {name} ({old} → {new} bits)"),
    _E("type_field_removed", _B,
       impact="Old code accesses a field that no longer exists at the expected offset; reads garbage or writes out of bounds.",
       description_template="Field removed: {name}::{detail}"),
    _E("type_field_added", _B,
       impact="New field shifts subsequent fields; old code reads wrong offsets for all fields after insertion point.",
       description_template="Field added: {name}::{detail}"),
    _E("type_field_offset_changed", _B,
       impact="Old code reads/writes fields at stale offsets; silent data corruption.",
       description_template="Field offset changed: {name}::{detail} ({old} → {new} bits)"),
    _E("type_field_type_changed", _B,
       impact="Field has different size or representation; old code misinterprets the data.",
       description_template="Field type changed: {name}::{detail}"),
    _E("type_base_changed", _B,
       impact="Base class layout change shifts derived member offsets and vtable pointers; this-pointer arithmetic breaks."),
    _E("type_vtable_changed", _B,
       impact="Vtable slot reordering; virtual dispatch calls wrong method."),
    _E("type_added", _C, is_addition=True,
       impact="New type available; existing binaries are unaffected.",
       description_template="New type: {name}"),
    _E("type_removed", _B,
       impact="Old code references a type that no longer exists; compilation or link failure."),
    _E("type_field_added_compatible", _C, is_addition=True,
       impact="Field appended without changing existing offsets; old code works but won't initialize the new field.",
       description_template="Field added: {name}::{detail}"),

    # ── Enum changes ───────────────────────────────────────────────────────
    _E("enum_member_removed", _B,
       impact="Old code uses a constant that no longer exists; compile error for source, stale value for binaries.",
       description_template="Enum member removed: {name}::{detail}"),
    _E("enum_member_added", _C, is_addition=True,
       impact="New enumerator may shift subsequent values in non-fixed enums; switch defaults may miss the new case.",
       description_template="Enum member added: {name}::{detail}"),
    _E("enum_member_value_changed", _B,
       impact="Old binaries use stale numeric values; logic comparisons and switch statements silently break.",
       description_template="Enum member value changed: {name}::{detail}"),
    _E("enum_last_member_value_changed", _R,
       impact="Sentinel/MAX value changed; old code using it for array sizes allocates wrong amount.",
       description_template="Enum member value changed: {name}::{detail}"),
    _E("typedef_removed", _B,
       impact="Old code using the typedef name won't compile; binary impact depends on usage.",
       description_template="Typedef removed: {name}"),

    # ── Method qualifier changes ───────────────────────────────────────────
    _E("func_static_changed", _B,
       impact="Static/non-static transition changes calling convention (implicit this pointer); ABI mismatch.",
       description_template="Static qualifier changed: {name}"),
    _E("func_cv_changed", _B,
       impact="const/volatile on 'this' changes the mangled name; old binaries link to the wrong symbol.",
       description_template="CV qualifier changed: {name}"),
    _E("func_visibility_changed", _B,
       impact="Symbol hidden from dynamic linking; old binaries can't find it at load time.",
       description_template="Function visibility changed to hidden: {name}"),
    _E("func_visibility_protected_changed", _C,
       impact="Symbol visibility changed to STV_PROTECTED. The symbol remains exported and "
              "is still resolvable by external consumers. Interposition via LD_PRELOAD no "
              "longer works for calls originating inside the library itself — intentional "
              "by the library author. Existing compiled consumers are unaffected.",
       description_template="ELF symbol visibility changed: {name} ({old} → {new}); symbol still exported, interposition semantics changed"),

    # ── Virtual changes ────────────────────────────────────────────────────
    _E("func_pure_virtual_added", _B,
       impact="Old subclasses don't implement the pure virtual; instantiation causes linker error or UB.",
       description_template="Function became pure virtual: {name}"),
    _E("func_virtual_became_pure", _B,
       impact="Concrete virtual became pure; old binaries calling it get unresolved dispatch.",
       description_template="Function became pure virtual: {name}"),

    # ── Union field changes ────────────────────────────────────────────────
    _E("union_field_added", _C, is_addition=True,
       impact="Union size may grow; old code allocating with old sizeof gets truncated data.",
       description_template="Union field added: {name}::{detail}"),
    _E("union_field_removed", _B,
       impact="Old code accessing removed alternative reads uninitialized memory.",
       description_template="Union field removed: {name}::{detail}"),
    _E("union_field_type_changed", _B,
       impact="Old code interprets the union member with wrong type layout.",
       description_template="Union field type changed: {name}::{detail}"),

    # ── Typedef changes ────────────────────────────────────────────────────
    _E("typedef_base_changed", _B,
       impact="Underlying type changed; old code using the typedef operates on wrong representation.",
       description_template="Typedef base type changed: {name}"),

    # ── Bitfield changes ───────────────────────────────────────────────────
    _E("field_bitfield_changed", _B,
       impact="Bit-field width or offset changed; old code reads/writes wrong bits.",
       description_template="Bitfield layout changed: {name}::{detail}"),

    # ── ELF-only (Sprint 2) ───────────────────────────────────────────────
    _E("soname_changed", _R,
       impact="SONAME changed. Already-compiled consumers record the old SONAME "
              "in DT_NEEDED and can fail to load unless the old SONAME remains "
              "available. The exported ABI surface may still be compatible, but "
              "deployment action is required."),
    _E("soname_missing", _C,
       impact="Library has no SONAME; package managers and ldconfig cannot track versions.",
       description_template="Old library has no SONAME (bad practice — packaging/ldconfig will fail); new library correctly defines SONAME {new}"),
    _E("visibility_leak", _C,
       impact="Internal symbols exported without -fvisibility=hidden; namespace pollution risk.",
       description_template="Old library exports {detail} internal-looking symbol(s) without -fvisibility=hidden (bad practice — accidental ABI surface enlargement): {name}"),
    _E("needed_added", _C,
       impact="New shared library dependency; may not be available on target systems."),
    _E("needed_removed", _C,
       impact="Dependency removed; should be transparent to consumers."),
    _E("rpath_changed", _C,
       description_template="RPATH changed: {old} → {new}"),
    _E("runpath_changed", _C,
       description_template="RUNPATH changed: {old} → {new}"),

    # ── Mach-O specific ───────────────────────────────────────────────────
    _E("compat_version_changed", _B,
       impact="Mach-O compatibility version changed; dylibs linked against old version may fail to load.",
       description_template="compatibility version changed: {old} → {new}"),
    _E("macho_cpu_type_changed", _B,
       impact="A Mach-O architecture slice that used to ship is gone (e.g. a universal "
              "x86_64+arm64 dylib dropped its x86_64 slice, or x86_64 → arm64). Existing "
              "clients built for the removed architecture can no longer link against or load "
              "the dylib. Adding slices (single-arch → universal) is not flagged.",
       description_template="Mach-O architecture slice removed: {detail} no longer present ({old} → {new}); existing clients of the dropped arch can no longer load the dylib"),

    # ── PE/COFF specific (binary-only, no PDB needed) ─────────────────────
    _E("pe_forwarder_changed", _B,
       impact="A DLL export forwarder was repointed to a different target (DLL!Symbol). The "
              "effective implementation behind the exported name changed; dependent binaries get "
              "different — and possibly missing — behaviour at load time.",
       description_template="export '{name}' forwarder changed: {old} → {new}"),
    _E("pe_machine_changed", _B,
       impact="PE machine/architecture changed (e.g. AMD64 → ARM64); the DLL is a different "
              "architecture and cannot be loaded by existing clients.",
       description_template="PE machine/architecture changed: {old} → {new}"),

    # ── ELF security / bad practice ────────────────────────────────────────
    _E("executable_stack", _C,
       impact="Library has executable stack (PT_GNU_STACK RWE); NX protection disabled — security risk.",
       description_template="Executable stack detected: library linked with -Wl,-z,execstack — NX protection disabled (security risk)"),
    _E("executable_stack_removed", _C,
       impact="Executable stack removed (PT_GNU_STACK RWE→RW); NX protection restored — a hardening improvement, not a regression.",
       description_template="Executable stack removed: library now uses a non-executable stack — NX protection restored (good practice)"),
    # checksec-equivalent hardening regressions (G12). RISK by default so they
    # surface without failing a normal compatibility gate; the shipped
    # `security` policy (policies/security.yaml) flips them to break.
    _E("relro_weakened", _R,
       impact="RELRO protection weakened (e.g. full→partial); the GOT is no longer fully read-only, widening the GOT-overwrite attack surface.",
       description_template="RELRO weakened: {old} → {new}"),
    _E("pie_disabled", _R,
       impact="Position-independent executable disabled; the image loads at a fixed address, defeating ASLR.",
       description_template="PIE disabled: executable is no longer position-independent (ASLR defeated)"),
    _E("stack_canary_removed", _R,
       impact="Stack-smashing protector (-fstack-protector) no longer referenced; stack-buffer overflows are no longer detected at runtime.",
       description_template="Stack canary removed: -fstack-protector no longer referenced"),
    _E("fortify_source_weakened", _R,
       impact="_FORTIFY_SOURCE fortified libc wrappers no longer referenced; compile-time/runtime buffer-overflow checks were dropped.",
       description_template="FORTIFY_SOURCE weakened: fortified libc wrappers (*_chk) no longer referenced"),
    _E("writable_executable_segment", _R,
       impact="A loadable segment is now both writable and executable (W^X violation); injected code in that page becomes executable.",
       description_template="Writable + executable segment introduced (W^X violation)"),

    # ── Symbol metadata drift ──────────────────────────────────────────────
    _E("symbol_binding_changed", _C,
       impact="GLOBAL→WEAK binding lets interposers override unexpectedly; old code may get wrong implementation.",
       description_template="Symbol binding changed: {name} ({old} → {new})"),
    _E("symbol_binding_strengthened", _C,
       impact="WEAK→GLOBAL binding; safe upgrade, interposition still possible via LD_PRELOAD.",
       description_template="Symbol binding changed: {name} ({old} → {new})"),
    _E("symbol_type_changed", _B,
       impact="Symbol type changed (e.g. FUNC→OBJECT); callers using wrong calling convention.",
       description_template="Symbol type changed: {name} ({old} → {new})"),
    _E("symbol_size_changed", _B,
       impact="ELF symbol size changed; copy relocations or memcpy-based consumers get truncated/oversized data.",
       description_template="Symbol size changed: {name} ({old} → {new} bytes)"),
    _E("symbol_size_changed_internal", _B,
       impact="ELF size changed on an internal-looking (reserved/underscore-prefixed) exported data symbol; "
              "exported data remains part of the dynamic ABI and size changes can break copy relocations "
              "or direct data consumers. Override severity via --policy-file only when the symbol is known private.",
       description_template="Symbol size changed: {name} ({old} → {new} bytes)"),
    _E("symbol_size_changed_const_object", _B,
       impact="ELF size changed on a public const string-like object declared without a fixed bound in headers. "
              "Old non-PIE consumers may have copy relocations sized from the old DSO symbol, so a later DSO can "
              "truncate or otherwise mis-copy data at load time.",
       description_template="Symbol size changed: {name} ({old} → {new} bytes)"),
    _E("ifunc_introduced", _C,
       impact="IFUNC resolver indirection added; transparent to well-behaved callers.",
       description_template="Symbol became GNU_IFUNC: {name}"),
    _E("ifunc_removed", _C,
       impact="IFUNC removed; transparent to callers.",
       description_template="Symbol no longer GNU_IFUNC: {name}"),
    _E("common_symbol_risk", _C,
       description_template="Exported STT_COMMON symbol: {name} (resolution depends on linker/loader)"),

    # ── Symbol versioning ──────────────────────────────────────────────────
    _E("symbol_version_defined_removed", _B,
       impact="Defined symbol version removed; old binaries requesting that version get link error.",
       description_template="Symbol version removed: {old}"),
    _E("symbol_version_defined_added", _C,
       impact="New symbol version defined; transparent to existing consumers.",
       description_template="Symbol version definition added: {new}"),
    _E("symbol_version_required_added", _R,
       impact="Requires a newer symbol version than old system provides; may fail to load on older systems.",
       description_template="New symbol version requirement: {name} (from {detail})"),
    _E("symbol_version_required_added_compat", _C,
       impact="New version requirement added but older than existing max; safe on current systems.",
       description_template="New symbol version requirement: {name} (from {detail}) — not newer than previous max, backward-compatible"),
    _E("symbol_version_required_removed", _C,
       impact="Version requirement dropped; broadens compatibility.",
       description_template="Symbol version requirement removed: {name} (from {detail})"),

    # ── DWARF layout (Sprint 3) ───────────────────────────────────────────
    _E("dwarf_info_missing", _C,
       description_template="New binary has no DWARF debug info — struct/enum layout comparison was skipped. Recompile with -g to enable."),
    _E("layer_coverage_asymmetric", _R,
       impact="The base snapshot was analyzed with evidence layers the target "
              "lacks (e.g. debug info, build context, or source ABI). The "
              "comparison is scoped to the layers both sides share, so changes "
              "only the missing layers could prove are not reported. Re-scan "
              "the target with the same inputs to restore full coverage."),
    _E("versioned_symbol_scheme_detected", _R,
       impact="Most removed symbols reappear as added symbols differing only by a "
              "version token in the name (e.g. ICU 'u_strlen_75' -> 'u_strlen_78', "
              "or a GNU symbol-version node bump). The large removed/added churn is "
              "likely a library-wide versioned-symbol scheme, not independent API "
              "removals — review against the library's versioning convention; a "
              "suppression preset can scope these renames to compatible."),
    _E("evidence_required_missing", _A,
       impact="A policy require_evidence layer (build context, source ABI, or "
              "source graph) was declared mandatory but is absent from this "
              "compare, so the run is failed rather than passing on a silently "
              "degraded scan (ADR-033 D7). Supply the missing evidence pack or "
              "relax the policy."),
    _E("struct_size_changed", _B,
       impact="sizeof(T) changed in debug info; confirms layout break visible at binary level.",
       description_template="Struct size changed: {name} ({old} → {new} bytes)"),
    _E("struct_field_offset_changed", _B,
       impact="Field moved to different offset; old code accesses wrong memory.",
       description_template="Field offset changed: {name}::{detail} (+{old} → +{new})"),
    _E("struct_field_removed", _B,
       impact="Field removed from struct; old code accessing it reads/writes garbage.",
       description_template="Struct field removed: {name}::{detail}"),
    _E("struct_field_type_changed", _B,
       impact="Field type changed in binary; old code misinterprets the field data.",
       description_template="Field type changed: {name}::{detail} {old} → {new}"),
    _E("struct_alignment_changed", _B,
       impact="Struct alignment changed; may cause misaligned access in embedded structs.",
       description_template="Struct alignment changed: {name} ({old} → {new})"),
    _E("enum_underlying_size_changed", _B,
       impact="Enum underlying type changed (e.g. int→long); affects ABI of functions passing enums by value.",
       description_template="Enum underlying type size changed: {name} ({old} → {new} bytes)"),

    # ── DWARF advanced (Sprint 4) ─────────────────────────────────────────
    _E("calling_convention_changed", _B,
       impact="Function calling convention changed; registers/stack usage differs, call crashes.",
       policy_overrides={"plugin_abi": _C}),
    _E("value_abi_trait_changed", _B,
       policy_overrides={"plugin_abi": _C}),
    _E("struct_packing_changed", _B,
       impact="Packing attribute changed; field offsets differ from what old code expects."),
    _E("type_visibility_changed", _B),
    _E("toolchain_flag_drift", _C,
       impact="Compiler flags differ between versions; may cause subtle ABI mismatches."),
    _E("frame_register_changed", _B,
       policy_overrides={"plugin_abi": _C}),
    _E("vector_abi_changed", _B,
       impact="Vector-function (SIMD clone) ABI selection changed (-mveclibabi/-fveclib/-vecabi); vectorized call variants resolve to a different ABI, so callers of the vector entry points pass/return data in the wrong registers.",
       policy_overrides={"plugin_abi": _C}),
    _E("struct_return_convention_changed", _B,
       impact="The aggregate (struct/class/union) return convention changed for a "
              "public function — e.g. a small struct that was returned in registers "
              "is now returned via a hidden caller-provided pointer (sret), or vice "
              "versa (-freg-struct-return ↔ -fpcc-struct-return, or a "
              "triviality/size change that crosses the register-return threshold). "
              "Callers and callee disagree on where the result lives, so the return "
              "value is read from the wrong location — silent corruption or a crash. "
              "Proven from DWARF/ABI facts, so BREAKING; the flag-only signal stays "
              "as the generic abi_relevant_build_flag_changed (RISK).",
       policy_overrides={"plugin_abi": _C}),

    # ── Sprint 2 — gap detectors ──────────────────────────────────────────
    _E("func_deleted", _B,
       impact="Function marked = delete; old binaries still call it, getting link error or UB.",
       description_template="Function explicitly deleted (= delete): {name}"),
    _E("var_became_const", _B,
       impact="Variable moved to read-only section; old code writing to it gets SIGSEGV."),
    _E("var_lost_const", _B,
       impact="Variable no longer const; ODR violations possible if old code inlined the value."),
    _E("type_became_opaque", _B,
       impact="Type became forward-declaration only; old code using sizeof or accessing fields fails.",
       description_template="Type became opaque (forward-declaration only): {name} — stack allocation no longer possible"),
    _E("base_class_position_changed", _B,
       description_template="Base class order reordered: {name} — this-pointer adjustments changed"),
    _E("base_class_virtual_changed", _B,
       description_template="Base class virtual inheritance changed: {name} — {detail}"),

    # ── Sprint 7 — Source-level breaks ─────────────────────────────────────
    _E("enum_member_renamed", _A,
       impact="Enumerator name changed but value is the same; source code using old name won't compile.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Enum member renamed: {name}::{old} → {new} (value={detail})"),
    _E("param_default_value_changed", _C,
       description_template="Parameter default changed: {name} param {detail}"),
    _E("param_default_value_removed", _A,
       policy_overrides={"sdk_vendor": _C},
       description_template="Parameter default removed: {name} param {detail}"),
    _E("field_renamed", _A,
       impact="Field name changed but offset is the same; source code using old name won't compile.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Field renamed: {name}::{old} → {new}"),
    _E("param_renamed", _A,
       policy_overrides={"sdk_vendor": _C},
       description_template="Parameter renamed: {name} param {detail}: {old} → {new}"),

    # ── Field qualifier changes ────────────────────────────────────────────
    _E("field_became_const", _C,
       description_template="Field became const: {name}::{detail}"),
    _E("field_lost_const", _C,
       description_template="Field lost const: {name}::{detail}"),
    _E("field_became_volatile", _C,
       description_template="Field became volatile: {name}::{detail}"),
    _E("field_lost_volatile", _C,
       description_template="Field lost volatile: {name}::{detail}"),
    _E("field_became_mutable", _C,
       description_template="Field became mutable: {name}::{detail}"),
    _E("field_lost_mutable", _C,
       description_template="Field lost mutable: {name}::{detail}"),

    # ── Pointer level changes ──────────────────────────────────────────────
    _E("param_pointer_level_changed", _B,
       description_template="Parameter pointer level changed: {name} param {detail} (depth {old} → {new})"),
    _E("return_pointer_level_changed", _B,
       description_template="Return pointer level changed: {name} (depth {old} → {new})"),

    # ── Access level changes ───────────────────────────────────────────────
    _E("method_access_changed", _A,
       impact="Method access level narrowed (e.g. public→private); old code calling it won't compile.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Method access level narrowed: {name} ({old} → {new})"),
    _E("field_access_changed", _A,
       impact="Field access level narrowed; old code accessing it won't compile.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Field access level narrowed: {name}::{detail} ({old} → {new})"),

    # ── Anonymous struct/union ─────────────────────────────────────────────
    _E("anon_field_changed", _B),

    # ── ABICC full parity — remaining gaps ─────────────────────────────────
    _E("var_value_changed", _C,
       description_template="Global data value changed: {name} ({old} → {new})"),
    _E("type_kind_changed", _B,
       description_template="Aggregate kind changed: {name} ({old} → {new})"),
    _E("source_level_kind_changed", _A,
       policy_overrides={"sdk_vendor": _C},
       description_template="Aggregate kind changed: {name} ({old} → {new})"),
    _E("used_reserved_field", _C,
       description_template="Reserved field put into use: {name}::{old} → {new}"),
    _E("removed_const_overload", _A,
       impact="Const overload removed; source code calling const version breaks.",
       policy_overrides={"sdk_vendor": _C},
       description_template="Const method overload removed: {name} (non-const version still exists)"),
    _E("param_restrict_changed", _C,
       description_template="Parameter restrict qualifier {detail}: {name} param {old}"),
    _E("param_became_va_list", _C,
       description_template="Parameter became va_list: {name} param {detail}"),
    _E("param_lost_va_list", _C,
       description_template="Parameter was va_list, now fixed: {name} param {detail}"),
    _E("constant_changed", _A,
       description_template="Preprocessor constant value changed: {name} ({old} → {new})"),
    _E("constant_added", _C, is_addition=True,
       description_template="New preprocessor constant: {name}"),
    _E("constant_removed", _A,
       description_template="Preprocessor constant removed: {name}"),
    _E("var_access_changed", _A,
       description_template="Variable access level narrowed: {name} ({old} → {new})"),
    _E("var_access_widened", _C,
       description_template="Variable access level widened: {name} ({old} → {new})"),

    # ── Inline attribute changes ───────────────────────────────────────────
    _E("func_became_inline", _A),
    _E("func_lost_inline", _C,
       description_template="Function lost inline attribute (now has external linkage): {name}"),

    # ── PR #89: ELF fallback ──────────────────────────────────────────────
    _E("func_deleted_elf_fallback", _B,
       description_template="Symbol disappeared from ELF .dynsym without explicit deletion marker: {name} — was exported in old library, absent in new library's dynamic symbol table while header still declares it"),

    # ── Template inner-type analysis ──────────────────────────────────────
    _E("template_param_type_changed", _B,
       description_template="Template parameter inner type changed: {name} param {detail} ({old} → {new})"),
    _E("template_return_type_changed", _B,
       description_template="Template return type inner argument changed: {name} ({old} → {new})"),

    # ── Version-stamped typedef sentinel ───────────────────────────────────
    _E("typedef_version_sentinel", _C,
       impact="Typedef name encodes a version number (e.g. png_libpng_version_1_6_46) — "
              "this is a compile-time sentinel that changes every release by design; "
              "it is never exported as an ELF symbol and does not affect binary ABI.",
       description_template="Version-stamped typedef removed (compile-time sentinel, not an ABI break): {name}"),

    # ── ELF st_other visibility transitions ────────────────────────────────
    _E("symbol_elf_visibility_changed", _C,
       impact="ELF symbol visibility (st_other) changed (e.g. DEFAULT→PROTECTED). "
              "Symbol is still exported but interposition via LD_PRELOAD may stop working.",
       description_template="ELF visibility changed: {name} ({old} → {new})"),

    # ── Symbol rename detection ────────────────────────────────────────────
    _E("symbol_renamed_batch", _B,
       impact="Multiple symbols renamed (e.g. namespace prefix added/removed); "
              "old binaries reference the old names and will get undefined symbol errors at load time.",
       description_template="Batch symbol rename detected (namespace refactoring): prefix '{name}' added to {detail}"),
    _E("func_likely_renamed", _B,
       impact="Function likely renamed (binary fingerprint match: identical code size and hash, "
              "different symbol name). Old binaries reference the old name and will fail to "
              "resolve at load time. This is a heuristic signal — verify the rename is intentional.",
       description_template="Function likely renamed: {old} → {new} (size={detail}B, confidence={name}%)"),

    # ── Symbol origin detection ────────────────────────────────────────────
    _E("symbol_leaked_from_dependency_changed", _R,
       impact="Symbol originates from a dependency library (e.g. libstdc++, libgcc) that leaked "
              "into this library's public ABI surface. The symbol changed between versions — "
              "existing consumers are unlikely to be affected directly, but the leak itself is a "
              "library quality issue. Apply -fvisibility=hidden to prevent accidental ABI surface "
              "enlargement from dependencies."),

    # ── Gap analysis: proposed new checks ──────────────────────────────────

    # C++ ref-qualifier change on member functions (& / &&)
    _E("func_ref_qual_changed", _B,
       impact="Ref-qualifier (&/&&) on a member function changed; this alters the "
              "Itanium C++ ABI mangled name and overload resolution, so old binaries "
              "link to the wrong symbol or fail to resolve it.",
       description_template="Ref-qualifier changed: {name} ({old} → {new})"),

    # extern "C" ↔ C++ linkage flip
    _E("func_language_linkage_changed", _B,
       impact="Language linkage changed (extern \"C\" ↔ C++); the mangled symbol name "
              "changes, so old binaries reference a symbol that no longer exists under "
              "that name.",
       description_template="Language linkage changed: {name} ({old} → {new})"),

    # Symbol version alias (default version) changed
    _E("symbol_version_alias_changed", _R,
       impact="Default symbol version alias changed (e.g. foo@@VER_1.0 → foo@@VER_2.0). "
              "Old binaries requesting the previous default version may get a link or "
              "load error if the old version alias is not retained."),

    # TLS variable model or size changed
    _E("tls_var_size_changed", _B,
       impact="Exported thread-local (TLS) variable size changed; consumers using copy "
              "relocations or direct TLS access will read/write out of bounds.",
       description_template="TLS variable size changed: {name} ({old} → {new} bytes)"),

    # ELF visibility: STV_PROTECTED ↔ STV_DEFAULT for data symbols
    _E("protected_visibility_changed", _R,
       impact="ELF symbol visibility changed between DEFAULT and PROTECTED. For data "
              "symbols this can break copy relocations; for functions it changes "
              "interposition semantics. The symbol remains exported.",
       description_template="Data symbol visibility changed: {name} ({old} → {new}); may break copy relocations"),

    # libstdc++ dual ABI flip diagnostic
    _E("glibcxx_dual_abi_flip_detected", _C,
       impact="Mass symbol churn detected that matches a libstdc++ dual ABI toggle "
              "(_GLIBCXX_USE_CXX11_ABI). Individual removed/added symbols are likely "
              "caused by this single root cause rather than intentional API changes.",
       description_template="libstdc++ dual ABI flip detected ({detail}): {name} churned symbols contain CXX11 ABI markers; likely caused by _GLIBCXX_USE_CXX11_ABI toggle"),

    # Inline namespace move
    _E("inline_namespace_moved", _B,
       impact="Symbols moved to a different inline namespace (e.g. v1:: → v2::); "
              "mangled names change so old binaries fail to resolve the symbols.",
       description_template="Inline namespace move detected: {detail} symbols appear to have moved between inline namespace versions (e.g. ::v1:: → ::v2::); mangled names changed"),

    # vtable/typeinfo symbol identity changed (layout stable)
    _E("vtable_symbol_identity_changed", _R,
       impact="Vtable or typeinfo symbol identity changed (e.g. via visibility or "
              "version-script changes) while class layout is stable. Cross-DSO RTTI "
              "comparison and exception handling may silently fail."),

    # ABI surface explosion diagnostic
    _E("abi_surface_explosion", _C,
       impact="Public ABI surface grew or shrank dramatically (e.g. lost "
              "-fvisibility=hidden). This is a configuration/packaging signal, not "
              "a per-symbol break, but may indicate an unintended visibility regression.",
       description_template="ABI surface {detail} dramatically: {old} → {new} exported symbols ({name}); check -fvisibility=hidden and version scripts"),

    # ── ELF symbol-version policy checks ────────────────────────────────────
    _E("symbol_version_node_removed", _B,
       impact="A version node (e.g. LIBFOO_1.0) was entirely removed from the "
              "version script. Applications linked against symbols under that "
              "version node will get unresolved symbol errors at load time.",
       description_template="Version node {name} was entirely removed from the version script. Symbols previously under this node: {detail}. Applications linked against {name} will get unresolved symbol errors."),
    _E("symbol_moved_version_node", _R,
       impact="Symbol moved from one version node to another (e.g. LIBFOO_1.0 → "
              "LIBFOO_2.0). Applications linked against the old version node will "
              "not find this symbol at the expected version. This is typically "
              "intentional during a major release.",
       description_template="Symbol {name} moved from version node {old} to {new}. Applications linked against {old} will not find this symbol at the expected version. This is typically intentional during a major release."),
    # TODO(policy): The spec calls for strict_abi to treat this as BREAKING
    # and sdk_vendor as COMPATIBLE_WITH_RISK, but the current policy override
    # mechanism only supports downgrading (not upgrading) verdicts.  Adding
    # per-policy upgrades requires changes to policy_kind_sets() and the
    # integrity assertions in checker_policy.py.  Tracked for v2.0.
    _E("soname_bump_recommended", _C,
       impact="Binary-incompatible changes detected but SONAME was not bumped. "
              "Consumers linked against the current SONAME will encounter runtime "
              "failures. Recommended: bump the SONAME to signal the ABI break.",
       description_template="{name} binary-incompatible change(s) detected but {detail}. Consumers linked against {old} will encounter runtime failures. Recommended: bump SONAME to signal the ABI break."),
    _E("soname_bump_unnecessary", _C,
       impact="SONAME was bumped but no binary-incompatible changes were detected. "
              "This forces all consumers to relink unnecessarily. Consider whether "
              "the bump was intentional.",
       description_template="SONAME changed from {old} to {new} but no binary-incompatible changes were detected. This forces all consumers to relink unnecessarily. Consider whether the bump was intentional."),
    _E("version_script_missing", _C,
       impact="Library exports symbols without a version script. This is a common "
              "oversight that prevents fine-grained symbol versioning and makes "
              "future ABI evolution harder to manage.",
       description_template="Library exports {detail} symbol(s) without a version script. This is a common oversight that prevents fine-grained symbol versioning and makes future ABI evolution harder to manage. Consider adding a version script (--version-script=libfoo.map)."),

    # ── SYCL Plugin Interface (PI) ────────────────────────────────────────
    _E("sycl_implementation_changed", _B,
       impact="SYCL implementation changed (e.g., DPC++ to AdaptiveCpp); "
              "entirely different runtime ABI, plugin interface, and binary layout. "
              "All SYCL consumers must be rebuilt.",
       description_template="SYCL implementation changed from {old} to {new}; entirely different runtime ABI."),
    _E("sycl_pi_version_changed", _B,
       impact="PI interface version changed; runtime rejects plugins compiled against the old "
              "PI version. All backend plugins must be rebuilt or upgraded.",
       description_template="PI interface version changed from {old} to {new}; backend plugins compiled against the old version may be rejected at runtime."),
    _E("sycl_pi_entrypoint_removed", _B,
       impact="Required PI entry point removed from plugin dispatch table; runtime calls to "
              "this function will crash or return PI_ERROR_UNKNOWN.",
       description_template="{detail} entry point '{name}' removed from plugin '{old}'; runtime calls to this function will fail."),
    _E("sycl_pi_entrypoint_added", _C, is_addition=True,
       impact="New PI entry point added to dispatch table; existing plugins are unaffected.",
       description_template="{detail} entry point '{name}' added to plugin '{new}'."),
    _E("sycl_plugin_removed", _B,
       impact="Backend plugin removed from distribution; applications targeting this backend "
              "will fail at runtime with PI_ERROR_DEVICE_NOT_FOUND.",
       description_template="Backend plugin '{name}' ({detail}) removed; applications targeting the {old} backend will fail at runtime."),
    _E("sycl_plugin_added", _C, is_addition=True,
       impact="New backend plugin available; broadens hardware support.",
       description_template="Backend plugin '{name}' ({detail}) added; new {new} backend support available."),
    _E("sycl_plugin_search_path_changed", _R,
       impact="Plugin discovery path changed; plugins may not be found at runtime unless "
              "deployment configuration is updated.",
       description_template="SYCL plugin search paths changed; plugins may not be found at runtime without deployment configuration update."),
    _E("sycl_runtime_version_changed", _C,
       impact="SYCL runtime version changed; informational. Actual binary breaks are detected "
              "by symbol/type diff of the runtime library.",
       description_template="SYCL runtime version changed from {old} to {new}."),
    _E("sycl_backend_driver_req_changed", _R,
       impact="Minimum backend driver version requirement increased; may fail on systems with "
              "older drivers (e.g., Level Zero, OpenCL ICD).",
       description_template="Minimum driver requirement for {name} backend changed from {old} to {new}."),

    # ── Flexible array member detection (libabigail parity) ──────────────
    _E("flexible_array_member_changed", _C,
       impact="Flexible array member (FAM) at end of struct changed: last field with "
              "zero/unknown array size was added, removed, or changed type. The struct "
              "binary layout is unchanged (FAM has zero static size), but runtime "
              "allocation patterns may differ."),

    # ── DWARF-based = delete detection (P3 gap) ─────────────────────────
    _E("func_deleted_dwarf", _B,
       impact="Function marked as deleted (= delete) detected via DWARF debug info. "
              "The function was previously callable; callers will fail to link.",
       description_template="Function explicitly deleted (= delete): {name}"),

    # ── Bundle / multi-library findings (ADR-023) ───────────────────────
    _E("bundle_intra_dep_removed", _B,
       impact="A sibling library in this bundle still imports a symbol that no "
              "library in the new bundle exports. Loading the consumer will fail "
              "with undefined symbol at runtime."),
    _E("bundle_intra_dep_signature_changed", _B,
       impact="A sibling library imports a symbol whose provider changed its "
              "DWARF signature (parameters or return type) while keeping the same "
              "mangled name (typical of extern \"C\" or weak boundaries). The "
              "linker resolves the symbol but the calling convention is wrong; "
              "callers pass arguments with the old layout, callee reads the new."),
    _E("bundle_intra_type_changed", _B,
       impact="A type defined in one library of this bundle is used in the public "
              "ABI of a sibling library, and its layout changed. The sibling's "
              "ABI looks unchanged on its own, but every cross-DSO call that "
              "passes the type by value or reads its fields is now miscompiled."),
    _E("bundle_provider_changed", _R,
       impact="A symbol moved from one library in this bundle to another. "
              "Downstream binaries that had DT_NEEDED on the old provider may "
              "still resolve transitively through the bundle's link graph, or "
              "may not — depends on whether the consumer's existing dependency "
              "chain reaches the new provider."),
    _E("bundle_manifest_instantiation_removed", _B,
       impact="A symbol listed in the supplied --manifest as a public ABI "
              "promise is not exported by any library in the new bundle. "
              "Consumers of the previously-promised template instantiation will "
              "fail to link or load."),
    _E("bundle_manifest_instantiation_added", _C, is_addition=True,
       impact="A symbol present in the new manifest is not in the old one; "
              "new instantiation now publicly promised."),
    _E("bundle_library_removed", _B,
       impact="A library present in the old bundle is absent in the new bundle "
              "and at least one of its exported symbols was consumed by a sibling. "
              "Loading any consumer fails with NEEDED-library-not-found."),
    _E("bundle_library_added", _C, is_addition=True,
       impact="A new library appears in the bundle; existing consumers unaffected."),
    _E("bundle_intra_dep_resolved_to_different_version", _R,
       impact="A sibling import that previously resolved to one symbol version "
              "now resolves to a different version in the new bundle (gnu.version_r "
              "drift). Compatible at the linker level but the underlying ABI of "
              "that version may differ."),

    # ── Internal-namespace leak via public API ──────────────────────────
    _E("internal_type_leaks_via_public_api", _B,
       impact="A type in an internal namespace (e.g. ::detail::, ::impl::, ::internal::) "
              "changed and is reachable from a public exported type or symbol "
              "(via inheritance, embedded-by-value field, or template argument). "
              "Although the type is conceptually 'internal', it is part of the "
              "effective public ABI: changes to it propagate into the layout, "
              "vtable, or compiled code of every consumer of the public type. "
              "Common in libraries that wrap implementation in a "
              "'detail' namespace (for example oneDAL)."),

    # ── library-family-shaped breaks (case77–case89, follow-up to PR #238) ──────
    _E("instantiation_missing_from_binary", _B,
       impact="Header declares an explicit template instantiation that the shipped "
              "library no longer exports. Consumer source compiles cleanly but fails "
              "to link at load time with an undefined-symbol error. Common when a "
              "build trim drops a Float/Method/Task combination without updating "
              "the public header's `extern template` declarations.",
       description_template="Template instantiation '{name}' was exported by the old library but is missing from the new binary. Other instantiations of '{detail}' still exist, so the public header very likely still advertises this one. Consumers built against the old header link cleanly but fail at load time with an undefined-symbol error."),

    _E("serialization_tag_changed", _B,
       impact="A serialization tag ID (or equivalent constant identifying a class "
              "for persistence) changed value or was swapped with another class's "
              "tag. Symbol table, types, and layout are all unchanged — every "
              "conventional ABI check passes. But saved models / persisted state "
              "from the old library deserialize as the wrong class against the new "
              "library, silently corrupting data. Common in "
              "SerializationIface-style designs."),

    _E("sycl_overload_set_removed", _B,
       impact="A family of public overloads that take a SYCL queue as the first "
              "parameter was removed in bulk (typical when DPC++ support is "
              "disabled at build time). Reported as one grouped finding rather "
              "than N independent func_removed entries to make the deployment-"
              "level event ('the GPU/SYCL overload family was withdrawn') "
              "visible at a glance.",
       description_template="SYCL overload family withdrawn: {detail}. This is the deployment-level event 'DPC++ build disabled' rather than independent API removals — consumers built against the SYCL surface need a DPC++-enabled rebuild."),

    _E("cpu_dispatch_isa_dropped", _R,
       impact="An entire CPU ISA tier (e.g. avx512) of dispatched specializations "
              "was removed. The runtime dispatcher continues to work for callers "
              "that did not pin a specific ISA, but consumers that linked directly "
              "against a now-removed ISA-specific symbol get unresolved symbols. "
              "Reported as one grouped finding listing the affected algorithm "
              "stems.",
       description_template="CPU dispatch ISA '{name}' tier removed: {detail}. Runtime dispatcher continues to work; consumers that pinned directly to '{name}' symbols get unresolved references at load time."),

    _E("bundle_soname_skew", _B,
       impact="A co-versioned bundle of shared libraries (e.g. libfoo_core, "
              "libfoo_thread, libfoo_dpc) did not move SONAME in lockstep. "
              "Some siblings bumped the major SONAME, others did not. Distro "
              "packages built on this bundle have inconsistent dependency "
              "metadata; binaries dynamically loading the mixed cohort can fetch "
              "incompatible internal contracts and corrupt at the first cross-"
              "library call."),

    _E("tag_type_renamed", _B,
       impact="An empty tag struct (zero fields, no methods) used solely for "
              "template specialization was renamed. Layout-based detectors see no "
              "change because the type has no layout, but every explicit "
              "instantiation that referenced the old tag is re-mangled and the "
              "old symbol disappears. Consumers built against the old header get "
              "unresolved-symbol errors at load time. Common with "
              "method::* / task::* tag families.",
       description_template="Empty tag struct '{old}' renamed to '{new}'. The type has no fields or vtable, so layout-based detectors see no change, but {detail}. Consumers built against the old header fail to resolve the instantiation at load time."),

    _E("default_template_arg_changed", _B,
       impact="A default template argument changed (e.g. `Distance = "
              "minkowski_distance<Float>` → `Distance = euclidean_distance<Float>`). "
              "Consumer source compiles unchanged but the substituted instantiation "
              "type differs, producing a different mangled symbol. The library "
              "ships only one instantiation; consumers built against the old "
              "default reference a symbol that no longer exists. Unlike function "
              "default parameter changes (NO_CHANGE), template default arguments "
              "ARE part of the substituted type and affect mangling.",
       description_template="Template instantiation '{name}' substitutes to different arguments than its surviving sibling '{detail}'. This is consistent with a change to a default template argument in the declaring header: consumer source compiles unchanged, but the substituted mangled symbol differs. Consumers built against the old default get unresolved symbols."),

    _E("inline_body_references_renamed_member", _B,
       impact="An inline public accessor (header-emitted into every consumer "
              "binary) reaches into a pimpl/detail member by name. That member "
              "was renamed in the implementation type, and although the inline "
              "accessor's body was updated in lockstep in the new header, "
              "consumers compiled against the OLD header have the old field "
              "name baked into their binary. At runtime, the inline body "
              "accesses a field at the wrong offset (or by a name that no "
              "longer exists), producing silent wrong data or crashes.",
       description_template="Public class '{name}' has inline accessors {detail} by name. Field '{old}' was renamed to '{new}' in the new internal layout. Consumers compiled against the old header have the old member name baked into their inline accessor bodies; running against the new library reads the wrong offset or fails to resolve the member."),

    # ── Explicit specifier transitions ───────────────────────────────────
    _E("ctor_explicit_added", _A,
       impact="A constructor or conversion operator gained the `explicit` "
              "specifier. Source code that relied on implicit conversion "
              "(copy-initialization like `Foo f = 42;`, pass-by-value at a "
              "call site, or return-by-implicit-conversion) no longer "
              "compiles. The mangled name is unchanged so binaries keep "
              "running, but recompilation against the new header fails."),
    _E("ctor_explicit_removed", _R,
       impact="A constructor or conversion operator lost the `explicit` "
              "specifier. Existing code keeps compiling, but implicit "
              "conversion paths that previously did not consider this "
              "function now do, potentially selecting a different overload "
              "than before and causing silent behavioral drift."),
    _E("ctor_overload_ambiguity_risk", _R,
       impact="A class gained a second (or later) non-explicit, single-"
              "argument constructor. Any call site whose argument type is "
              "implicitly convertible to more than one of the class's "
              "converting constructors becomes ambiguous — it either stops "
              "compiling or silently resolves to a different constructor "
              "than before. This cannot be proven from a header/binary "
              "snapshot alone (it depends on actual call-site argument "
              "types), so it is reported as a risk to review, not a "
              "certain break.",
       description_template="Class '{name}' gained a 2nd+ non-explicit converting constructor: {new}"),

    # ── Class `final`-specifier transitions (header/castxml only) ────────
    _E("type_became_final", _A,
       impact="A class/struct gained the `final` specifier. Any consumer that "
              "derives from it (`class D : public C`) no longer compiles. The "
              "type layout and mangled names are unchanged so already-built "
              "binaries keep running, but recompilation against the new header "
              "fails — a source/API break. Invisible to binary analysis: "
              "`final` is not recorded in DWARF or the object file, so this is "
              "detected only in header (castxml) mode.",
       description_template="Class gained `final` specifier: {name} — consumers that derive from it no longer compile"),
    _E("type_lost_final", _R,
       impact="A class/struct lost the `final` specifier. Deriving from it is "
              "now allowed and previously-valid source still compiles, so this "
              "is not a source break. The risk is on already-compiled consumers: "
              "code built while the class was `final` may have had its virtual "
              "calls *devirtualized*, and if a later version introduces a "
              "subclass that overrides, those old binaries keep dispatching "
              "statically to the wrong target. KDE's C++ binary-compatibility "
              "policy lists removing `final` as a change to avoid; surfaced as a "
              "deployment risk for review rather than a hard break.",
       description_template="Class lost `final` specifier: {name}"),

    # ── Namespace-shape patterns (PR follow-up to #238) ─────────────────
    # Generic detectors for template / header-only libraries (the patterns
    # show up in libraries such as oneDPL, but are not library-specific).
    # Live in abicheck/diff_namespaces.py.
    _E("experimental_graduated", _C, is_addition=True,
       impact="A declaration that previously lived under an `experimental::` "
              "(or similar) namespace is now also available at a stable name "
              "in the same library, while the experimental alias is retained. "
              "Compatible: existing consumers keep compiling; new consumers "
              "are encouraged to migrate to the stable name.",
       description_template="Experimental {detail} '{old}' graduated to stable name '{new}'; experimental alias retained."),

    _E("experimental_removed_without_replacement", _A,
       impact="A declaration that previously lived under an `experimental::` "
              "(or similar) namespace was removed and no declaration with "
              "the same leaf name appears under a stable namespace in the "
              "new headers. Consumers that depended on the experimental name "
              "no longer compile. The mangled name change is the same as a "
              "func_removed/type_removed for an instantiated template, but "
              "the experimental graduation pattern is named explicitly so "
              "users see whether a replacement was published.",
       description_template="Experimental {detail} '{old}' was removed and no {detail} with leaf '{name}' was published at a stable namespace in the new headers."),

    _E("std_reexport_removed", _A,
       impact="A public header used to re-export a name from `std::` "
              "(e.g. `using std::execution::par;`) and the re-export was "
              "deleted in the new headers. Consumer source that referenced "
              "the library-qualified name (`lib::par`) no longer compiles "
              "even though the underlying `std::par` is still available. "
              "Source break only — no symbol disappears, but every TU that "
              "named the library alias must be edited.",
       description_template="Public re-export '{name}' of standard-library entity '{detail}' was removed. Consumer code that named '{name}' no longer compiles; '{detail}' is still available under its std:: name."),

    _E("inline_namespace_version_bumped", _B,
       impact="A header-declared symbol or type lives under a versioned "
              "inline namespace (e.g. `inline namespace _V1`) and the "
              "version segment shifted (`_V1` → `_V2`). Declarations look "
              "identical to consumers but every newly compiled TU produces "
              "a different mangled symbol; old TUs in the same program ODR-"
              "violate against new TUs. Specialisation of inline_namespace_"
              "moved that fires from declared-name evidence (works even "
              "when the library ships no .so).",
       description_template="Inline namespace version bumped: '{old}' → '{new}' (version segment changed from {detail}); mangled names change so old and new TUs of the same program ODR-violate."),

    # ── Template / overload-set patterns (PR-B) ─────────────────────────
    _E("internal_template_leaks_via_public_api", _B,
       impact="An internal-namespace function template (e.g. "
              "`acme::detail::__pattern_walk2<...>`) changed "
              "signature, and its instantiations appear in consumer "
              "symbol tables because public algorithms inline-dispatch "
              "through it. The internal helper is part of the effective "
              "public ABI — every consumer must be rebuilt. Function-"
              "template analogue of INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API.",
       description_template="Internal-namespace function template '{name}' has changed instantiations: {detail}. These mangled names participate in consumer symbol tables; every consumer must rebuild."),

    _E("cpo_kind_changed", _B,
       impact="A public customization point object (CPO) changed kind: "
              "what used to be a free function is now a function-object "
              "(variable of an unspecified class type), or vice versa. "
              "Call syntax (`lib::sort(args...)`) keeps working but "
              "`decltype(lib::sort)` is now a different type, breaking "
              "extern templates, trait specializations, and any code that "
              "took the CPO's address.",
       description_template="Public name '{name}' was a {old} in old and is a {new} in new. Call syntax preserved; decltype, extern templates, and trait specializations break."),

    _E("overload_set_rerouted", _R,
       impact="The overload set under a public name changed in a way "
              "where some overloads were removed and others added. "
              "Existing call sites that previously resolved to a removed "
              "overload now resolve to a different overload (often via "
              "implicit conversion or a templated catch-all), silently "
              "changing the called function. Compiles, links, runs — but "
              "runs different code.",
       description_template="Overload set for '{name}' changed: {detail}. Call sites that previously resolved to a removed overload may silently re-route to a different overload."),

    _E("overload_added", _R,
       impact="A new overload was added under a public name that previously had "
              "exactly one declaration. Old binaries are unaffected (binary "
              "compatible), but the change is not source-compatible: taking the "
              "function's address (`&Foo::bar`) becomes ambiguous and fails to "
              "compile, and existing call sites that relied on an implicit "
              "conversion may now resolve to the new overload, silently changing "
              "which function runs. KDE's C++ binary-compatibility policy lists "
              "adding an overload to a non-overloaded function as a change to "
              "avoid. Verdict is policy-adjustable — raise to API_BREAK under a "
              "strict source-compatibility profile.",
       description_template="Overload added to previously non-overloaded function: {name} — `&{name}` becomes ambiguous and overload resolution may change"),
    _E("mandatory_template_param_added", _A,
       impact="A function or class template parameter that was defaulted "
              "(or deduced) became mandatory. Consumer source that wrote "
              "`Foo<int>` without supplying the new parameter no longer "
              "compiles. Mangled symbols also change because the "
              "instantiation tuple differs.",
       description_template="Template '{name}' minimum effective argument count grew from {old} to {new}. Consumers that wrote '{name}<...{old} args...>' without supplying the new parameter no longer compile."),

    _E("unspecified_return_now_named", _A,
       impact="A factory function's return type changed between an "
              "unspecified placeholder (`auto`, lambda type, anonymous "
              "class) and a named type — or vice versa. Source that "
              "stored the result with the deduced spelling (`auto x = "
              "make_X();`) keeps compiling; source that wrote out the "
              "type fails to compile."),

    # ── Build-config / probe-harness patterns (PR-C) ────────────────────
    _E("concept_tightened", _A,
       impact="A public C++20 concept became more constrained; consumer templates or calls that satisfied the old constraint may no longer compile against the new headers.",
       description_template="Concept constraint tightened: {name}"),
    _E("api_depends_on_consumer_env", _R,
       impact="A public declaration is present under one consumer build "
              "configuration (compiler, language standard, macro set) "
              "and absent under another. Source that compiled on the "
              "library author's machine may not compile on the consumer's. "
              "Detected only when abicheck is given a probe matrix "
              "(snapshots taken under multiple configurations).",
       description_template="{detail} '{name}' is present in configurations {old} but absent in {new}. Consumers compiling under different toolchains see different public APIs."),

    _E("cxx_standard_floor_raised", _A,
       impact="The library's minimum required C++ standard increased "
              "between releases (e.g. C++17 → C++20). Consumers still "
              "building with the old standard no longer get a working "
              "header set; standard-library facilities removed in newer "
              "standards (e.g. std::result_of) may also disappear from "
              "the API surface.",
       description_template="C++ standard floor raised from {old} to {new}. Consumers still building with the old standard get a degraded or non-functional API surface."),

    _E("behavioural_default_changed", _R,
       impact="A documented default value changed without altering any "
              "signature — e.g. the default device selector, the default "
              "execution backend, or the default policy. Source compiles "
              "and links unchanged; runtime behaviour silently differs. "
              "Read from the probe manifest's `defaults:` section."),

    # ── Hidden-friend transitions (PR #248 follow-up) ───────────────────
    _E("hidden_friend_removed", _A,
       impact="An in-class `friend` declaration (a 'hidden friend' — "
              "findable only via ADL on one of its argument types) was "
              "removed. Inline hidden friends never receive an external "
              "symbol, so the break is invisible at the binary layer, but "
              "every consumer that wrote `a + b` (or any other ADL-driven "
              "call site) fails to compile against the new headers. When "
              "the friend was also defined out-of-line, removal "
              "additionally surfaces as FUNC_REMOVED at link time.",
       description_template="Hidden friend declaration removed: {old}"),
    _E("hidden_friend_added", _C, is_addition=True,
       impact="A new in-class `friend` declaration was added. Pure "
              "addition: existing code keeps compiling, no symbol "
              "disappears, and the new operator/function only "
              "participates in overload resolution at call sites that "
              "trigger ADL on one of its argument types.",
       description_template="Hidden friend declaration added: {new}"),

    # ── modern-C++ / numerical-library ABI hazards (gap analysis) ───────────
    _E("integer_model_changed", _B,
       impact="A large fraction of public integer parameters/returns flipped "
              "width together (e.g. int→long, int32_t→int64_t), or a public "
              "integer typedef changed its underlying size. This is the "
              "signature of an LP64↔ILP64 model switch (e.g. a BLAS-style "
              "`INT` typedef built for the 32-bit vs 64-bit integer interface). "
              "Every caller "
              "passes/reads integers with the wrong width; arguments and array "
              "indices are silently truncated or sign-extended.",
       description_template="Integer model changed ({new}): {detail}. This is the signature of an LP64↔ILP64 switch (e.g. oneMKL's 32-bit vs 64-bit MKL_INT interface); every caller passes/reads integers with the wrong width."),
    _E("abi_tag_changed", _B,
       impact="The Itanium ABI-tag set on a symbol changed (e.g. it gained or "
              "lost `[abi:cxx11]` / a `[[gnu::abi_tag]]`). The mangled name "
              "encodes the tag, so old binaries reference a symbol that no "
              "longer exists under that name. Distinct from a mass dual-ABI "
              "flip: this is a per-symbol tag change.",
       description_template="ABI-tag set changed for '{name}': {detail}. The mangled name encodes the tag, so the old symbol ({old}) no longer exists under that name ({new})."),
    _E("char8t_migration", _B,
       impact="A public parameter, return, or field type changed between a "
              "char-family spelling (char / unsigned char) and C++20 `char8_t`. "
              "`char8_t` is a distinct type that participates in overload "
              "resolution and name mangling, so the mangled symbol changes and "
              "old binaries fail to resolve it.",
       description_template="char8_t migration ({detail}) on {name}: {old} → {new}. char8_t is a distinct C++20 type that changes overload identity and name mangling."),
    _E("bit_int_width_changed", _B,
       impact="A public use of C23 `_BitInt(N)` changed its width N between "
              "versions, or a field/param type changed to/from `_BitInt(N)`. "
              "The bit width determines the storage size and calling-convention "
              "treatment, so old code reads/writes the value with the wrong "
              "width.",
       description_template="_BitInt change on {name}: {detail} ({old} → {new}). The bit width determines storage size and ABI treatment."),
    _E("atomic_qualifier_changed", _B,
       impact="The `_Atomic` qualifier was added to or removed from a public "
              "field/param/return type. Per WG14 the size and alignment of an "
              "_Atomic-qualified type may differ from the unqualified type and "
              "varies across compilers, so layout and calling convention "
              "diverge and old code is miscompiled.",
       description_template="_Atomic {detail} on {name}: {old} → {new}. _Atomic size/alignment may differ from the unqualified type and varies across compilers."),

    # ── API-surface intelligence anti-patterns (ADR-027 A2 / D2.2) ──────────
    _E("public_api_exposes_stl_by_value", _R,
       impact="A public function takes or returns a `std::` type by value across "
              "the library boundary. Standard-library layouts (string, vector, "
              "etc.) differ across toolchains, standard-library versions, and "
              "the C++11 dual-ABI setting, so passing one by value at the ABI "
              "boundary is fragile: a consumer built with a different STL silently "
              "reads the wrong layout. Pass an opaque handle or a C-style view "
              "instead."),
    _E("polymorphic_type_non_virtual_dtor", _R,
       impact="A type with virtual methods (it has a vtable) is used as a factory "
              "return or base class but declares no virtual destructor. Deleting "
              "a derived object through a base pointer is undefined behaviour: the "
              "derived destructor never runs and the wrong amount of memory may be "
              "freed. Declare the base destructor `virtual`."),
    _E("opaque_invariant_broken", _B,
       impact="A type that was opaque (its definition hidden from callers, crossed "
              "only by pointer) or PIMPL now exposes its layout — its complete "
              "definition became visible in the public include closure, or a "
              "public function began passing it by value. Callers that relied on "
              "never seeing the layout can now `sizeof`/embed it, so the type's "
              "size and fields have joined the ABI and any later change to them is "
              "a hard break."),
    _E("handle_type_changed", _B,
       impact="An opaque handle typedef (a `void*` token or a pointer to a "
              "forward-declared struct) changed its underlying token type in a way "
              "callers can observe. Code that stored or compared the old handle "
              "representation now operates on an incompatible token."),

    # ── API-surface metric drift (ADR-027 A1 / D1.2) ────────────────────────
    _E("public_surface_grew", _C,
       impact="The aggregate count of public declarations (functions, variables, "
              "types, enums) increased between versions. Informational only — the "
              "individual additions are reported separately; this is the net "
              "signal for CI dashboards and release notes. Emitted only with "
              "--surface-metrics.",
       description_template="public surface grew: {old} → {new} declarations (+{detail})"),
    _E("public_surface_shrank", _C,
       impact="The aggregate count of public declarations decreased between "
              "versions. Informational roll-up only — individual removals are "
              "reported (and may be breaking) on their own. Emitted only with "
              "--surface-metrics.",
       description_template="public surface shrank: {old} → {new} declarations ({detail})"),
    _E("undocumented_export_ratio_increased", _C,
       impact="The fraction of exported symbols with no public-header declaration "
              "(EXPORT_ONLY origin) rose between versions — a packaging-hygiene "
              "regression: a symbol was exported without a corresponding public "
              "header. Informational; emitted only with --surface-metrics.",
       description_template="undocumented-export ratio rose: {old} → {new} (symbols exported without a public header)"),

    # ── Build-context evidence (ADR-028 L3 / ADR-029 D9) ────────────────────
    # Produced by the build-evidence diff over two BuildSourcePacks. Per ADR-028
    # D3 these are never BREAKING on their own: a build change that actually
    # breaks the ABI is caught by the artifact diff (L0/L1/L2) as a separate,
    # artifact-backed finding; these explain and localize it.
    _E("build_context_changed", _C,
       impact="Non-ABI-relevant build metadata changed between versions (e.g. "
              "include-path ordering, output paths, or generator version). "
              "Informational quality signal; no ABI impact on its own."),
    _E("abi_relevant_build_flag_changed", _R,
       impact="An ABI-affecting compiler/build option changed (e.g. -std, "
              "-fabi-version, _GLIBCXX_USE_CXX11_ABI, -fvisibility, -fpack-struct, "
              "--target/-mabi, sysroot). The artifact diff decides whether the "
              "shipped ABI actually broke; this flags the elevated risk and "
              "localizes the cause for review."),
    _E("header_parse_context_drift", _R,
       impact="The public-header AST was parsed under a different context (flags, "
              "defines, include paths) than the real build used. Header-derived "
              "API facts may be unreliable; align the parse context (e.g. via "
              "compile_commands.json) to restore confidence."),
    _E("toolchain_version_changed", _R,
       impact="The compiler, standard library, or sysroot/SDK changed between "
              "versions. Layout, mangling, and codegen can shift even with "
              "identical sources; review for ABI-affecting toolchain drift."),
    _E("generated_file_dependency_unstable", _R,
       impact="The build graph indicates a generated-file dependency risk "
              "(e.g. missing or unstable generator dependencies). Generated "
              "public declarations may differ from what was analyzed; rebuild "
              "determinism is not guaranteed."),
    _E("link_export_policy_changed", _R,
       impact="The export policy changed — version script, export map, or .def "
              "file. The set of exported symbols may have shifted. When this "
              "actually removes or alters exports, the artifact diff (L0) emits "
              "the corresponding BREAKING findings separately; this kind explains "
              "and localizes them and does not escalate on its own."),

    # ── Runtime-model / build-mode flips (L3 gap-analysis follow-up) ─────────
    # Produced by the build-evidence diff when a runtime-model flag flips. Never
    # BREAKING on their own (ADR-028 D3); they flag the risk and localize the
    # cause for the artifact diff to confirm.
    _E("exceptions_mode_changed", _R,
       impact="C++ exception support was toggled between builds (-fexceptions ↔ "
              "-fno-exceptions). The two modes are not link-compatible: an "
              "exception thrown in -fexceptions code that unwinds through a frame "
              "compiled with -fno-exceptions is undefined behaviour (it calls "
              "std::terminate at best), and -fno-exceptions changes the codegen "
              "and emitted cleanup/EH tables of every public inline that uses "
              "throw/try/catch. If the public API exposes exception types or "
              "throwing inlines, rebuild all consumers in the matching mode."),
    _E("rtti_mode_changed", _R,
       impact="C++ RTTI support was toggled between builds (-frtti ↔ -fno-rtti). "
              "-fno-rtti omits typeinfo for polymorphic types, so dynamic_cast / "
              "typeid against those types, and cross-DSO exception matching that "
              "relies on RTTI identity, can fail to link or silently misbehave "
              "when one side was built with RTTI and the other without. If the "
              "public API exposes polymorphic types or dynamic_cast/typeid in "
              "inlines, rebuild consumers in the matching mode."),
    _E("tls_model_changed", _R,
       impact="The thread-local storage model changed between builds "
              "(-ftls-model=, or -fextern-tls-init ↔ -fno-extern-tls-init). The "
              "TLS access sequence (and, with -fextern-tls-init, whether a wrapper "
              "function mediates access to a dynamically-initialized thread_local "
              "from another TU) differs, so consumers built against the old model "
              "can use the wrong access pattern for an exported thread_local."),
    _E("threadsafe_statics_mode_changed", _R,
       impact="Thread-safe initialization of function-local statics was toggled "
              "(-fno-threadsafe-statics ↔ default). With -fno-threadsafe-statics "
              "the compiler omits the __cxa_guard acquire/release calls around a "
              "local static's first-use initialization, so a public inline holding "
              "a function-local static, compiled in different modes across TUs, has "
              "mismatched guard expectations — a data race or double-init on "
              "concurrent first use."),
    _E("enum_size_flag_changed", _R,
       impact="The enum storage-size policy was toggled between builds "
              "(-fshort-enums ↔ default). With -fshort-enums the compiler picks the "
              "smallest integer type that holds an enum's range instead of a full "
              "int, so an enum member of a public struct, an enum-typed parameter, "
              "or an enum return value changes size and (as a struct member) shifts "
              "every field after it. Symbol names are unchanged, so a symbol-only "
              "check is blind; the artifact/type diff confirms any concrete layout "
              "break. Build all consumers with the matching -fshort-enums setting."),
    _E("struct_packing_mode_changed", _R,
       impact="The default struct-packing/alignment policy changed between builds "
              "(-fpack-struct / MSVC /Zp, or a differing pack width). Reducing the "
              "packing alignment removes padding, so every member offset and the "
              "type's size can change without any source or symbol change. Consumers "
              "compiled against the old packing read fields at stale offsets. The "
              "artifact/type diff proves the concrete offset break; this localizes "
              "the flag that caused it. Build consumers with the matching packing."),
    _E("lto_mode_changed", _R,
       impact="Link-time optimization was toggled between builds (-flto ↔ no LTO, "
              "or with -fwhole-program-vtables). LTO changes cross-TU inlining and "
              "can devirtualize or drop vtable/typeinfo emission the linker would "
              "otherwise keep, so the emitted symbol set and inlined public-inline "
              "bodies can differ from a non-LTO build of the same source. A risk "
              "signal to review; the artifact diff proves any concrete symbol/layout "
              "break. Prefer a single LTO policy across the library and consumers."),
    _E("char_signedness_changed", _R,
       impact="The signedness of a plain `char` changed between builds "
              "(-fsigned-char ↔ -funsigned-char; the default is target-dependent). "
              "`char`, `signed char` and `unsigned char` are three distinct types, "
              "so a plain-`char` parameter or member reinterprets the same bytes "
              "with the opposite sign, silently changing comparisons and value "
              "range in consumer code recompiled against the other setting. Symbol "
              "names are unchanged, so only the captured build flag exposes it. "
              "Build consumers with the matching char signedness."),
    _E("whole_program_vtables_mode_changed", _R,
       impact="Whole-program vtable optimization was toggled between builds "
              "(-fwhole-program-vtables, typically with LTO). It lets the linker "
              "devirtualize calls and elide or rewrite vtable/typeinfo emission "
              "across translation units under a closed-world assumption, so mixing "
              "a build that assumed whole-program visibility with a consumer that "
              "extends a class or overrides a virtual can dispatch to the wrong "
              "slot. If the public API exposes polymorphic types, build the library "
              "and its consumers with the matching setting."),
    _E("sanitizer_mode_changed", _R,
       impact="The sanitizer set changed between builds (-fsanitize=). Sanitizers "
              "instrument code and change object layout — AddressSanitizer adds "
              "redzones around globals and stack objects and swaps in an "
              "interceptor allocator, and the runtime must match — so a library "
              "and a consumer built with different -fsanitize= settings are not "
              "compatible. Ship sanitized builds only for testing, and match the "
              "sanitizer set across the library and its consumers."),
    _E("float_abi_changed", _R,
       impact="The floating-point calling convention changed between builds "
              "(-mfloat-abi=soft/softfp/hard; the default is target-dependent). On "
              "ARM the float ABI decides whether floating-point arguments and "
              "returns travel in FP registers (hard) or core registers/memory "
              "(soft), so a function taking or returning a float/double is called "
              "with an incompatible convention across the boundary — a silent "
              "corruption or crash. Build the whole stack with one float ABI."),
    _E("stdlib_debug_mode_changed", _R,
       impact="A standard-library debug/hardening mode was toggled between builds "
              "(_GLIBCXX_DEBUG / _GLIBCXX_ASSERTIONS for libstdc++, "
              "_ITERATOR_DEBUG_LEVEL for the MSVC STL). These modes change the "
              "layout and size of std:: containers (extra debug members / iterator "
              "bookkeeping), so any public type embedding a std:: container by "
              "value, or a function taking one across the boundary, is "
              "ABI-incompatible between a debug-mode build and a normal one. Build "
              "the library and its consumers with the matching setting."),

    # ── Source ABI replay evidence (ADR-028 L4 / ADR-030 D6) ────────────────
    # Produced by the source-replay diff over two linked source ABI surfaces.
    # These recover source/API facts that final artifacts under-represent
    # (macros, default args, inline/template bodies, constexpr, uninstantiated
    # templates). Per ADR-028 D3 / ADR-030 D6 they are never BREAKING on their
    # own: they default to API_BREAK (source breaks) or RISK (deployment/context
    # risk). A shipped-ABI break is still proven only by the artifact diff.
    _E("public_macro_value_changed", _A,
       impact="The value of a macro constant in a public header changed (e.g. "
              "FOO_SIZE). Source that bakes the old value into compiled code "
              "(array sizes, switch labels, struct layout) silently mismatches a "
              "library built with the new value. A source/API break; recompile "
              "consumers against the new headers."),
    _E("default_argument_changed", _A,
       impact="A default argument of a public function changed (e.g. f(int x=1) "
              "to x=2). The signature is unchanged, so old binaries link, but "
              "newly compiled callers that omit the argument get a different "
              "value — a source-visible behavioral break. Build-context replay "
              "adds provenance over header-only detection."),
    _E("inline_body_changed", _R,
       impact="The body of a public inline function changed while no exported "
              "binary symbol changed. Callers that inlined the old body keep the "
              "old behavior until recompiled, so a mixed-build deployment can run "
              "two versions of the same function. A deployment/ODR risk, not a "
              "proven binary break."),
    _E("constexpr_value_changed", _A,
       impact="The value of a public constexpr constant changed. Like a macro "
              "constant, the old value may be baked into consumer code; a "
              "source/API break until consumers are recompiled against the new "
              "headers."),
    _E("template_body_changed", _R,
       impact="The implementation of an uninstantiated public template changed. "
              "No binary symbol exists to compare (the ADR-026 case122 residual), "
              "so this is invisible to artifact comparison; consumers that "
              "instantiate the template pick up the new body on recompile. A "
              "source-visible risk surfaced only by source replay."),
    _E("uninstantiated_template_removed", _A,
       impact="A public template that was never instantiated into a binary symbol "
              "was removed from the headers. Source that instantiates it no longer "
              "compiles; there is no binary footprint, so only source replay sees "
              "it. A source/API break."),
    _E("source_decl_binary_symbol_mismatch", _R,
       impact="A public source declaration no longer maps to an exported binary "
              "symbol — the declaration is present in the headers but absent from "
              "the library's exports. With artifact backing this escalates to the "
              "authoritative removed-export finding; on its own it is a "
              "surface/export consistency risk to investigate."),
    _E("source_binary_provenance_mismatch", _R,
       impact="A large fraction of the source tree's public declarations fail to "
              "map to any exported binary symbol, which strongly suggests the "
              "source checkout does not correspond to the shipped binary (e.g. a "
              "wrong tag/commit). All L4/L5 source findings for this pair are then "
              "untrustworthy; re-check the source out at the binary's build tag. "
              "Per ADR-028 D3 this is a context risk, never a proven binary break."),
    _E("odr_source_conflict", _R,
       impact="The same type name resolves to different definitions across "
              "translation units (One Definition Rule conflict). Linking or "
              "loading code that mixes the definitions is undefined behavior; a "
              "correctness risk surfaced by comparing per-TU source surfaces."),
    _E("generated_header_changed", _R,
       impact="A generated public configuration header changed between versions. "
              "Generated headers encode build-time configuration into the public "
              "API surface, so a change can alter declarations or macro contracts "
              "seen by consumers. Policy may escalate to an API break; by default "
              "a risk to review."),
    _E("public_typedef_target_changed", _A,
       impact="A public typedef/alias now resolves to a different underlying type "
              "(e.g. `typedef int32_t handle_t;` became `typedef int64_t "
              "handle_t;`). Source that relied on the old aliased type — overload "
              "resolution, template specialization, or the type's size in a "
              "consumer-owned struct — can change meaning or fail to compile. "
              "Surfaced by source replay because a bare typedef leaves no exported "
              "symbol of its own; a source/API break until consumers recompile."),
    _E("public_macro_removed", _A,
       impact="A macro that was part of the public header surface was removed. "
              "Macros never reach the binary, so no artifact layer can see the "
              "removal — only source replay does. Source that referenced the macro "
              "(a constant, a feature guard, or a function-like macro) no longer "
              "compiles. A source/API break; provide a replacement or a deprecation "
              "shim, or document the removal for consumers."),
    _E("inline_function_removed", _A,
       impact="A public header-only inline function was removed. Because it was "
              "inline it had no exported binary symbol, so the artifact diff (L0) "
              "sees nothing; only source replay observes the lost declaration. "
              "Source that called the inline no longer compiles. A source/API "
              "break — keep a compatible declaration or move the removal behind a "
              "documented deprecation."),
    _E("public_typedef_removed", _A,
       impact="A public typedef/alias was removed from the headers. A bare typedef "
              "emits no symbol of its own, so the artifact diff is blind; source "
              "replay surfaces the removal. Consumer source that named the alias "
              "(variables, casts, template arguments) no longer compiles. A "
              "source/API break; retain the alias or provide a replacement name."),
    _E("source_fact_coverage_incomplete", _R,
       impact="The L4 source-fact evidence for this comparison is incomplete or "
              "produced by incompatible producers/fact-set versions — a mandatory "
              "fact family (functions, macros, templates, inline bodies, "
              "constexpr values, ...) was 'partial' or 'failed' on one or both "
              "sides, or the old/new fact-set version or producer differ. Per "
              "ADR-038 C.8, absence of another L4 finding must not be read as "
              "proof nothing changed in that family; treat this pair's other "
              "source-replay findings as unreliable until re-collected with a "
              "consistent, complete fact set."),

    # ── Source graph evidence (ADR-028 L5 / ADR-031 D6) ─────────────────────
    _E("public_reachability_changed", _R,
       impact="A public declaration entered or left the public-API reachability "
              "closure (target → public header → declaration → exported symbol) "
              "between versions. Explains and prioritizes impact derived from the "
              "source graph; never on its own decides an ABI break."),
    _E("source_to_binary_mapping_changed", _R,
       impact="A declaration present in both versions now maps to a different "
              "exported binary symbol (or its source↔symbol mapping changed) "
              "without a clear artifact ABI diff. A surface/mapping consistency "
              "risk to investigate, surfaced by comparing source graph summaries."),
    _E("generated_header_reaches_public_api", _R,
       impact="A generated file newly participates in the public declaration "
              "closure (it is a public header, or it declares a reachable public "
              "entity). Build-time-generated content now shapes the public API "
              "surface, so its provenance and reproducibility warrant review."),
    _E("call_graph_public_entry_reachability_changed", _C,
       impact="The set of implementation declarations statically reachable from "
              "an exported entry point changed (per the approximate Clang call "
              "graph). A quality/behavioral signal that the implementation behind "
              "a stable public symbol moved; never an ABI break on its own."),
    _E("include_graph_public_header_drift", _R,
       impact="The transitive include closure behind a public header changed "
              "(per the depfile/-M include graph). Consumers may now pull in "
              "different declarations or macros; a source/API risk to review, "
              "never on its own an artifact-proven ABI break."),
    _E("build_option_reaches_public_symbol", _R,
       impact="A changed ABI-relevant build option feeds a compile unit that "
              "produces an exported public symbol (per the build/source graph). "
              "It localizes a flag-drift risk to the public surface it can affect; "
              "a risk to review, never on its own an artifact-proven ABI break."),
    _E("public_api_internal_dependency_added", _R,
       impact="A public/exported entry point newly reaches an internal "
              "(non-public-header) declaration through the L5 source graph — a "
              "public API now calls or references an entity that lives only in a "
              "private header or source file, where it did not in the prior "
              "version. The public surface has taken on an undeclared dependency, "
              "so a later change to that internal entity becomes a hidden "
              "behavioral risk to the API. The version-over-version analogue of the "
              "intra-version public-to-internal cross-check; a risk to review, "
              "never on its own an artifact-proven ABI break."),
    _E("target_dependency_added", _R,
       impact="The library gained an inter-target build/link dependency (a new "
              "TARGET_DEPENDS_ON edge in the build graph). The shipped artifact may "
              "now require an additional library at load time, so a deployment that "
              "only shipped the old dependency set can fail to resolve at runtime, "
              "and the added dependency's own ABI now transitively affects "
              "consumers. A packaging/deployment risk to review; the artifact's "
              "DT_NEEDED diff proves any concrete new load-time dependency."),
    _E("exported_symbol_source_owner_changed", _R,
       impact="An exported symbol present in both versions is now declared by a "
              "different file (the file owning its declaration moved in the source "
              "graph — e.g. a public declaration relocated to another header, or "
              "its declaring translation unit changed). The symbol name and "
              "signature are unchanged, so the artifact diff is quiet, but the "
              "declaration behind a stable public symbol moved — a refactor that "
              "can change consumers' include paths, inlining, or introduce an ODR "
              "risk if the old location still declares it. A source-graph risk to "
              "review, never on its own an artifact-proven ABI break."),
    # ── Cross-source validation (ADR-035 D4 / G19.2) ────────────────────────
    # Produced by the intra-version cross-source engine (buildsource/crosscheck.py),
    # which diffs ONE merged snapshot's evidence sources against each other rather
    # than comparing two versions. Per ADR-035 D1/D4 they are never BREAKING on
    # their own: an artifact diff still proves a shipped break. They default to
    # RISK (deployment/hygiene risk) or API_BREAK (source-context risk) and stay
    # advisory/suppressible until a check earns its FP-rate-gate corpus.
    _E("exported_not_public", _R,
       impact="A symbol is exported by the binary but no public header declares it "
              "(EXPORT_ONLY provenance). It is reachable ABI surface that consumers "
              "can link against yet was never promised by the API, so it is easy to "
              "change or remove by accident — and equally a sign of a missing "
              "visibility annotation. Hide it (`-fvisibility=hidden` / a version "
              "script) or document it; respects the ABI-relevant-symbol filter and "
              "public-surface scoping so intentional internal exports can be "
              "suppressed."),
    _E("public_not_exported", _R,
       impact="A public header declares an entity that promises an external symbol "
              "(an exported, non-inline, non-template, default-visibility function or "
              "variable) but the binary does not export it. Consumers that compile "
              "against the header get an undefined-symbol link error. Narrowly scoped "
              "to declarations with a real export obligation — inline/templated/"
              "constexpr/hidden-visibility decls are public source surface that "
              "legitimately emit no dynamic symbol and are excluded."),
    _E("header_build_context_mismatch", _A,
       impact="The public headers were parsed without the build's ABI-relevant "
              "context (the L3 build evidence records ABI-affecting flags/macros, but "
              "the header AST was captured context-free). The declared API surface may "
              "therefore not match what the shipped translation units actually "
              "compile to (e.g. a macro-conditional field or a packing pragma is "
              "evaluated differently). Re-dump the headers with the build's "
              "compile_commands.json so the L2 surface reflects the real build."),
    _E("private_header_leak", _R,
       impact="A public header exposes (and so transitively pulls in) a type declared "
              "only in a private / non-installed header — detected from declaration "
              "provenance (origin) and, when present, the L5 include graph. Downstream "
              "consumers that include the public header reference a declaration that is "
              "not shipped, so their build breaks once the private header is absent "
              "from the install tree — a packaging-hygiene risk. Make the public header "
              "self-contained or install the leaked header."),
    _E("odr_type_variant", _A,
       impact="One type has divergent definitions across translation units (the L4 "
              "source-replay surface recorded an ODR conflict: the same qualified name "
              "resolves to different per-TU layouts). Linking code that mixes the "
              "definitions is undefined behavior — a consumer compiled against one "
              "layout silently reads a struct laid out the other way. A source/API "
              "break surfaced from one merged snapshot's L4 evidence; never on its own "
              "an artifact-proven shipped-ABI break. Reconcile the conflicting "
              "definitions (usually a macro/flag that changes the type per TU)."),
    _E("public_to_internal_dependency", _R,
       impact="A public/exported declaration reaches an internal (non-public-header) "
              "entity through the L5 source graph — a public API calls, references, or "
              "embeds a type that lives only in a private header or source file. The "
              "public surface depends on a declaration consumers cannot see, so a "
              "change to that internal entity is an undeclared behavioral risk to the "
              "API. Elevated when the internal entity is among the revision's changed "
              "files. Explains and localizes risk from the source graph; never on its "
              "own an artifact-proven ABI break."),
    # ── Single-release hygiene audit (ADR-035 D8 / G19.6) ───────────────────
    # Intra-version "bad ABI hygiene" the same cross-source engine surfaces from
    # ONE build (no baseline), exposed through `scan --audit` / `surface-report
    # --audit`. RISK, advisory until promoted; never BREAKING on their own.
    _E("unversioned_exported_symbol", _R,
       impact="The library defines a symbol-versioning scheme (a version script /"
              " .gnu.version_d table) yet exports this symbol without a version node. "
              "Unversioned exports cannot be evolved compatibly later — consumers bind "
              "to the bare name with no version guarantee, so a future versioned "
              "release silently changes what they resolve to. Add the symbol to the "
              "version script (or hide it if it is not public API). A single-release "
              "hygiene risk, never on its own an artifact-proven ABI break."),
    _E("rtti_for_internal_type", _R,
       impact="The binary exports RTTI (typeinfo/vtable, `_ZTI`/`_ZTV`/`_ZTS`) for a "
              "polymorphic type that is declared only in a private / non-installed "
              "header. The type's run-time type information leaks onto the ABI surface "
              "even though consumers cannot name the type, which both bloats the export "
              "set and risks cross-module RTTI/`dynamic_cast` coupling to an internal "
              "class. Hide the internal type (anonymous namespace / "
              "`-fvisibility=hidden`) or stop exporting its typeinfo. A single-release "
              "hygiene risk, never on its own an artifact-proven ABI break."),
    # ── Cross-implementation standard-library compatibility (D-stdlib) ───────
    # Produced by the build-mode diff (diff_stdlib_impl.py). Compatibility
    # between *different* C++ standard-library implementations is a third axis
    # the standard never guarantees: a class that holds a std:: container by
    # value gets a different layout under libstdc++ vs libc++ vs MSVC STL, so
    # the same source linked against a mismatched runtime is silently
    # ABI-incompatible. These default to RISK — when an embedded stdlib type's
    # layout actually differs, the type diff emits the BREAKING size/offset
    # finding separately; these explain and localize it and never escalate on
    # their own. They are emitted only when build-mode evidence is present on
    # both sides; absent that, the diff stays silent rather than guessing.
    _E("stdlib_implementation_changed", _R,
       impact="The two artifacts were built against different C++ standard-library "
              "implementations (e.g. libstdc++ vs libc++, or vs MSVC STL). The "
              "standard does not guarantee ABI compatibility across implementations: "
              "any public type embedding a std:: container/string by value gets a "
              "different layout, and inline std:: code can ODR-conflict. Pin a single "
              "implementation or rebuild consumers against the matching runtime."),
    _E("libcpp_abi_version_changed", _R,
       impact="The libc++ ABI version changed (e.g. _LIBCPP_ABI_VERSION 1 → 2). "
              "libc++ selects incompatible internal layouts for std:: types via an "
              "inline namespace (std::__1 vs std::__2), so types embedding them by "
              "value are laid out differently. Rebuild consumers against the matching "
              "libc++ ABI version.",
       description_template="libc++ ABI version changed ({old} → {new}). libc++ selects incompatible internal layouts for std:: types via an inline namespace (std::__{old} vs std::__{new}); types embedding them by value are laid out differently. Rebuild consumers against the matching libc++ ABI version."),

    # ── Fine-grained class-layout descriptor (layout-closure work) ───────────
    # Produced by diff_layout.py from the optional RecordType layout fields
    # (base offsets, vptr offset, dsize/tail-padding, standard-layout /
    # trivially-copyable traits). They capture layout mechanics the coarse
    # size/offset detectors under-represent. Each is guarded tri-state — emitted
    # only when both sides carry the evidence — so an evidence-tier downgrade
    # never fabricates one.
    _E("base_class_offset_changed", _B,
       impact="A base-class subobject moved to a different offset within the derived "
              "object (e.g. an empty-base optimization was lost, or a member/base was "
              "inserted ahead of it) without the base list reordering. The `this` "
              "pointer adjustment for that base and every field after it shifts; old "
              "binaries read the wrong addresses.",
       description_template="Base class '{detail}' moved within '{name}' ({old} → {new} bits). The `this`-pointer adjustment for that base and the offset of every field after it shift; existing binaries read the wrong addresses."),
    _E("vptr_introduced", _B,
       impact="A previously non-polymorphic class gained its first virtual function, "
              "so the compiler prepends a vtable pointer. sizeof grows and every data "
              "member's offset shifts by a pointer width; existing binaries that embed "
              "or derive from the type are laid out incompatibly.",
       description_template="'{name}' gained a vtable pointer (became polymorphic). sizeof grows and every data member's offset shifts by a pointer width; binaries that embed or derive from the type are laid out incompatibly."),
    _E("trivially_copyable_lost", _B,
       impact="A type stopped being trivially copyable (e.g. a user-declared "
              "copy/move constructor, destructor, or a non-trivial member was added). "
              "Non-trivially-copyable types are passed and returned by value "
              "differently (via a hidden reference / not in registers), so the calling "
              "convention for any function taking or returning it by value changes.",
       description_template="'{name}' is no longer trivially copyable. It is now passed and returned by value differently (via a hidden reference / not in registers), so the calling convention of any function taking or returning it by value changes."),
    _E("standard_layout_lost", _R,
       impact="A type stopped being standard-layout (e.g. it gained a mix of access "
              "specifiers, a base with members, or virtual members). `offsetof` and "
              "C interoperability are no longer guaranteed and tail-padding reuse "
              "rules change; review code that relies on the C-compatible layout.",
       description_template="'{name}' is no longer standard-layout. `offsetof` and C interoperability are no longer guaranteed and tail-padding reuse rules change; review code relying on the C-compatible layout."),
    _E("tail_padding_reuse_changed", _R,
       impact="The type's data size (the bytes its own members occupy, excluding "
              "trailing tail padding) changed while sizeof stayed the same. A derived "
              "class may reuse a base's tail padding, so this can silently shift a "
              "derived layout even though the base's sizeof is unchanged.",
       description_template="'{name}' data size changed ({old} → {new} bits) while sizeof stayed {detail} bits. A derived class may reuse this type's tail padding, so a derived layout can shift even though sizeof is unchanged."),
    _E("layout_unverifiable", _R,
       impact="A public type's layout could not be verified at the available evidence "
              "tier — its size/offsets are not present (e.g. a symbols-only or partial "
              "dump with no debug info), so a real layout change cannot be ruled out. "
              "Informational and non-escalating; rebuild with debug info (or supply "
              "headers) to confirm.",
       description_template="'{name}' layout could not be verified: one side carries a layout descriptor but the other has no layout evidence (no size/offsets). A real layout change cannot be ruled out — rebuild with debug info (or supply headers) to confirm. Informational and non-escalating."),

    # ── Binary-only (no-DWARF / L0) C++ layout descriptors ───────────────────
    # Recovered from .dynsym symbol sizes alone by diff_elf_layout.py. The
    # Itanium C++ ABI fixes the on-disk size of a class's vtable (`_ZTV`) and
    # typeinfo (`_ZTI`) objects, so these break detections work on libraries
    # shipped without any DWARF debug info or public headers.
    _E("vtable_slot_count_changed", _B,
       impact="A polymorphic class's vtable changed size — its `_ZTV` object now holds "
              "a different number of virtual-function slots (a virtual method was added, "
              "removed, or reordered). Existing binaries dispatch through fixed vtable "
              "offsets, so they call the wrong slot or run off the end of the table. "
              "Recovered from the ELF symbol size without DWARF — the binary-only analogue "
              "of FUNC_VIRTUAL_ADDED / TYPE_VTABLE_CHANGED.",
       description_template="Vtable for '{name}' changed size: {old} → {new} bytes ({detail}). A virtual method was added, removed, or reordered; existing binaries dispatch through fixed vtable offsets and will call the wrong slot. Detected from the ELF symbol size without debug info."),
    _E("rtti_inheritance_changed", _B,
       impact="A polymorphic class's RTTI typeinfo (`_ZTI`) object changed size, which in "
              "the Itanium C++ ABI means its base-class shape changed: no-base "
              "(`__class_type_info`, 2 words) ↔ single-base (`__si_class_type_info`, "
              "3 words) ↔ multiple/virtual-base (`__vmi_class_type_info`, larger), or the "
              "number of bases differs. Base-class changes shift `this`-pointer "
              "adjustments, member offsets, and the vtable, so derived classes and "
              "by-value users are miscompiled. Recovered from the ELF symbol size without "
              "DWARF — the binary-only analogue of TYPE_BASE_CHANGED.",
       description_template="RTTI typeinfo for '{name}' changed size: {old} → {new} bytes ({detail}). The base-class shape changed, which shifts this-pointer adjustments, member offsets, and the vtable. Detected from the ELF symbol size without debug info."),

    # ── CPython extension modules (abi3 / Py_LIMITED_API) ─────────────────────
    _E("python_stable_abi_violation", _R,
       impact="A stable-ABI (`abi3` / `Py_LIMITED_API`) CPython extension module — "
              "produced by Cython, pybind11, nanobind, or a hand-written C "
              "extension — gained an import of a CPython C-API symbol that is not "
              "part of the Limited API (typically a private `_Py*` symbol). The "
              "module still exports only `PyInit_<mod>`, so the export-table view "
              "sees no change, but the module now links a symbol outside its abi3 "
              "promise. On an interpreter built without that symbol exported it "
              "fails to import with an `undefined symbol` error. Verdict is a "
              "deployment RISK: whether it breaks depends on the target "
              "interpreter, not on the module's own consumers.",
       description_template="abi3 extension '{name}' imports non-stable CPython symbol: {detail}"),
    _E("python_abi3_dropped", _R,
       impact="A CPython extension module that was previously a stable-ABI "
              "(`abi3` / `Py_LIMITED_API`) build — loadable on every interpreter "
              "at or above its floor — is now a version-specific build (its SOABI "
              "tag pins it to a single `cpython-3XX`). Consumers running any other "
              "interpreter in the module's former supported range can no longer "
              "import it. Nothing in the export table reveals the narrowed "
              "support; the promise lived in the wheel/SOABI tag. A deployment "
              "RISK for anyone not on the exact new interpreter.",
       description_template="extension '{name}' dropped its abi3 promise: {old} → {new}"),
    _E("python_gil_abi_changed", _R,
       impact="A CPython extension module switched between the regular (GIL) and "
              "the free-threaded (PEP 703, `Py_GIL_DISABLED`) CPython ABI — its "
              "SOABI tag gained or lost the free-threaded `t` marker "
              "(`cpython-3XX` ↔ `cpython-3XXt`). The two builds target different, "
              "non-interchangeable interpreter ABIs: a consumer running the "
              "regular interpreter cannot load a free-threaded build and vice "
              "versa (different extension suffix, different struct layouts, and — "
              "since `Py_LIMITED_API` is incompatible with `Py_GIL_DISABLED` — a "
              "free-threaded build can never be `abi3`). A deployment RISK: "
              "whether it breaks depends on which interpreter the consumer runs.",
       description_template="extension '{name}' changed GIL/free-threaded ABI: {old} → {new}"),
    _E("python_abi3_floor_raised", _R,
       impact="Both builds of a CPython extension are stable-ABI (`abi3`) and both "
              "carry an explicit `cpXY-abi3` wheel/SOABI tag, but the new build's "
              "declared `Py_LIMITED_API` floor is higher than the old one's "
              "(e.g. `cp39-abi3` → `cp310-abi3`). Every interpreter in the dropped "
              "range — CPython at or above the old floor but below the new one — "
              "can no longer import the module, even though its exported and "
              "imported symbols may be unchanged. Because the floor is read from "
              "the explicit tag on *both* sides, this is exact (no heuristic "
              "min-of-imports inference). A deployment RISK: whether it breaks "
              "depends on which interpreters the consumer must support.",
       description_template="abi3 extension '{name}' raised its Py_LIMITED_API floor: {old} → {new}"),
    # ── G23 Phase A — Linux ELF artifact facts ──────────────────────────────
    # A1: static-TLS drift.
    _E("static_tls_introduced", _R,
       impact="The library set DF_STATIC_TLS: it now uses the static "
              "(initial-exec / local-exec) TLS model. Such a library can no "
              "longer be reliably dlopen()ed — the dynamic loader may fail with "
              "'cannot allocate memory in static TLS block' when the process's "
              "static TLS surplus is exhausted. Link-time consumers are "
              "unaffected, so this defaults to RISK; gate it to break via the "
              "plugin/security policy if the library is meant to be dlopen-loadable. "
              "The flag-level TLS_MODEL_CHANGED (L3) explains which build flag "
              "caused it; this kind proves the artifact effect.",
       description_template="Static-TLS model introduced (DF_STATIC_TLS set): the library may no longer be reliably dlopen()ed"),
    _E("static_tls_removed", _C,
       impact="DF_STATIC_TLS was cleared: the library returned to the dynamic "
              "TLS model and is dlopen-friendly again. Informational improvement.",
       description_template="Static-TLS model removed (DF_STATIC_TLS cleared) — dlopen-friendly again"),

    # A2: GNU-property control-flow-protection drift.
    _E("cet_protection_weakened", _R,
       impact="An x86 CET control-flow-protection feature (IBT and/or SHSTK) was "
              "dropped from .note.gnu.property. CET is enforced per link map: a "
              "single non-IBT DSO disables indirect-branch tracking for the whole "
              "process, so weakening it silently lowers the runtime hardening of "
              "every consumer. RISK by default; the shipped security policy gates "
              "it to break.",
       description_template="CET protection weakened: {old} → {new}"),
    _E("branch_protection_weakened", _R,
       impact="An AArch64 branch-protection feature (BTI and/or PAC) was dropped "
              "from .note.gnu.property. Like CET, BTI enforcement is process-wide, "
              "so a single non-BTI DSO weakens the guarantee for the whole link "
              "map. RISK by default; gated to break by the security policy.",
       description_template="Branch protection weakened: {old} → {new}"),
    _E("cet_protection_improved", _C,
       impact="An x86 CET feature (IBT/SHSTK) was added to .note.gnu.property — "
              "a hardening improvement. Informational.",
       description_template="CET protection improved: {old} → {new}"),
    _E("branch_protection_improved", _C,
       impact="An AArch64 branch-protection feature (BTI/PAC) was added to "
              ".note.gnu.property — a hardening improvement. Informational.",
       description_template="Branch protection improved: {old} → {new}"),

    # A3: ELF identity / ABI-flags guard.
    _E("elf_machine_changed", _B,
       impact="The ELF e_machine (target architecture) changed. The two inputs "
              "are different-architecture binaries — nothing about their ABI is "
              "comparable, and a consumer built for one cannot load the other. "
              "The ELF-side analogue of PE_MACHINE_CHANGED / MACHO_CPU_TYPE_CHANGED.",
       description_template="ELF machine changed: {old} → {new} — different target architecture"),
    _E("elf_class_changed", _B,
       impact="The ELF class changed between 32-bit and 64-bit. Pointer width, "
              "type sizes, and the calling convention all differ; no consumer "
              "built against one class can use the other.",
       description_template="ELF class changed: {old}-bit → {new}-bit"),
    _E("elf_abi_flags_changed", _B,
       impact="The ELF e_flags ABI-selecting bits changed — the float ABI "
              "(hard/soft-float), EABI version, or base ISA differs between "
              "versions. Object code compiled against the old convention passes "
              "floating-point arguments in the wrong registers/stack slots, "
              "silently corrupting calls. Artifact-proven from e_flags; the "
              "flag-level FLOAT_ABI_CHANGED (L3) stays the explanatory signal.",
       description_template="ELF ABI flags changed: {old} → {new}"),
    _E("elf_osabi_changed", _R,
       impact="The ELF EI_OSABI (target OS ABI) changed (e.g. SYSV ↔ GNU/Linux ↔ "
              "FreeBSD). This can alter the meaning of OS-specific symbol types "
              "and relocations; consumers may resolve or load differently. RISK.",
       description_template="ELF OS ABI changed: {old} → {new}"),

    # A4: STB_GNU_UNIQUE binding transitions.
    _E("symbol_binding_became_unique", _R,
       impact="An exported symbol's binding became STB_GNU_UNIQUE. GNU-unique "
              "symbols are enforced as process-wide unique by the dynamic loader, "
              "and a library that defines one becomes non-unloadable — dlclose() "
              "is inhibited for it. Changes loader semantics for consumers that "
              "rely on unloading. RISK.",
       description_template="Symbol binding became GNU_UNIQUE: {name} — inhibits dlclose() on this library"),
    _E("symbol_binding_lost_unique", _R,
       impact="An exported symbol's binding was STB_GNU_UNIQUE and is no longer. "
              "The process-wide ODR-uniqueness guarantee that consumers may have "
              "relied on (a single shared instance of an inline/template static "
              "across all DSOs) is gone; duplicate per-DSO instances may reappear. "
              "RISK.",
       description_template="Symbol binding lost GNU_UNIQUE: {name} — process-wide uniqueness guarantee removed"),

    # ── G23 Phase B1 — Itanium multi-inheritance vtable machinery (L0) ───────
    _E("vtable_thunk_offset_changed", _B,
       impact="A virtual-override thunk's this-pointer adjustment offset changed "
              "(e.g. `_ZThn8_` → `_ZThn16_` for the same target method). In the "
              "Itanium C++ ABI a thunk fixes up `this` when a call arrives through "
              "a secondary base's vtable, and the adjustment is baked into the "
              "vtables of every already-compiled consumer. A changed offset means "
              "a base subobject moved, so old binaries adjust `this` by the wrong "
              "amount and corrupt memory on virtual dispatch — with no symbol "
              "error. Recovered from the thunk symbol name alone (no DWARF), so it "
              "is caught even on stripped binaries where the primary-vtable _ZTV "
              "size is unchanged.",
       description_template="Vtable thunk offset changed for {name}: {old} → {new} — a base subobject moved; old binaries mis-adjust `this` on virtual dispatch"),
    _E("vtable_thunk_set_changed", _B,
       impact="A method that persists across versions gained or lost a "
              "virtual-override thunk. A thunk appears when a class overrides a "
              "virtual inherited through a *secondary* (multiple-inheritance) "
              "base; its appearance/disappearance means the override was added or "
              "removed in a secondary vtable. Because the inherited slot itself "
              "persists, the primary-vtable _ZTV size can be unchanged, so this is "
              "invisible to the slot-count diff. Old binaries dispatch to the "
              "wrong target through the secondary vtable.",
       description_template="Vtable thunk set changed for {name}: {detail} — a secondary-base override was added or removed"),
    _E("vtt_slot_count_changed", _B,
       impact="A class's VTT (virtual-table-table, `_ZTT`) object changed size. "
              "The VTT is the construction scaffolding the Itanium ABI uses to "
              "initialize the vtable pointers of virtual bases during "
              "construction/destruction; its size encodes the number of "
              "sub-vtables. A change means the virtual-inheritance shape changed, "
              "so a constructor compiled against the old VTT installs the wrong "
              "vptrs. Recovered from the `_ZTT` symbol size alone (no DWARF).",
       description_template="VTT size changed for '{name}': {old} → {new} bytes — virtual-base construction scaffolding changed"),
    # B2: L1 DWARF vtable-group reconstruction.
    _E("secondary_vtable_group_changed", _B,
       impact="A polymorphic class's set of *secondary* vtable groups changed even "
              "though its own base declaration list did not — a direct or virtual "
              "base gained or lost virtual functions, so it started or stopped "
              "owning a secondary vtable group in the derived class. In the Itanium "
              "C++ ABI each polymorphic non-primary base contributes its own vtable "
              "group with its own this-adjustment; adding, removing, or reordering "
              "a group shifts every following group and the this-offsets baked into "
              "already-compiled consumers, so virtual dispatch through the affected "
              "base lands on the wrong slot. Reconstructed from DWARF inheritance "
              "(L1), catching a cross-type effect the per-type base/field diff — "
              "which only sees the unchanged derived class — cannot.",
       description_template="Secondary vtable groups changed for '{name}': {old} → {new} — a base's polymorphism changed, restructuring the derived vtable"),
    _E("virtual_base_offset_changed", _B,
       impact="A class's virtual bases were reordered with the base set unchanged, "
              "so the virtual-base offset table (vbase offsets stored in the "
              "vtable) is laid out in a different order. The this-pointer "
              "adjustment used to reach a virtual base is baked into old binaries; "
              "after a reorder those adjustments point at the wrong subobject, "
              "corrupting access to virtual-base members with no symbol error. "
              "Detected from the DWARF virtual-inheritance order (L1); a pure "
              "virtual-base reorder is invisible to the non-virtual "
              "base_class_position_changed check.",
       description_template="Virtual base order changed for '{name}': {old} → {new} — vbase offset table reordered; old binaries mis-adjust `this` to virtual bases"),

    # ── G23 Phase D — ecosystem detectors ───────────────────────────────────
    # D3: unnamed-type leakage.
    _E("unnamed_type_in_public_abi", _R,
       impact="An exported symbol embeds an unnamed type in its mangled name — a "
              "lambda closure (`Ul…E_`) or an unnamed struct/enum (`Ut…_`). The "
              "Itanium mangling of unnamed types is per-translation-unit and "
              "compiler-ordering dependent (recompiling, or merely reordering "
              "unrelated declarations, can renumber `{lambda#1}` → `{lambda#2}`), "
              "so exporting one is an ABI time bomb: a rebuilt consumer can fail to "
              "resolve the symbol. RISK / hygiene — reported when newly introduced.",
       description_template="Unnamed type leaks into the public ABI: {name} ({detail}) — its mangled name is compiler-ordering-fragile"),
    # D2: long-double representation change.
    _E("long_double_abi_changed", _B,
       impact="A function's `long double` parameter or return representation "
              "changed — e.g. ppc64 migrating IBM double-double ↔ IEEE binary128, "
              "or `-mlong-double-64` shrinking 80-bit x87 to 64-bit. The source "
              "signature is unchanged, but the floating-point format differs, so "
              "old binaries pass/return the value in the wrong size and bit layout, "
              "silently corrupting it. Detected from the Itanium long-double "
              "mangling token (`e`/`g`/`u9__ieee128`) on a removed↔added pair, or "
              "from the `long double` DWARF byte size on a persisting symbol.",
       description_template="long double ABI changed: {detail} — floating-point representation differs (symbol {old} → {new})"),
    # D1: kABI (Module.symvers).
    _E("kabi_symbol_removed", _B,
       impact="A kernel-exported symbol (EXPORT_SYMBOL*) was removed from "
              "Module.symvers. Out-of-tree modules that reference it fail to load "
              "with 'Unknown symbol'.",
       description_template="Kernel-exported symbol removed: {name}"),
    _E("kabi_crc_changed", _B,
       impact="A kernel-exported symbol's genksyms CRC changed. Even though the "
              "symbol still exists, CONFIG_MODVERSIONS embeds the old CRC in "
              "out-of-tree modules and the loader rejects the module ('disagrees "
              "about version of symbol') — the type signature behind the symbol "
              "changed.",
       description_template="Kernel symbol CRC changed: {name} ({old} → {new}) — modversions will reject the module"),
    _E("kabi_symbol_namespace_changed", _B,
       impact="A kernel-exported symbol gained or moved its export namespace "
              "(EXPORT_SYMBOL_NS*). A module that does not declare the matching "
              "MODULE_IMPORT_NS() fails to load, so a gained/changed namespace is a "
              "load-time break for existing modules.",
       description_template="Kernel symbol namespace changed: {name} ({old} → {new})"),
    _E("kabi_export_type_changed", _A,
       impact="A kernel-exported symbol changed between EXPORT_SYMBOL and "
              "EXPORT_SYMBOL_GPL. A non-GPL module that used a symbol now marked "
              "GPL-only can no longer link against it — a license-gated "
              "availability break for that class of consumer.",
       description_template="Kernel symbol export type changed: {name} ({old} → {new})"),
    _E("kabi_symbol_added", _C, is_addition=True,
       impact="A new kernel-exported symbol appeared; existing modules are unaffected.",
       description_template="New kernel-exported symbol: {name}"),

    # ── Python-level API of an extension module (G23) ─────────────────────────
    _E("python_api_function_removed", _A,
       impact="A public top-level function was removed from a CPython extension "
              "module's Python-visible API (recovered from its `.pyi` type "
              "stub). The compiled `.so`/`.pyd` still loads — its C-ABI export "
              "table is unchanged — but any consumer that `import`s and calls "
              "the function now fails with an `AttributeError` / `ImportError`. "
              "A source-level (`API_BREAK`) change the native-ABI check cannot "
              "see.",
       description_template="Python function removed from extension API: {name}"),
    _E("python_api_function_added", _C, is_addition=True,
       impact="A new public top-level function was added to the module's "
              "Python-visible API. Additive — existing callers are unaffected.",
       description_template="New Python function in extension API: {name}"),
    _E("python_api_class_removed", _A,
       impact="A public class was removed from a CPython extension module's "
              "Python-visible API. The binary still loads, but consumers that "
              "reference the class break at import/attribute-access time. A "
              "source-level (`API_BREAK`) change invisible to the C-ABI view.",
       description_template="Python class removed from extension API: {name}"),
    _E("python_api_class_added", _C, is_addition=True,
       impact="A new public class was added to the module's Python-visible API. "
              "Additive — existing callers are unaffected.",
       description_template="New Python class in extension API: {name}"),
    _E("python_api_method_removed", _A,
       impact="A public method was removed from a class that still exists in the "
              "module's Python-visible API. Callers of the method break at "
              "attribute-access time even though the class and the compiled "
              "binary are otherwise unchanged. Source-level (`API_BREAK`).",
       description_template="Python method removed from extension API: {name}"),
    _E("python_api_method_added", _C, is_addition=True,
       impact="A new public method was added to an existing class in the "
              "module's Python-visible API. Additive — existing callers are "
              "unaffected.",
       description_template="New Python method in extension API: {name}"),
    _E("python_api_parameter_removed", _A,
       impact="A parameter was removed from a function/method in the module's "
              "Python-visible API. Any caller that passed that argument (by "
              "position or keyword) now raises a `TypeError`. The C-ABI is "
              "unchanged; the break lives in the Python signature. "
              "Source-level (`API_BREAK`).",
       description_template="Python parameter removed from {name}: {detail}"),
    _E("python_api_parameter_added", _A,
       impact="A new *required* parameter (one with no default) was added to a "
              "function/method in the module's Python-visible API. Every "
              "existing call that omitted it now raises a missing-argument "
              "`TypeError`. Source-level (`API_BREAK`); a new *optional* "
              "parameter would be compatible and is not reported.",
       description_template="Required Python parameter added to {name}: {detail}"),
    _E("python_api_parameter_renamed", _A,
       impact="A parameter was renamed in a function/method of the module's "
              "Python-visible API. Callers that passed it by keyword hit an "
              "unexpected-keyword `TypeError`. The compiled binary is "
              "byte-identical — this is the canonical break the native-ABI "
              "check misses. Source-level (`API_BREAK`).",
       description_template="Python parameter renamed in {name}: {old} → {new}"),
    _E("python_api_default_removed", _A,
       impact="A parameter lost its default value in the module's "
              "Python-visible API, making a previously optional argument "
              "mandatory. Callers relying on the default now raise a "
              "missing-argument `TypeError`. Source-level (`API_BREAK`).",
       description_template="Python parameter default removed in {name}: {detail}"),
    _E("python_api_parameter_type_changed", _R,
       impact="A parameter's type annotation changed in the module's "
              "Python-visible API. This is a type-checker / behavioural "
              "signal, not a hard runtime break: existing calls still execute, "
              "but static analysis and callers relying on the old contract may "
              "be affected. A `RISK`.",
       description_template="Python parameter type changed in {name}: {detail} ({old} → {new})"),
    _E("python_api_return_type_changed", _R,
       impact="A function/method's return type annotation changed in the "
              "module's Python-visible API. Callers may mishandle the returned "
              "value, but existing calls still execute — a behavioural / "
              "type-checker `RISK`, not a hard break.",
       description_template="Python return type changed for {name}: {old} → {new}"),
    _E("python_api_parameter_kind_changed", _A,
       impact="A parameter's *binding* changed in the module's Python-visible "
              "API even though its name did not: it went positional↔keyword-only, "
              "keyword→positional-only, or the positional order/position shifted "
              "(a reordered or mid-inserted parameter). Existing call sites that "
              "pass the argument by position or by keyword now bind it "
              "differently — a positional caller lands on the wrong parameter, or "
              "a keyword caller hits an unexpected-keyword `TypeError`. The "
              "compiled binary is unchanged; the break lives in the call shape. "
              "Source-level (`API_BREAK`).",
       description_template="Python parameter binding changed in {name}: {detail}"),
    _E("python_api_callable_kind_changed", _A,
       impact="A callable's *protocol* changed in the module's Python-visible "
              "API even though its parameter list did not: `def`↔`async def` "
              "(callers must now `await`, or must stop awaiting, the result), or "
              "a class member changed between instance method, `@staticmethod`, "
              "`@classmethod`, and `@property`. Each of these changes how an "
              "existing site calls or accesses the member — an awaited call, a "
              "class-level vs instance-level bind, or attribute access vs a call "
              "— so it breaks callers. The compiled binary is unchanged. "
              "Source-level (`API_BREAK`).",
       description_template="Python callable kind changed for {name}: {detail}"),
    _E("python_api_overload_removed", _A,
       impact="An `@overload` signature variant was dropped from an overloaded "
              "function/method in the module's Python-visible API. Typed callers "
              "that relied on that particular call shape (e.g. passing an `int` "
              "where only a `str` overload now remains) lose a supported "
              "signature — a source-level break invisible to the export table. "
              "Adding an overload is compatible and not reported. "
              "Source-level (`API_BREAK`).",
       description_template="Python overload removed from {name}: {detail}"),

    # ── Toolchain / runtime environment drift (binutils & glibc skew) ────────
    _E("runtime_floor_raised", _R,
       impact="The maximum symbol version this binary requires from a provider "
              "library rose (e.g. GLIBC_2.28 → GLIBC_2.34). The binary is "
              "interface-identical for existing consumers but no longer loads on "
              "runtimes older than the new floor — a deployment-envelope change, "
              "typically caused by rebuilding/relinking on a newer distro or "
              "sysroot rather than by a source change. Check the listed symbols: "
              "a floor pulled up only by symbols like __libc_start_main is a pure "
              "relink artifact; a new API symbol means the code now genuinely "
              "depends on the newer runtime.",
       description_template="Runtime floor raised for {detail}: {old} → {new} (required by: {name})"),
    # platform_baseline_floor_raised (G10) lives in change_registry_coverage.py
    # to keep this module under the AI-readiness 2000-line hard cap.
    _E("dt_relr_introduced", _R,
       impact="The linker enabled packed relative relocations (DT_RELR, "
              "`-z pack-relative-relocs`; default on some distros since "
              "binutils 2.38). A DT_RELR binary requires glibc ≥ 2.36 (or an "
              "equivalent loader) — older dynamic loaders refuse to load it. "
              "glibc marks this with a synthetic GLIBC_ABI_DT_RELR version "
              "requirement. Rebuild with `-z nopack-relative-relocs` to keep "
              "supporting older runtimes.",
       description_template="Packed relative relocations introduced (DT_RELR): requires glibc >= 2.36 or equivalent loader"),
    _E("dt_relr_removed", _C,
       impact="Packed relative relocations (DT_RELR) were dropped; the binary "
              "loads on older dynamic loaders again. Slightly larger relocation "
              "tables, no compatibility cost.",
       description_template="Packed relative relocations removed (DT_RELR): loader floor lowered"),
    _E("rpath_type_changed", _R,
       impact="The library-search tag type flipped between DT_RPATH and "
              "DT_RUNPATH (ld --enable-new-dtags default drift). The two have "
              "different lookup semantics: DT_RPATH applies to the whole "
              "dependency subtree and takes precedence over LD_LIBRARY_PATH, "
              "while DT_RUNPATH applies only to the object's direct dependencies "
              "and is overridden by LD_LIBRARY_PATH. Transitive dependencies or "
              "environment overrides that resolved before may now resolve "
              "differently (or not at all).",
       description_template="Library search tag type changed: {old} → {new} (lookup semantics differ)"),
    _E("hash_style_removed", _R,
       impact="A symbol-hash table style present in the old binary was dropped "
              "(ld --hash-style default drift): SysV `.hash` and/or GNU "
              "`.gnu.hash`. Dynamic loaders and tools that only support the "
              "dropped style (very old glibc, some non-GNU loaders, MIPS "
              "toolchains for `.hash`) can no longer resolve symbols from this "
              "library.",
       description_template="Symbol hash table style removed: {old} → {new}"),
    _E("time64_abi_changed", _B,
       impact="The time64/large-file ABI flipped: time_t/off_t-family public "
              "typedefs changed width together (glibc `_TIME_BITS=64` / "
              "`_FILE_OFFSET_BITS=64`, available since glibc 2.34, sometimes "
              "flipped by a toolchain or distro default on 32-bit targets). "
              "Every public function or struct carrying one of these typedefs "
              "changed layout — old binaries pass 32-bit values where the new "
              "library reads 64-bit ones (or vice versa). The per-symbol breaking "
              "findings share this single root cause; align _TIME_BITS/"
              "_FILE_OFFSET_BITS across the library and its consumers.",
       description_template="time64/LFS ABI flip detected: {detail}"),

    _E("python_api_stub_invalid", _A,
       impact="A shipped Python type stub for the new extension artifact could "
              "not be safely parsed (syntax error, unreadable file, or size "
              "limit). The Python API surface is therefore untrusted and must "
              "fail closed rather than disabling Python-level API checks.",
       description_template="Invalid Python API stub for extension module: {detail}"),
    # Coverage-extension, composition-compatibility, build-source (L3/L4/L5),
    # NumPy C-API (G26), wheel deployment-claim (G27), CastXML schema-
    # completeness, and suppression reachability (ADR-044) kinds each live in
    # their own change_registry_*.py file to keep this file under the cap.
    *COVERAGE_EXTENSION_ENTRIES,
    *COMPOSITION_EXTENSION_ENTRIES,
    *BUILDSOURCE_EXTENSION_ENTRIES,
    *NUMPY_EXTENSION_ENTRIES,
    *WHEEL_DEPLOYMENT_EXTENSION_ENTRIES,
    *CASTXML_EXTENSION_ENTRIES,
    *SUPPRESSION_EXTENSION_ENTRIES,
])
