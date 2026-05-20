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

from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    NO_CHANGE = "NO_CHANGE"
    COMPATIBLE = "COMPATIBLE"
    COMPATIBLE_WITH_RISK = "COMPATIBLE_WITH_RISK"
    API_BREAK = "API_BREAK"
    BREAKING = "BREAKING"


@dataclass(frozen=True)
class ChangeKindMeta:
    """All metadata for a single ChangeKind, declared in one place."""

    kind: str  # ChangeKind enum value (e.g. "func_removed")
    default_verdict: Verdict
    impact: str = ""
    is_addition: bool = False
    policy_overrides: dict[str, Verdict] = field(default_factory=dict)


class ChangeKindRegistry:
    """Registry of ChangeKindMeta entries, deriving classification sets.

    Usage::

        registry = ChangeKindRegistry(entries)
        breaking = registry.kinds_for_verdict(Verdict.BREAKING)
        impact = registry.impact_for("func_removed")
    """

    def __init__(self, entries: list[ChangeKindMeta]) -> None:
        self._entries: dict[str, ChangeKindMeta] = {}
        for e in entries:
            if e.kind in self._entries:
                raise ValueError(f"Duplicate registry entry for {e.kind!r}")
            self._entries[e.kind] = e

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, kind_value: str) -> bool:
        return kind_value in self._entries

    def get(self, kind_value: str) -> ChangeKindMeta | None:
        return self._entries.get(kind_value)

    def kinds_for_verdict(self, verdict: Verdict) -> frozenset[str]:
        """Return all kind values whose default_verdict matches."""
        return frozenset(
            e.kind for e in self._entries.values() if e.default_verdict == verdict
        )

    def addition_kinds(self) -> frozenset[str]:
        """Return kind values flagged as additions (subset of COMPATIBLE)."""
        return frozenset(
            e.kind
            for e in self._entries.values()
            if e.is_addition
        )

    def policy_overrides_for(self, policy: str) -> dict[str, Verdict]:
        """Return {kind_value: overridden_verdict} for a given policy name."""
        return {
            e.kind: e.policy_overrides[policy]
            for e in self._entries.values()
            if policy in e.policy_overrides
        }

    def impact_text(self) -> dict[str, str]:
        """Return {kind_value: impact} for all entries with non-empty impact."""
        return {e.kind: e.impact for e in self._entries.values() if e.impact}

    @property
    def entries(self) -> dict[str, ChangeKindMeta]:
        return dict(self._entries)


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
    _E("func_removed_elf_only", _C,
       impact="Symbol removed from ELF but was not in public headers; low risk unless dlsym() callers depend on it."),
    _E("func_removed_from_binary", _B,
       impact="Header-declared function disappeared from .dynsym; consumers' PLT entries will fail to resolve at load time."),
    _E("func_added", _C, is_addition=True,
       impact="New function available; existing binaries are unaffected."),
    _E("func_return_changed", _B,
       impact="Callers expect the old return type layout in registers/stack; misinterpretation causes data corruption."),
    _E("func_params_changed", _B,
       impact="Callers push arguments with the old layout; callee reads wrong data from stack/registers."),
    _E("func_noexcept_added", _C,
       impact="In C++17 noexcept is part of the function type; old callers compiled against non-noexcept signature get a different mangled name."),
    _E("func_noexcept_removed", _C,
       impact="Old callers may rely on noexcept guarantee for optimizations; removing it can cause unexpected std::terminate."),
    _E("func_virtual_added", _B,
       impact="Vtable layout changes; old binaries call wrong virtual function slot, leading to crashes or wrong behavior."),
    _E("func_virtual_removed", _B,
       impact="Vtable entry removed; old binaries that dispatch through the vtable call the wrong slot."),
    _E("var_removed", _B,
       impact="Old binaries reference a global variable that no longer exists; link or load failure."),
    _E("var_added", _C, is_addition=True,
       impact="New variable available; existing binaries are unaffected."),
    _E("var_type_changed", _B,
       impact="Old binaries read/write the variable with wrong size or layout; data corruption or segfault."),

    # ── Type changes ───────────────────────────────────────────────────────
    _E("type_size_changed", _B,
       impact="Old code allocates or copies the type with the old size; heap/stack corruption, out-of-bounds access."),
    _E("type_alignment_changed", _B,
       impact="Misaligned access can cause bus errors on strict architectures or silent data corruption with SIMD."),
    _E("type_field_removed", _B,
       impact="Old code accesses a field that no longer exists at the expected offset; reads garbage or writes out of bounds."),
    _E("type_field_added", _B,
       impact="New field shifts subsequent fields; old code reads wrong offsets for all fields after insertion point."),
    _E("type_field_offset_changed", _B,
       impact="Old code reads/writes fields at stale offsets; silent data corruption."),
    _E("type_field_type_changed", _B,
       impact="Field has different size or representation; old code misinterprets the data."),
    _E("type_base_changed", _B,
       impact="Base class layout change shifts derived member offsets and vtable pointers; this-pointer arithmetic breaks."),
    _E("type_vtable_changed", _B,
       impact="Vtable slot reordering; virtual dispatch calls wrong method."),
    _E("type_added", _C, is_addition=True,
       impact="New type available; existing binaries are unaffected."),
    _E("type_removed", _B,
       impact="Old code references a type that no longer exists; compilation or link failure."),
    _E("type_field_added_compatible", _C, is_addition=True,
       impact="Field appended without changing existing offsets; old code works but won't initialize the new field."),

    # ── Enum changes ───────────────────────────────────────────────────────
    _E("enum_member_removed", _B,
       impact="Old code uses a constant that no longer exists; compile error for source, stale value for binaries."),
    _E("enum_member_added", _C, is_addition=True,
       impact="New enumerator may shift subsequent values in non-fixed enums; switch defaults may miss the new case."),
    _E("enum_member_value_changed", _B,
       impact="Old binaries use stale numeric values; logic comparisons and switch statements silently break."),
    _E("enum_last_member_value_changed", _R,
       impact="Sentinel/MAX value changed; old code using it for array sizes allocates wrong amount."),
    _E("typedef_removed", _B,
       impact="Old code using the typedef name won't compile; binary impact depends on usage."),

    # ── Method qualifier changes ───────────────────────────────────────────
    _E("func_static_changed", _B,
       impact="Static/non-static transition changes calling convention (implicit this pointer); ABI mismatch."),
    _E("func_cv_changed", _B,
       impact="const/volatile on 'this' changes the mangled name; old binaries link to the wrong symbol."),
    _E("func_visibility_changed", _B,
       impact="Symbol hidden from dynamic linking; old binaries can't find it at load time."),
    _E("func_visibility_protected_changed", _C,
       impact="Symbol visibility changed to STV_PROTECTED. The symbol remains exported and "
              "is still resolvable by external consumers. Interposition via LD_PRELOAD no "
              "longer works for calls originating inside the library itself — intentional "
              "by the library author. Existing compiled consumers are unaffected."),

    # ── Virtual changes ────────────────────────────────────────────────────
    _E("func_pure_virtual_added", _B,
       impact="Old subclasses don't implement the pure virtual; instantiation causes linker error or UB."),
    _E("func_virtual_became_pure", _B,
       impact="Concrete virtual became pure; old binaries calling it get unresolved dispatch."),

    # ── Union field changes ────────────────────────────────────────────────
    _E("union_field_added", _C, is_addition=True,
       impact="Union size may grow; old code allocating with old sizeof gets truncated data."),
    _E("union_field_removed", _B,
       impact="Old code accessing removed alternative reads uninitialized memory."),
    _E("union_field_type_changed", _B,
       impact="Old code interprets the union member with wrong type layout."),

    # ── Typedef changes ────────────────────────────────────────────────────
    _E("typedef_base_changed", _B,
       impact="Underlying type changed; old code using the typedef operates on wrong representation."),

    # ── Bitfield changes ───────────────────────────────────────────────────
    _E("field_bitfield_changed", _B,
       impact="Bit-field width or offset changed; old code reads/writes wrong bits."),

    # ── ELF-only (Sprint 2) ───────────────────────────────────────────────
    _E("soname_changed", _C,
       impact="SONAME changed. This is a packaging/policy signal, not a binary ABI break: "
              "the symbol table, types, and calling conventions are unchanged. "
              "Deployment action may be required: update ldconfig symlinks or the "
              "linker flag (-lfoo) in dependent packages. Already-compiled consumers "
              "whose loader resolves the library by full path or DT_RPATH are unaffected."),
    _E("soname_missing", _C,
       impact="Library has no SONAME; package managers and ldconfig cannot track versions."),
    _E("visibility_leak", _C,
       impact="Internal symbols exported without -fvisibility=hidden; namespace pollution risk."),
    _E("needed_added", _C,
       impact="New shared library dependency; may not be available on target systems."),
    _E("needed_removed", _C,
       impact="Dependency removed; should be transparent to consumers."),
    _E("rpath_changed", _C),
    _E("runpath_changed", _C),

    # ── Mach-O specific ───────────────────────────────────────────────────
    _E("compat_version_changed", _B,
       impact="Mach-O compatibility version changed; dylibs linked against old version may fail to load."),

    # ── ELF security / bad practice ────────────────────────────────────────
    _E("executable_stack", _C,
       impact="Library has executable stack (PT_GNU_STACK RWE); NX protection disabled — security risk."),

    # ── ELF symbol visibility drift ────────────────────────────────────────
    _E("elf_visibility_changed", _C,
       impact="ELF symbol visibility changed (e.g. DEFAULT→PROTECTED); symbol still exported but interposition semantics differ."),

    # ── Symbol metadata drift ──────────────────────────────────────────────
    _E("symbol_binding_changed", _C,
       impact="GLOBAL→WEAK binding lets interposers override unexpectedly; old code may get wrong implementation."),
    _E("symbol_binding_strengthened", _C,
       impact="WEAK→GLOBAL binding; safe upgrade, interposition still possible via LD_PRELOAD."),
    _E("symbol_type_changed", _B,
       impact="Symbol type changed (e.g. FUNC→OBJECT); callers using wrong calling convention."),
    _E("symbol_size_changed", _B,
       impact="ELF symbol size changed; copy relocations or memcpy-based consumers get truncated/oversized data."),
    _E("ifunc_introduced", _C,
       impact="IFUNC resolver indirection added; transparent to well-behaved callers."),
    _E("ifunc_removed", _C,
       impact="IFUNC removed; transparent to callers."),
    _E("common_symbol_risk", _C),

    # ── Symbol versioning ──────────────────────────────────────────────────
    _E("symbol_version_defined_removed", _B,
       impact="Defined symbol version removed; old binaries requesting that version get link error."),
    _E("symbol_version_defined_added", _C,
       impact="New symbol version defined; transparent to existing consumers."),
    _E("symbol_version_required_added", _R,
       impact="Requires a newer symbol version than old system provides; may fail to load on older systems."),
    _E("symbol_version_required_added_compat", _C,
       impact="New version requirement added but older than existing max; safe on current systems."),
    _E("symbol_version_required_removed", _C,
       impact="Version requirement dropped; broadens compatibility."),

    # ── DWARF layout (Sprint 3) ───────────────────────────────────────────
    _E("dwarf_info_missing", _C),
    _E("struct_size_changed", _B,
       impact="sizeof(T) changed in debug info; confirms layout break visible at binary level."),
    _E("struct_field_offset_changed", _B,
       impact="Field moved to different offset; old code accesses wrong memory."),
    _E("struct_field_removed", _B,
       impact="Field removed from struct; old code accessing it reads/writes garbage."),
    _E("struct_field_type_changed", _B,
       impact="Field type changed in binary; old code misinterprets the field data."),
    _E("struct_alignment_changed", _B,
       impact="Struct alignment changed; may cause misaligned access in embedded structs."),
    _E("enum_underlying_size_changed", _B,
       impact="Enum underlying type changed (e.g. int→long); affects ABI of functions passing enums by value."),

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

    # ── Sprint 2 — gap detectors ──────────────────────────────────────────
    _E("func_deleted", _B,
       impact="Function marked = delete; old binaries still call it, getting link error or UB."),
    _E("var_became_const", _B,
       impact="Variable moved to read-only section; old code writing to it gets SIGSEGV."),
    _E("var_lost_const", _B,
       impact="Variable no longer const; ODR violations possible if old code inlined the value."),
    _E("type_became_opaque", _B,
       impact="Type became forward-declaration only; old code using sizeof or accessing fields fails."),
    _E("base_class_position_changed", _B),
    _E("base_class_virtual_changed", _B),

    # ── Sprint 7 — Source-level breaks ─────────────────────────────────────
    _E("enum_member_renamed", _A,
       impact="Enumerator name changed but value is the same; source code using old name won't compile.",
       policy_overrides={"sdk_vendor": _C}),
    _E("param_default_value_changed", _C),
    _E("param_default_value_removed", _A,
       policy_overrides={"sdk_vendor": _C}),
    _E("field_renamed", _A,
       impact="Field name changed but offset is the same; source code using old name won't compile.",
       policy_overrides={"sdk_vendor": _C}),
    _E("param_renamed", _A,
       policy_overrides={"sdk_vendor": _C}),

    # ── Field qualifier changes ────────────────────────────────────────────
    _E("field_became_const", _C),
    _E("field_lost_const", _C),
    _E("field_became_volatile", _C),
    _E("field_lost_volatile", _C),
    _E("field_became_mutable", _C),
    _E("field_lost_mutable", _C),

    # ── Pointer level changes ──────────────────────────────────────────────
    _E("param_pointer_level_changed", _B),
    _E("return_pointer_level_changed", _B),

    # ── Access level changes ───────────────────────────────────────────────
    _E("method_access_changed", _A,
       impact="Method access level narrowed (e.g. public→private); old code calling it won't compile.",
       policy_overrides={"sdk_vendor": _C}),
    _E("field_access_changed", _A,
       impact="Field access level narrowed; old code accessing it won't compile.",
       policy_overrides={"sdk_vendor": _C}),

    # ── Anonymous struct/union ─────────────────────────────────────────────
    _E("anon_field_changed", _B),

    # ── ABICC full parity — remaining gaps ─────────────────────────────────
    _E("var_value_changed", _C),
    _E("type_kind_changed", _B),
    _E("source_level_kind_changed", _A,
       policy_overrides={"sdk_vendor": _C}),
    _E("used_reserved_field", _C),
    _E("removed_const_overload", _A,
       impact="Const overload removed; source code calling const version breaks.",
       policy_overrides={"sdk_vendor": _C}),
    _E("param_restrict_changed", _C),
    _E("param_became_va_list", _C),
    _E("param_lost_va_list", _C),
    _E("constant_changed", _A),
    _E("constant_added", _C, is_addition=True),
    _E("constant_removed", _A),
    _E("var_access_changed", _A),
    _E("var_access_widened", _C),

    # ── Inline attribute changes ───────────────────────────────────────────
    _E("func_became_inline", _A),
    _E("func_lost_inline", _C),

    # ── PR #89: ELF fallback ──────────────────────────────────────────────
    _E("func_deleted_elf_fallback", _B),

    # ── Template inner-type analysis ──────────────────────────────────────
    _E("template_param_type_changed", _B),
    _E("template_return_type_changed", _B),

    # ── Version-stamped typedef sentinel ───────────────────────────────────
    _E("typedef_version_sentinel", _C,
       impact="Typedef name encodes a version number (e.g. png_libpng_version_1_6_46) — "
              "this is a compile-time sentinel that changes every release by design; "
              "it is never exported as an ELF symbol and does not affect binary ABI."),

    # ── ELF st_other visibility transitions ────────────────────────────────
    _E("symbol_elf_visibility_changed", _C,
       impact="ELF symbol visibility (st_other) changed (e.g. DEFAULT→PROTECTED). "
              "Symbol is still exported but interposition via LD_PRELOAD may stop working."),

    # ── Symbol rename detection ────────────────────────────────────────────
    _E("symbol_renamed_batch", _B,
       impact="Multiple symbols renamed (e.g. namespace prefix added/removed); "
              "old binaries reference the old names and will get undefined symbol errors at load time."),
    _E("func_likely_renamed", _R,
       impact="Function likely renamed (binary fingerprint match: identical code size and hash, "
              "different symbol name). Old binaries reference the old name and will fail to "
              "resolve at load time. This is a heuristic signal — verify the rename is intentional."),

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
              "link to the wrong symbol or fail to resolve it."),

    # extern "C" ↔ C++ linkage flip
    _E("func_language_linkage_changed", _B,
       impact="Language linkage changed (extern \"C\" ↔ C++); the mangled symbol name "
              "changes, so old binaries reference a symbol that no longer exists under "
              "that name."),

    # Symbol version alias (default version) changed
    _E("symbol_version_alias_changed", _R,
       impact="Default symbol version alias changed (e.g. foo@@VER_1.0 → foo@@VER_2.0). "
              "Old binaries requesting the previous default version may get a link or "
              "load error if the old version alias is not retained."),

    # TLS variable model or size changed
    _E("tls_var_size_changed", _B,
       impact="Exported thread-local (TLS) variable size changed; consumers using copy "
              "relocations or direct TLS access will read/write out of bounds."),

    # ELF visibility: STV_PROTECTED ↔ STV_DEFAULT for data symbols
    _E("protected_visibility_changed", _R,
       impact="ELF symbol visibility changed between DEFAULT and PROTECTED. For data "
              "symbols this can break copy relocations; for functions it changes "
              "interposition semantics. The symbol remains exported."),

    # libstdc++ dual ABI flip diagnostic
    _E("glibcxx_dual_abi_flip_detected", _C,
       impact="Mass symbol churn detected that matches a libstdc++ dual ABI toggle "
              "(_GLIBCXX_USE_CXX11_ABI). Individual removed/added symbols are likely "
              "caused by this single root cause rather than intentional API changes."),

    # Inline namespace move
    _E("inline_namespace_moved", _B,
       impact="Symbols moved to a different inline namespace (e.g. v1:: → v2::); "
              "mangled names change so old binaries fail to resolve the symbols."),

    # vtable/typeinfo symbol identity changed (layout stable)
    _E("vtable_symbol_identity_changed", _R,
       impact="Vtable or typeinfo symbol identity changed (e.g. via visibility or "
              "version-script changes) while class layout is stable. Cross-DSO RTTI "
              "comparison and exception handling may silently fail."),

    # ABI surface explosion diagnostic
    _E("abi_surface_explosion", _C,
       impact="Public ABI surface grew or shrank dramatically (e.g. lost "
              "-fvisibility=hidden). This is a configuration/packaging signal, not "
              "a per-symbol break, but may indicate an unintended visibility regression."),

    # ── ELF symbol-version policy checks ────────────────────────────────────
    _E("symbol_version_node_removed", _B,
       impact="A version node (e.g. LIBFOO_1.0) was entirely removed from the "
              "version script. Applications linked against symbols under that "
              "version node will get unresolved symbol errors at load time."),
    _E("symbol_moved_version_node", _R,
       impact="Symbol moved from one version node to another (e.g. LIBFOO_1.0 → "
              "LIBFOO_2.0). Applications linked against the old version node will "
              "not find this symbol at the expected version. This is typically "
              "intentional during a major release."),
    # TODO(policy): The spec calls for strict_abi to treat this as BREAKING
    # and sdk_vendor as COMPATIBLE_WITH_RISK, but the current policy override
    # mechanism only supports downgrading (not upgrading) verdicts.  Adding
    # per-policy upgrades requires changes to policy_kind_sets() and the
    # integrity assertions in checker_policy.py.  Tracked for v2.0.
    _E("soname_bump_recommended", _C,
       impact="Binary-incompatible changes detected but SONAME was not bumped. "
              "Consumers linked against the current SONAME will encounter runtime "
              "failures. Recommended: bump the SONAME to signal the ABI break."),
    _E("soname_bump_unnecessary", _C,
       impact="SONAME was bumped but no binary-incompatible changes were detected. "
              "This forces all consumers to relink unnecessarily. Consider whether "
              "the bump was intentional."),
    _E("version_script_missing", _C,
       impact="Library exports symbols without a version script. This is a common "
              "oversight that prevents fine-grained symbol versioning and makes "
              "future ABI evolution harder to manage."),

    # ── SYCL Plugin Interface (PI) ────────────────────────────────────────
    _E("sycl_implementation_changed", _B,
       impact="SYCL implementation changed (e.g., DPC++ to AdaptiveCpp); "
              "entirely different runtime ABI, plugin interface, and binary layout. "
              "All SYCL consumers must be rebuilt."),
    _E("sycl_pi_version_changed", _B,
       impact="PI interface version changed; runtime rejects plugins compiled against the old "
              "PI version. All backend plugins must be rebuilt or upgraded."),
    _E("sycl_pi_entrypoint_removed", _B,
       impact="Required PI entry point removed from plugin dispatch table; runtime calls to "
              "this function will crash or return PI_ERROR_UNKNOWN."),
    _E("sycl_pi_entrypoint_added", _C, is_addition=True,
       impact="New PI entry point added to dispatch table; existing plugins are unaffected."),
    _E("sycl_plugin_removed", _B,
       impact="Backend plugin removed from distribution; applications targeting this backend "
              "will fail at runtime with PI_ERROR_DEVICE_NOT_FOUND."),
    _E("sycl_plugin_added", _C, is_addition=True,
       impact="New backend plugin available; broadens hardware support."),
    _E("sycl_plugin_search_path_changed", _R,
       impact="Plugin discovery path changed; plugins may not be found at runtime unless "
              "deployment configuration is updated."),
    _E("sycl_runtime_version_changed", _C,
       impact="SYCL runtime version changed; informational. Actual binary breaks are detected "
              "by symbol/type diff of the runtime library."),
    _E("sycl_backend_driver_req_changed", _R,
       impact="Minimum backend driver version requirement increased; may fail on systems with "
              "older drivers (e.g., Level Zero, OpenCL ICD)."),

    # ── Flexible array member detection (libabigail parity) ──────────────
    _E("flexible_array_member_changed", _C,
       impact="Flexible array member (FAM) at end of struct changed: last field with "
              "zero/unknown array size was added, removed, or changed type. The struct "
              "binary layout is unchanged (FAM has zero static size), but runtime "
              "allocation patterns may differ."),

    # ── DWARF-based = delete detection (P3 gap) ─────────────────────────
    _E("func_deleted_dwarf", _B,
       impact="Function marked as deleted (= delete) detected via DWARF debug info. "
              "The function was previously callable; callers will fail to link."),

    # ── Internal-namespace leak via public API ──────────────────────────
    _E("internal_type_leaks_via_public_api", _B,
       impact="A type in an internal namespace (e.g. ::detail::, ::impl::, ::internal::) "
              "changed and is reachable from a public exported type or symbol "
              "(via inheritance, embedded-by-value field, or template argument). "
              "Although the type is conceptually 'internal', it is part of the "
              "effective public ABI: changes to it propagate into the layout, "
              "vtable, or compiled code of every consumer of the public type. "
              "Common in libraries such as oneDAL that wrap implementation in a "
              "'detail' namespace."),
])
