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

"""Central change policy registry and verdict computation.

Classification sets (BREAKING_KINDS, COMPATIBLE_KINDS, etc.) and IMPACT_TEXT
are now DERIVED from the single-declaration registry in ``change_registry.py``.
Adding a new ChangeKind requires only one entry there — no shotgun surgery.

Hierarchy (5-tier):
    BREAKING_KINDS      → category 1: binary ABI incompatibilities
    API_BREAK_KINDS     → category 2a: source-level breaks (recompilation required)
    RISK_KINDS          → category 2b: binary-compatible but deployment risk present
    QUALITY_KINDS       → category 3: problematic behaviors (COMPATIBLE minus additions)
    ADDITION_KINDS      → category 4: new API surface (subset of COMPATIBLE_KINDS)

    COMPATIBLE_KINDS    = ADDITION_KINDS ∪ QUALITY_KINDS

Cross-references:
    abicheck/change_registry.py — single-declaration metadata registry
    examples/ground_truth.json  — expected verdicts per example case
    tests/test_example_autodiscovery.py — reads from ground_truth.json
    tests/test_abi_examples.py  — hardcoded expectations (cases 01-18)
    examples/README.md          — case index table
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .change_registry import REGISTRY as _REGISTRY, Verdict as Verdict


class ChangeKind(str, Enum):
    # Function / variable changes
    FUNC_REMOVED = "func_removed"  # public symbol removed → BREAKING
    FUNC_REMOVED_ELF_ONLY = (
        "func_removed_elf_only"  # exported ELF-only function removed -> binary break
    )
    FUNC_ADDED = "func_added"  # new public symbol → COMPATIBLE
    FUNC_RETURN_CHANGED = "func_return_changed"  # return type changed → BREAKING
    FUNC_PARAMS_CHANGED = "func_params_changed"  # parameter types changed → BREAKING
    FUNC_NOEXCEPT_ADDED = "func_noexcept_added"  # noexcept added → BREAKING (C++17 P0012R1: noexcept is part of function type)
    FUNC_NOEXCEPT_REMOVED = "func_noexcept_removed"  # noexcept removed → COMPATIBLE_WITH_RISK (C++17: part of fn-pointer/template mangling; source risk)
    FUNC_VIRTUAL_ADDED = (
        "func_virtual_added"  # became virtual → vtable change → BREAKING
    )
    FUNC_VIRTUAL_REMOVED = "func_virtual_removed"  # → BREAKING
    VIRTUAL_METHOD_ADDED = (
        # a brand-new virtual *method* added to a class that already exists across
        # versions → grows/relayouts the vtable, breaking derived classes (and the
        # vptr if the class had none). Catches the KDE "add a virtual to a non-leaf
        # class" rule when the vtable array itself is not diff-able (DWARF/symbol-only
        # snapshots), where it would otherwise be mistaken for a compatible func_added.
        "virtual_method_added"  # → BREAKING
    )

    VAR_REMOVED = "var_removed"
    VAR_ADDED = "var_added"
    VAR_TYPE_CHANGED = "var_type_changed"

    # Type changes
    TYPE_SIZE_CHANGED = "type_size_changed"  # struct/class layout change → BREAKING
    TYPE_ALIGNMENT_CHANGED = "type_alignment_changed"  # alignment change → BREAKING
    TYPE_FIELD_REMOVED = "type_field_removed"  # → BREAKING
    TYPE_FIELD_ADDED = "type_field_added"  # if in non-final class, may be BREAKING
    TYPE_FIELD_OFFSET_CHANGED = "type_field_offset_changed"  # → BREAKING
    TYPE_FIELD_TYPE_CHANGED = "type_field_type_changed"  # → BREAKING
    TYPE_BASE_CHANGED = "type_base_changed"  # inheritance change → BREAKING
    TYPE_VTABLE_CHANGED = "type_vtable_changed"  # → BREAKING

    TYPE_ADDED = "type_added"  # new type → COMPATIBLE
    TYPE_REMOVED = "type_removed"  # type removed → BREAKING if used in API
    TYPE_FIELD_ADDED_COMPATIBLE = "type_field_added_compatible"  # appended to standard-layout non-polymorphic type

    # Enum changes
    ENUM_MEMBER_REMOVED = "enum_member_removed"
    ENUM_MEMBER_ADDED = (
        "enum_member_added"  # BREAKING (closed enums / value shift risk)
    )
    ENUM_MEMBER_VALUE_CHANGED = "enum_member_value_changed"
    ENUM_LAST_MEMBER_VALUE_CHANGED = (
        "enum_last_member_value_changed"  # sentinel changed
    )
    TYPEDEF_REMOVED = "typedef_removed"  # placed here for logical grouping

    # Method qualifier changes
    FUNC_STATIC_CHANGED = "func_static_changed"
    FUNC_CV_CHANGED = "func_cv_changed"  # const/volatile on this
    FUNC_VISIBILITY_CHANGED = (
        "func_visibility_changed"  # default→hidden: symbol gone from ABI
    )
    FUNC_VISIBILITY_PROTECTED_CHANGED = "func_visibility_protected_changed"  # default↔protected: interposition semantics changed, symbol still exported

    # Virtual changes
    FUNC_PURE_VIRTUAL_ADDED = "func_pure_virtual_added"
    FUNC_VIRTUAL_BECAME_PURE = "func_virtual_became_pure"

    # Union field changes
    UNION_FIELD_ADDED = "union_field_added"
    UNION_FIELD_REMOVED = "union_field_removed"
    UNION_FIELD_TYPE_CHANGED = "union_field_type_changed"

    # Typedef changes
    TYPEDEF_BASE_CHANGED = "typedef_base_changed"

    # Bitfield changes
    FIELD_BITFIELD_CHANGED = "field_bitfield_changed"

    # ── ELF-only (Sprint 2) ──────────────────────────────────────────────
    # Dynamic section contract
    SONAME_CHANGED = "soname_changed"
    SONAME_MISSING = "soname_missing"  # old library had no SONAME — bad practice
    VISIBILITY_LEAK = "visibility_leak"  # library exports internal symbols without -fvisibility=hidden
    NEEDED_ADDED = "needed_added"  # new DT_NEEDED dep
    NEEDED_REMOVED = "needed_removed"  # dep dropped
    RPATH_CHANGED = "rpath_changed"
    RUNPATH_CHANGED = "runpath_changed"

    # ── Mach-O specific ──────────────────────────────────────────────────
    COMPAT_VERSION_CHANGED = (
        "compat_version_changed"  # LC_ID_DYLIB compat_version changed → BREAKING
    )
    MACHO_CPU_TYPE_CHANGED = (
        "macho_cpu_type_changed"  # Mach-O header CPU type/arch changed → BREAKING
    )

    # ── PE/COFF specific (binary-only, no PDB needed) ────────────────────
    PE_FORWARDER_CHANGED = "pe_forwarder_changed"  # export forwarder target repointed
    PE_MACHINE_CHANGED = "pe_machine_changed"  # PE machine/architecture drift

    # ELF security / bad practice
    EXECUTABLE_STACK = "executable_stack"  # PT_GNU_STACK gains PF_X — NX disabled (regression; gateable)
    EXECUTABLE_STACK_REMOVED = "executable_stack_removed"  # PT_GNU_STACK loses PF_X — hardening improvement (informational)
    # checksec-equivalent hardening regressions (see G12). RISK by default;
    # gateable to break via the shipped security policy.
    RELRO_WEAKENED = "relro_weakened"  # full→partial / →none RELRO
    PIE_DISABLED = "pie_disabled"  # PIE executable → non-PIE
    STACK_CANARY_REMOVED = "stack_canary_removed"  # -fstack-protector dropped
    FORTIFY_SOURCE_WEAKENED = "fortify_source_weakened"  # _FORTIFY_SOURCE dropped
    WRITABLE_EXECUTABLE_SEGMENT = "writable_executable_segment"  # W^X violation introduced

    # Symbol metadata drift (ELF .dynsym)
    SYMBOL_BINDING_CHANGED = "symbol_binding_changed"  # GLOBAL→WEAK (breaking)
    SYMBOL_BINDING_STRENGTHENED = (
        "symbol_binding_strengthened"  # WEAK→GLOBAL (compatible)
    )
    SYMBOL_TYPE_CHANGED = "symbol_type_changed"  # FUNC→OBJECT, etc.
    SYMBOL_SIZE_CHANGED = "symbol_size_changed"  # st_size changed
    # st_size changed on an internal-looking (reserved/underscore-prefixed)
    # exported data symbol; exported data size drift is breaking by default.
    SYMBOL_SIZE_CHANGED_INTERNAL = "symbol_size_changed_internal"
    # st_size changed on a public const string-like object, e.g.
    # extern char const version[]. Old non-PIE executables can still carry copy
    # relocations sized from the old DSO symbol, so this remains breaking.
    SYMBOL_SIZE_CHANGED_CONST_OBJECT = "symbol_size_changed_const_object"
    IFUNC_INTRODUCED = "ifunc_introduced"  # → STT_GNU_IFUNC
    IFUNC_REMOVED = "ifunc_removed"  # STT_GNU_IFUNC →
    COMMON_SYMBOL_RISK = "common_symbol_risk"  # STT_COMMON exported

    # Symbol versioning contract
    SYMBOL_VERSION_DEFINED_REMOVED = "symbol_version_defined_removed"
    SYMBOL_VERSION_DEFINED_ADDED = (
        "symbol_version_defined_added"  # versioning introduced
    )
    SYMBOL_VERSION_REQUIRED_ADDED = (
        "symbol_version_required_added"  # new GLIBC_X — newer than old max (BREAKING)
    )
    SYMBOL_VERSION_REQUIRED_ADDED_COMPAT = "symbol_version_required_added_compat"  # added but older than old max (COMPATIBLE)
    SYMBOL_VERSION_REQUIRED_REMOVED = "symbol_version_required_removed"

    # DWARF layout (Sprint 3)
    DWARF_INFO_MISSING = "dwarf_info_missing"  # new binary stripped of -g
    EVIDENCE_COVERAGE_ASYMMETRIC = "layer_coverage_asymmetric"  # base scanned with evidence the target lacks
    EVIDENCE_REQUIRED_MISSING = "evidence_required_missing"  # policy require_evidence layer absent (ADR-033 D7)
    VERSIONED_SYMBOL_SCHEME_DETECTED = "versioned_symbol_scheme_detected"  # bulk removed↔added differ only by a version token (ICU u_*_NN / GNU symver); advisory
    STRUCT_SIZE_CHANGED = "struct_size_changed"  # sizeof(T) changed
    STRUCT_FIELD_OFFSET_CHANGED = "struct_field_offset_changed"  # field moved
    STRUCT_FIELD_REMOVED = "struct_field_removed"  # field deleted
    STRUCT_FIELD_TYPE_CHANGED = "struct_field_type_changed"  # field type/size changed
    STRUCT_ALIGNMENT_CHANGED = "struct_alignment_changed"  # alignof(T) changed
    ENUM_UNDERLYING_SIZE_CHANGED = "enum_underlying_size_changed"  # int→long

    # DWARF advanced (Sprint 4)
    CALLING_CONVENTION_CHANGED = (
        "calling_convention_changed"  # DW_AT_calling_convention drift
    )
    VALUE_ABI_TRAIT_CHANGED = (
        "value_abi_trait_changed"  # DWARF triviality-based calling conv heuristic
    )
    STRUCT_PACKING_CHANGED = (
        "struct_packing_changed"  # __attribute__((packed)) added/removed
    )
    TYPE_VISIBILITY_CHANGED = (
        "type_visibility_changed"  # typeinfo/vtable visibility changed
    )
    TOOLCHAIN_FLAG_DRIFT = "toolchain_flag_drift"  # -fshort-enums/-fpack-struct drift
    FRAME_REGISTER_CHANGED = (
        "frame_register_changed"  # CFA/frame-pointer convention changed (#117)
    )
    VECTOR_ABI_CHANGED = (
        # Vector-function (SIMD clone) ABI selection drifted between versions:
        # the vectorized call variants of a function resolve to a different
        # ABI. Detected from vector-ABI compiler flags in DW_AT_producer
        # (-mveclibabi= GCC, -fveclib= clang, -vecabi= Intel-style).
        "vector_abi_changed"
    )

    # Sprint 2 — gap detectors
    FUNC_DELETED = "func_deleted"  # = delete added → BREAKING (was callable)
    VAR_BECAME_CONST = "var_became_const"  # non-const → const: writes → SIGSEGV
    VAR_LOST_CONST = "var_lost_const"  # const → non-const: BREAKING (ODR / inlining)
    TYPE_BECAME_OPAQUE = "type_became_opaque"  # complete → forward-decl only → BREAKING
    # `final` class-key specifier transitions (header/castxml only — DWARF and
    # the binary carry no `final` information). Source-level: gaining `final`
    # breaks any consumer that derives from the class.
    TYPE_BECAME_FINAL = "type_became_final"  # gained `final` → derivation no longer compiles → API_BREAK
    TYPE_LOST_FINAL = "type_lost_final"      # lost `final` → devirtualization desync risk on old binaries → COMPATIBLE_WITH_RISK
    BASE_CLASS_POSITION_CHANGED = (
        "base_class_position_changed"  # base reorder → this-ptr offset change
    )
    BASE_CLASS_VIRTUAL_CHANGED = (
        "base_class_virtual_changed"  # base became virtual or non-virtual
    )

    # ── Sprint 7 — Full ABICC parity + beyond ────────────────────────────
    # Source-level breaks (not binary ABI, but API contract)
    ENUM_MEMBER_RENAMED = (
        "enum_member_renamed"  # same value, different name → API_BREAK
    )
    PARAM_DEFAULT_VALUE_CHANGED = "param_default_value_changed"  # default arg changed
    PARAM_DEFAULT_VALUE_REMOVED = (
        "param_default_value_removed"  # default arg removed → API_BREAK
    )
    FIELD_RENAMED = "field_renamed"  # same offset+type, different name
    PARAM_RENAMED = "param_renamed"  # parameter name changed

    # Field qualifier changes
    FIELD_BECAME_CONST = "field_became_const"
    FIELD_LOST_CONST = "field_lost_const"
    FIELD_BECAME_VOLATILE = "field_became_volatile"
    FIELD_LOST_VOLATILE = "field_lost_volatile"
    FIELD_BECAME_MUTABLE = "field_became_mutable"
    FIELD_LOST_MUTABLE = "field_lost_mutable"

    # Pointer level changes
    PARAM_POINTER_LEVEL_CHANGED = "param_pointer_level_changed"  # T* → T** or T** → T*
    RETURN_POINTER_LEVEL_CHANGED = "return_pointer_level_changed"  # return T* → T**

    # Access level changes
    METHOD_ACCESS_CHANGED = "method_access_changed"  # public→protected/private
    FIELD_ACCESS_CHANGED = "field_access_changed"  # public→private field

    # Anonymous struct/union
    ANON_FIELD_CHANGED = "anon_field_changed"  # anon struct/union member changed

    # ── ABICC full parity — remaining gaps ─────────────────────────────────
    # Global data value
    VAR_VALUE_CHANGED = "var_value_changed"  # global data initial value changed

    # Aggregate kind change
    TYPE_KIND_CHANGED = "type_kind_changed"  # union-involving transition (struct→union, union→struct, class→union, union→class)
    SOURCE_LEVEL_KIND_CHANGED = "source_level_kind_changed"  # struct↔class transition (non-breaking, source-only)

    # Reserved field
    USED_RESERVED_FIELD = "used_reserved_field"  # __reserved field put into use

    # Const overload removal
    REMOVED_CONST_OVERLOAD = "removed_const_overload"  # const method overload removed

    # Parameter restrict qualifier
    PARAM_RESTRICT_CHANGED = (
        "param_restrict_changed"  # restrict qualifier added/removed
    )

    # Parameter va_list
    PARAM_BECAME_VA_LIST = "param_became_va_list"  # fixed param → va_list
    PARAM_LOST_VA_LIST = "param_lost_va_list"  # va_list → fixed param

    # Preprocessor constants
    CONSTANT_CHANGED = "constant_changed"  # #define value changed
    CONSTANT_ADDED = "constant_added"  # new #define
    CONSTANT_REMOVED = "constant_removed"  # #define removed

    # Global data access level
    VAR_ACCESS_CHANGED = (
        "var_access_changed"  # public→private/protected variable (narrowing)
    )
    VAR_ACCESS_WIDENED = (
        "var_access_widened"  # private/protected→public variable (widening)
    )

    # ── Inline attribute changes (ABICC issue #125) ─────────────────────────────
    FUNC_BECAME_INLINE = (
        "func_became_inline"  # function became inline — symbol may disappear from DSO
    )
    FUNC_LOST_INLINE = "func_lost_inline"  # function lost inline — now has external linkage (compatible)

    # ── PR #89: ELF fallback for = delete (issue #100) ───────────────────────────
    # Emitted when castxml metadata lacks deleted="1" but the symbol disappears
    # from the ELF .dynsym while the header model still declares the function.
    # This is a best-effort fallback; lower confidence than FUNC_DELETED.
    FUNC_DELETED_ELF_FALLBACK = "func_deleted_elf_fallback"

    # ── PR: Template inner-type deep analysis (issues #38 / #73) ─────────────
    # Emitted when a function param or return type is a template specialization
    # whose inner type argument(s) change, e.g. vector<int> → vector<double>.
    TEMPLATE_PARAM_TYPE_CHANGED = "template_param_type_changed"
    TEMPLATE_RETURN_TYPE_CHANGED = "template_return_type_changed"

    # ── Version-stamped typedef sentinel ────────────────────────────────────
    # Emitted when a typedef whose name encodes a version number
    # (e.g. png_libpng_version_1_6_46) is removed.  These are compile-time
    # sentinels only and are never exported as ELF symbols — NOT an ABI break.
    TYPEDEF_VERSION_SENTINEL = "typedef_version_sentinel"

    # ── ELF st_other visibility transitions ────────────────────────────────────
    SYMBOL_ELF_VISIBILITY_CHANGED = (
        "symbol_elf_visibility_changed"  # DEFAULT→PROTECTED etc.
    )

    # ── Symbol rename detection ────────────────────────────────────────────────
    # Emitted when multiple symbols are removed and corresponding prefixed/suffixed
    # versions are added, indicating a namespace refactoring. Old consumers linked
    # against the unprefixed symbols will get undefined symbol errors.
    SYMBOL_RENAMED_BATCH = "symbol_renamed_batch"
    FUNC_LIKELY_RENAMED = (
        "func_likely_renamed"  # binary fingerprint match: same code, different name
    )

    # ── Symbol origin detection ────────────────────────────────────────────────
    # Emitted when a symbol that changed (removed, type-changed, etc.) is detected
    # as likely originating from a dependency library (libstdc++, libgcc, libc, …)
    # rather than being natively defined by this library.  This is a real ABI fact
    # but the root cause is dependency versioning, not the library's own API.
    # Verdict: COMPATIBLE_WITH_RISK (not BREAKING — direct consumers do not link
    # against these symbols; they resolve through the dependency directly).
    SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED = "symbol_leaked_from_dependency_changed"

    # ── Gap analysis: proposed new checks ──────────────────────────────────
    FUNC_REF_QUAL_CHANGED = "func_ref_qual_changed"  # &/&& ref-qualifier changed
    FUNC_LANGUAGE_LINKAGE_CHANGED = "func_language_linkage_changed"  # extern "C" ↔ C++
    SYMBOL_VERSION_ALIAS_CHANGED = (
        "symbol_version_alias_changed"  # default version alias changed
    )
    TLS_VAR_SIZE_CHANGED = "tls_var_size_changed"  # TLS variable size changed
    PROTECTED_VISIBILITY_CHANGED = (
        "protected_visibility_changed"  # STV_PROTECTED ↔ DEFAULT
    )
    GLIBCXX_DUAL_ABI_FLIP_DETECTED = (
        "glibcxx_dual_abi_flip_detected"  # dual ABI toggle diagnostic
    )
    INLINE_NAMESPACE_MOVED = "inline_namespace_moved"  # inline namespace version change
    VTABLE_SYMBOL_IDENTITY_CHANGED = (
        "vtable_symbol_identity_changed"  # vtable/typeinfo symbol rename
    )
    ABI_SURFACE_EXPLOSION = (
        "abi_surface_explosion"  # dramatic ABI surface growth/shrink
    )

    # ELF symbol-version policy checks
    SYMBOL_VERSION_NODE_REMOVED = "symbol_version_node_removed"
    SYMBOL_MOVED_VERSION_NODE = "symbol_moved_version_node"
    SONAME_BUMP_RECOMMENDED = "soname_bump_recommended"
    SONAME_BUMP_UNNECESSARY = "soname_bump_unnecessary"
    VERSION_SCRIPT_MISSING = "version_script_missing"

    # ── Flexible array member detection (libabigail parity) ──────────────
    FLEXIBLE_ARRAY_MEMBER_CHANGED = "flexible_array_member_changed"

    # ── DWARF-based = delete detection (P3 gap) ─────────────────────────
    FUNC_DELETED_DWARF = "func_deleted_dwarf"  # DW_AT_deleted in DWARF5+, or absent from DWARF but present in headers

    # SYCL Plugin Interface (PI) — ADR-020b
    SYCL_IMPLEMENTATION_CHANGED = "sycl_implementation_changed"
    SYCL_PI_VERSION_CHANGED = "sycl_pi_version_changed"
    SYCL_PI_ENTRYPOINT_REMOVED = "sycl_pi_entrypoint_removed"
    SYCL_PI_ENTRYPOINT_ADDED = "sycl_pi_entrypoint_added"
    SYCL_PLUGIN_REMOVED = "sycl_plugin_removed"
    SYCL_PLUGIN_ADDED = "sycl_plugin_added"
    SYCL_PLUGIN_SEARCH_PATH_CHANGED = "sycl_plugin_search_path_changed"
    SYCL_RUNTIME_VERSION_CHANGED = "sycl_runtime_version_changed"
    SYCL_BACKEND_DRIVER_REQ_CHANGED = "sycl_backend_driver_req_changed"

    # ── Internal-namespace leak via public API ───────────────────────────
    # A type that lives in an "internal" namespace (e.g. ::detail::, ::impl::,
    # ::internal::) has changed and is reachable from a public exported type
    # or symbol. This is the detail-namespace leak break where users of the
    # public API still observe ABI differences because the public type inherits
    # from / embeds-by-value / uses-as-template-argument the internal type.
    INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API = "internal_type_leaks_via_public_api"

    # ── library-family-shaped breaks added in case77–case89 ──────────────────────
    # See examples/case79_missing_template_instantiation/README.md
    INSTANTIATION_MISSING_FROM_BINARY = "instantiation_missing_from_binary"
    # See examples/case81_serialization_tag_reassigned/README.md
    SERIALIZATION_TAG_CHANGED = "serialization_tag_changed"
    # See examples/case82_sycl_overload_set_removed/README.md
    SYCL_OVERLOAD_SET_REMOVED = "sycl_overload_set_removed"
    # See examples/case83_cpu_dispatch_isa_dropped/README.md
    CPU_DISPATCH_ISA_DROPPED = "cpu_dispatch_isa_dropped"
    # See examples/case84_bundle_soname_skew/README.md
    BUNDLE_SONAME_SKEW = "bundle_soname_skew"
    # See examples/case86_tag_struct_renamed/README.md
    TAG_TYPE_RENAMED = "tag_type_renamed"
    # See examples/case87_default_template_arg_changed/README.md
    DEFAULT_TEMPLATE_ARG_CHANGED = "default_template_arg_changed"
    # See examples/case89_inline_accessor_renamed_pimpl_member/README.md
    INLINE_BODY_REFERENCES_RENAMED_MEMBER = "inline_body_references_renamed_member"

    # ── Bundle / multi-library findings (ADR-023) ────────────────────────
    # Reported by the bundle layer in addition to per-library changes.
    # See abicheck/bundle.py.
    BUNDLE_INTRA_DEP_REMOVED = "bundle_intra_dep_removed"
    BUNDLE_INTRA_DEP_SIGNATURE_CHANGED = "bundle_intra_dep_signature_changed"
    BUNDLE_INTRA_TYPE_CHANGED = "bundle_intra_type_changed"
    BUNDLE_PROVIDER_CHANGED = "bundle_provider_changed"
    BUNDLE_MANIFEST_INSTANTIATION_REMOVED = "bundle_manifest_instantiation_removed"
    BUNDLE_MANIFEST_INSTANTIATION_ADDED = "bundle_manifest_instantiation_added"
    BUNDLE_LIBRARY_REMOVED = "bundle_library_removed"
    BUNDLE_LIBRARY_ADDED = "bundle_library_added"
    BUNDLE_INTRA_DEP_VERSION_DRIFT = "bundle_intra_dep_resolved_to_different_version"

    # ── Explicit specifier transitions on constructors / conversion ops ─
    # Source-level contract: an `explicit` specifier added to a previously-
    # implicit converting constructor invalidates user code that depended on
    # implicit conversion (e.g. `Foo f = 42;` or pass-by-value at call site).
    # Removing `explicit` is the dual; existing code keeps compiling, but
    # implicit conversion may now select a different overload and cause
    # behavioral drift. Neither change alters the mangled name.
    CTOR_EXPLICIT_ADDED = "ctor_explicit_added"
    CTOR_EXPLICIT_REMOVED = "ctor_explicit_removed"

    # ── Namespace-shape patterns (oneDPL / header-only follow-up) ────────
    # See examples/case99_experimental_graduated/README.md
    EXPERIMENTAL_GRADUATED = "experimental_graduated"
    # See examples/case100_experimental_removed_without_replacement/README.md
    EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT = (
        "experimental_removed_without_replacement"
    )
    # Example case deferred — detector + unit tests live in PR #247.
    STD_REEXPORT_REMOVED = "std_reexport_removed"
    # Specialisation of INLINE_NAMESPACE_MOVED for header-declared
    # symbols whose qualified name path explicitly carries a versioned
    # inline namespace segment (``::_V1::`` → ``::_V2::``). Fires at the
    # declaration level so it is detectable even when the library ships
    # no .so (header-only / template libraries).
    INLINE_NAMESPACE_VERSION_BUMPED = "inline_namespace_version_bumped"

    # ── Template / overload-set patterns (PR-B follow-up) ────────────────
    # See examples/case85_internal_template_signature_changed/README.md
    INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API = "internal_template_leaks_via_public_api"
    # See examples/case88_cpo_kind_changed/README.md
    CPO_KIND_CHANGED = "cpo_kind_changed"
    OVERLOAD_SET_REROUTED = "overload_set_rerouted"
    # a new overload added to a previously *unique* (non-overloaded) public name.
    # Binary-compatible (old binaries unaffected) but source-risky: taking the
    # function's address (`&f`) becomes ambiguous and overload resolution at
    # existing call sites may silently change. KDE "Binary Compatibility Issues
    # With C++" lists this under changes to avoid. → COMPATIBLE_WITH_RISK.
    OVERLOAD_ADDED = "overload_added"
    MANDATORY_TEMPLATE_PARAM_ADDED = "mandatory_template_param_added"
    UNSPECIFIED_RETURN_NOW_NAMED = "unspecified_return_now_named"

    # ── Build-configuration / probe-harness patterns (PR-C) ──────────────
    # See examples/case97_api_depends_on_consumer_env/README.md
    API_DEPENDS_ON_CONSUMER_ENV = "api_depends_on_consumer_env"
    CONCEPT_TIGHTENED = "concept_tightened"
    CXX_STANDARD_FLOOR_RAISED = "cxx_standard_floor_raised"
    BEHAVIOURAL_DEFAULT_CHANGED = "behavioural_default_changed"

    # Hidden friends (in-class `friend` declarations, typically inline).
    # Inline-defined hidden friends are findable only via ADL on one of
    # their argument types; removing one is a source-level break for any
    # consumer that wrote `a + b` (or similar operator/ADL usage). When
    # the friend was also defined out-of-line, removal additionally fires
    # FUNC_REMOVED at the binary level; the two findings are complementary.
    HIDDEN_FRIEND_REMOVED = "hidden_friend_removed"
    HIDDEN_FRIEND_ADDED = "hidden_friend_added"

    # ── modern-C++ / numerical-library ABI hazards (gap analysis) ───────────
    INTEGER_MODEL_CHANGED = "integer_model_changed"
    ABI_TAG_CHANGED = "abi_tag_changed"
    CHAR8T_MIGRATION = "char8t_migration"
    BIT_INT_WIDTH_CHANGED = "bit_int_width_changed"
    ATOMIC_QUALIFIER_CHANGED = "atomic_qualifier_changed"

    # ── API-surface intelligence anti-patterns (ADR-027 A2 / D2.2) ──────────
    # Graph-shaped findings recognised from the declaration graph rather than a
    # per-symbol diff. The two RISK kinds are single-snapshot anti-patterns
    # (reported by `surface-report`, and at diff time only when newly
    # introduced); the two BREAKING kinds are idiom *transitions* emitted by the
    # A4 pattern-verdict pass when an opacity/handle guarantee callers relied on
    # is lost.
    PUBLIC_API_EXPOSES_STL_BY_VALUE = "public_api_exposes_stl_by_value"
    POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR = "polymorphic_type_non_virtual_dtor"
    OPAQUE_INVARIANT_BROKEN = "opaque_invariant_broken"
    HANDLE_TYPE_CHANGED = "handle_type_changed"

    # ── API-surface metric drift (ADR-027 A1 / D1.2) ────────────────────────
    # Aggregate, informational signals emitted only with --surface-metrics.
    # COMPATIBLE: never breaking on their own; useful for CI dashboards and
    # release notes.
    PUBLIC_SURFACE_GREW = "public_surface_grew"
    PUBLIC_SURFACE_SHRANK = "public_surface_shrank"
    UNDOCUMENTED_EXPORT_RATIO_INCREASED = "undocumented_export_ratio_increased"

    # ── Build-context evidence (ADR-028 L3 / ADR-029 D9) ────────────────────
    # Emitted only by the build-evidence diff over two BuildSourcePacks. These are
    # source/build-context findings, not artifact-backed ABI breaks: per
    # ADR-028 D3 they default to COMPATIBLE (quality) or RISK and never to
    # BREAKING. When a build-context change actually breaks the ABI, the
    # artifact diff (L0/L1/L2) emits the BREAKING finding separately; these
    # kinds explain and localize it.
    BUILD_CONTEXT_CHANGED = "build_context_changed"  # non-ABI build metadata drift → COMPATIBLE (quality)
    ABI_RELEVANT_BUILD_FLAG_CHANGED = "abi_relevant_build_flag_changed"  # ABI-affecting flag changed → RISK
    HEADER_PARSE_CONTEXT_DRIFT = "header_parse_context_drift"  # headers parsed under different context than the build → RISK
    TOOLCHAIN_VERSION_CHANGED = "toolchain_version_changed"  # compiler/stdlib/sysroot changed → RISK
    GENERATED_FILE_DEPENDENCY_UNSTABLE = "generated_file_dependency_unstable"  # generated-file dependency risk → RISK
    LINK_EXPORT_POLICY_CHANGED = "link_export_policy_changed"  # version script / export map / .def changed → RISK

    # ── Runtime-model / build-mode flips (ADR-028 L3 — gap-analysis follow-up) ─
    # Emitted by the build-evidence diff when a runtime-model build flag flips
    # between versions. Like the other L3 kinds these are never BREAKING on their
    # own (ADR-028 D3): the artifact diff proves an actual break; these flag the
    # elevated risk and localize the cause. They default to RISK.
    EXCEPTIONS_MODE_CHANGED = "exceptions_mode_changed"  # -fexceptions ↔ -fno-exceptions flip → RISK
    RTTI_MODE_CHANGED = "rtti_mode_changed"  # -frtti ↔ -fno-rtti flip → RISK
    TLS_MODEL_CHANGED = "tls_model_changed"  # -ftls-model / -fextern-tls-init flip → RISK
    THREADSAFE_STATICS_MODE_CHANGED = "threadsafe_statics_mode_changed"  # -fno-threadsafe-statics flip → RISK
    ENUM_SIZE_FLAG_CHANGED = "enum_size_flag_changed"  # -fshort-enums flip → enum storage size changes → RISK
    STRUCT_PACKING_MODE_CHANGED = "struct_packing_mode_changed"  # -fpack-struct / /Zp flip → member offsets shift → RISK
    LTO_MODE_CHANGED = "lto_mode_changed"  # -flto ↔ no-LTO flip → cross-TU codegen/vtable emission differs → RISK
    CHAR_SIGNEDNESS_CHANGED = "char_signedness_changed"  # -fsigned-char ↔ -funsigned-char flip → plain-char sign flips → RISK
    WHOLE_PROGRAM_VTABLES_MODE_CHANGED = "whole_program_vtables_mode_changed"  # -fwhole-program-vtables flip → vtable/typeinfo elision differs → RISK
    SANITIZER_MODE_CHANGED = "sanitizer_mode_changed"  # -fsanitize= flip → object layout/instrumentation/runtime contract differs → RISK
    FLOAT_ABI_CHANGED = "float_abi_changed"  # -mfloat-abi= flip → float calling convention differs (ARM) → RISK
    STDLIB_DEBUG_MODE_CHANGED = "stdlib_debug_mode_changed"  # _GLIBCXX_DEBUG / _ITERATOR_DEBUG_LEVEL flip → std container layout differs → RISK
    # Struct-return convention (-freg-struct-return / -fpcc-struct-return). Unlike
    # the flag-only RISK kinds above this is artifact-proven from DWARF/ABI facts,
    # so it defaults to BREAKING; the flag-only signal stays as the generic
    # ABI_RELEVANT_BUILD_FLAG_CHANGED (RISK).
    STRUCT_RETURN_CONVENTION_CHANGED = "struct_return_convention_changed"  # aggregate return passing changed → BREAKING

    # ── Source ABI replay evidence (ADR-028 L4 / ADR-030 D6) ────────────────
    # Emitted only by the source-replay diff over two linked source ABI
    # surfaces (source/source_abi.json). These cover source/API facts weakly or
    # not represented in final artifacts: macro constants, default arguments,
    # inline/template bodies, constexpr values, uninstantiated templates. Per
    # ADR-028 D3 / ADR-030 D6 they are source/API findings, never sole authority
    # for a shipped-ABI BREAKING verdict — they default to API_BREAK or RISK.
    PUBLIC_MACRO_VALUE_CHANGED = "public_macro_value_changed"  # public macro constant changed → API_BREAK
    DEFAULT_ARGUMENT_CHANGED = "default_argument_changed"  # default argument value changed → API_BREAK
    INLINE_BODY_CHANGED = "inline_body_changed"  # public inline body changed, no symbol change → RISK
    CONSTEXPR_VALUE_CHANGED = "constexpr_value_changed"  # public constexpr value changed → API_BREAK
    TEMPLATE_BODY_CHANGED = "template_body_changed"  # uninstantiated template body changed → RISK
    UNINSTANTIATED_TEMPLATE_REMOVED = "uninstantiated_template_removed"  # public template removed → API_BREAK
    SOURCE_DECL_BINARY_SYMBOL_MISMATCH = "source_decl_binary_symbol_mismatch"  # decl no longer maps to a symbol → RISK
    SOURCE_BINARY_PROVENANCE_MISMATCH = "source_binary_provenance_mismatch"  # source tree likely does not match the binary → RISK
    ODR_SOURCE_CONFLICT = "odr_source_conflict"  # same type name differs across TUs → RISK
    GENERATED_HEADER_CHANGED = "generated_header_changed"  # generated public header changed → RISK
    PUBLIC_TYPEDEF_TARGET_CHANGED = "public_typedef_target_changed"  # public typedef/alias underlying type changed → API_BREAK
    PUBLIC_MACRO_REMOVED = "public_macro_removed"  # public macro removed from the headers → API_BREAK
    INLINE_FUNCTION_REMOVED = "inline_function_removed"  # public header-only inline function removed (no exported symbol) → API_BREAK
    PUBLIC_TYPEDEF_REMOVED = "public_typedef_removed"  # public typedef/alias removed (no exported symbol) → API_BREAK

    # ── Source graph evidence (ADR-028 L5 / ADR-031 D6) ─────────────────────
    # Emitted only by the source-graph diff over two L5 graph summaries
    # (graph/source_graph_summary.json). Per ADR-031 D6 these *explain and
    # prioritize* impact — they never, on their own, decide or suppress an
    # artifact-proven ABI break; all default to RISK (COMPATIBLE_WITH_RISK).
    PUBLIC_REACHABILITY_CHANGED = "public_reachability_changed"  # decl entered/left the public-API reachability closure → RISK
    SOURCE_TO_BINARY_MAPPING_CHANGED = "source_to_binary_mapping_changed"  # a persisting decl now maps to a different exported symbol → RISK
    GENERATED_HEADER_REACHES_PUBLIC_API = "generated_header_reaches_public_api"  # a generated file entered the public declaration closure → RISK
    CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED = "call_graph_public_entry_reachability_changed"  # impl reachable from an exported entry changed → COMPATIBLE (quality)
    INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT = "include_graph_public_header_drift"  # the include closure of a public header changed → RISK
    BUILD_OPTION_REACHES_PUBLIC_SYMBOL = "build_option_reaches_public_symbol"  # a changed ABI-relevant option reaches a public symbol → RISK
    PUBLIC_API_INTERNAL_DEPENDENCY_ADDED = "public_api_internal_dependency_added"  # a public entry newly reaches an internal (non-public) decl via the L5 graph → RISK
    TARGET_DEPENDENCY_ADDED = "target_dependency_added"  # the library gained an inter-target build/link dependency → RISK
    EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED = "exported_symbol_source_owner_changed"  # an exported symbol's owning source/TU changed (implementation relocated) → RISK

    # ── Cross-source validation (ADR-035 D4 / G19.2) ────────────────────────
    # Emitted by the intra-version cross-source engine (buildsource/crosscheck.py)
    # which diffs ONE merged snapshot's evidence sources against each other
    # (binary exports ↔ header decls ↔ build flags ↔ include graph) — no baseline
    # compare. Per ADR-035 D1/D4 these are "bad ABI hygiene" findings, never
    # BREAKING on their own: they default to RISK or API_BREAK and are advisory
    # (suppressible) until a check earns its FP-rate-gate corpus and is promoted.
    EXPORTED_NOT_PUBLIC = "exported_not_public"  # symbol exported by the binary but declared in no public header → RISK
    PUBLIC_NOT_EXPORTED = "public_not_exported"  # public header declares an export obligation the binary does not provide → RISK
    HEADER_BUILD_CONTEXT_MISMATCH = "header_build_context_mismatch"  # headers parsed without the build's ABI-relevant context → API_BREAK
    PRIVATE_HEADER_LEAK = "private_header_leak"  # a public header pulls in a private/non-installed header → RISK
    ODR_TYPE_VARIANT = "odr_type_variant"  # one type has divergent per-TU layouts (L4 ODR conflict) → API_BREAK
    PUBLIC_TO_INTERNAL_DEPENDENCY = "public_to_internal_dependency"  # public API reaches an internal (non-public) entity via the L5 graph → RISK
    # Single-release hygiene audit (ADR-035 D8). Intra-version "bad ABI hygiene"
    # surfaced from one build (no baseline) by the same cross-source engine.
    UNVERSIONED_EXPORTED_SYMBOL = "unversioned_exported_symbol"  # exported symbol carries no version though the library uses a version script → RISK
    RTTI_FOR_INTERNAL_TYPE = "rtti_for_internal_type"  # typeinfo/vtable exported for a type declared only in a private header → RISK

    # ── Cross-implementation standard-library compatibility (D-stdlib) ───────
    # Emitted by the build-mode diff (diff_stdlib_impl.py) when the two
    # snapshots were produced against *different standard-library
    # implementations* — a third compatibility axis (alongside backward /
    # forward) that the C++ standard does not guarantee. These are RISK, not
    # BREAKING: when an embedded stdlib type's layout actually differs, the
    # artifact/type diff emits the BREAKING size/offset finding separately;
    # these kinds explain and localize the cause without escalating on their
    # own (and stay silent when build-mode evidence is absent).
    STDLIB_IMPLEMENTATION_CHANGED = "stdlib_implementation_changed"  # libstdc++ ↔ libc++ ↔ MSVC STL → RISK
    LIBCPP_ABI_VERSION_CHANGED = "libcpp_abi_version_changed"  # _LIBCPP_ABI_VERSION 1 ↔ 2 → RISK

    # ── Fine-grained class-layout descriptor (layout-closure work) ───────────
    # Emitted by diff_layout.py from the optional layout fields on RecordType
    # (base offsets, vptr offset, dsize/tail-padding, standard-layout /
    # trivially-copyable traits). Each is guarded tri-state: skipped when either
    # side lacks the evidence, so an evidence-tier downgrade never fabricates a
    # finding.
    BASE_CLASS_OFFSET_CHANGED = "base_class_offset_changed"  # base subobject moved → this-ptr/field offsets shift → BREAKING
    VPTR_INTRODUCED = "vptr_introduced"  # first virtual added → vtable pointer prepended → all offsets shift → BREAKING
    TRIVIALLY_COPYABLE_LOST = "trivially_copyable_lost"  # type no longer trivially-copyable → pass-by-value/register ABI changes → BREAKING
    STANDARD_LAYOUT_LOST = "standard_layout_lost"  # type no longer standard-layout → offsetof/C-compat/tail-padding reuse changes → RISK
    TAIL_PADDING_REUSE_CHANGED = "tail_padding_reuse_changed"  # data-size (dsize) changed at stable sizeof → derived tail-padding reuse shifts → RISK
    LAYOUT_UNVERIFIABLE = "layout_unverifiable"  # layout could not be verified at this evidence tier (no debug info) → RISK, non-escalating

    # ── Binary-only (no-DWARF / L0) C++ layout descriptors ───────────────────
    # Emitted by diff_elf_layout.py purely from .dynsym symbol sizes — no debug
    # info, no headers. The Itanium C++ ABI encodes a class's vtable slot count
    # in the size of its `_ZTV` vtable object and its inheritance shape in the
    # size of its `_ZTI` typeinfo object, so a virtual-method or base-class
    # change is observable even when the library ships fully stripped of DWARF.
    VTABLE_SLOT_COUNT_CHANGED = "vtable_slot_count_changed"  # _ZTV size delta → virtual method add/remove/reorder → BREAKING
    RTTI_INHERITANCE_CHANGED = "rtti_inheritance_changed"  # _ZTI size delta → base-class set/shape changed → BREAKING

    # ── CPython extension modules (Cython / pybind11 / C-ext, abi3) ───────────
    # Emitted by diff_python.py for a stable-ABI (abi3 / Py_LIMITED_API)
    # extension module. The compatibility contract for such a module is the set
    # of CPython C-API symbols it IMPORTS from libpython, not its exports (G14).
    PYTHON_STABLE_ABI_VIOLATION = "python_stable_abi_violation"  # abi3 module gained an import outside the stable ABI (e.g. a private _Py* symbol) → won't load on a Limited-API interpreter → RISK
    PYTHON_ABI3_DROPPED = "python_abi3_dropped"  # module was abi3 (loads on all interpreters ≥ its floor) but the new build is version-specific → drops every other interpreter it used to support → RISK
    PYTHON_GIL_ABI_CHANGED = "python_gil_abi_changed"  # extension switched between the regular (GIL) and free-threaded (PEP 703, Py_GIL_DISABLED) CPython ABI → the two builds are not interchangeable, a consumer on the other interpreter can't load it → RISK
    PYTHON_ABI3_FLOOR_RAISED = "python_abi3_floor_raised"  # both builds are abi3 but the new one's declared cpXY-abi3 tag floor is higher (e.g. cp39-abi3 → cp310-abi3) → interpreters in the dropped range can no longer load it → RISK

    # ── G23 Phase A — Linux ELF artifact facts ──────────────────────────────
    # A1: DF_STATIC_TLS drift. A library that adopts the static (initial/local-
    # exec) TLS model can no longer be reliably dlopen()ed. Artifact-provable
    # from the binary, so it does not need an L3 build pack (the flag-level
    # TLS_MODEL_CHANGED stays the explanatory L3 signal).
    STATIC_TLS_INTRODUCED = "static_tls_introduced"  # → RISK (breaks dlopen consumers)
    STATIC_TLS_REMOVED = "static_tls_removed"  # → COMPATIBLE (improvement)

    # A2: .note.gnu.property control-flow-protection drift. Dropping IBT/SHSTK
    # (x86 CET) or BTI/PAC (AArch64) weakens the process-wide guarantee.
    CET_PROTECTION_WEAKENED = "cet_protection_weakened"  # IBT/SHSTK dropped → RISK
    BRANCH_PROTECTION_WEAKENED = "branch_protection_weakened"  # BTI/PAC dropped → RISK
    CET_PROTECTION_IMPROVED = "cet_protection_improved"  # IBT/SHSTK gained → COMPATIBLE
    BRANCH_PROTECTION_IMPROVED = "branch_protection_improved"  # BTI/PAC gained → COMPATIBLE

    # A3: ELF identity / ABI-flags guard. The ELF-side counterpart to
    # PE_MACHINE_CHANGED / MACHO_CPU_TYPE_CHANGED. ELF_ABI_FLAGS_CHANGED makes
    # float-ABI drift artifact-proven (the flag-level FLOAT_ABI_CHANGED stays the
    # explanatory L3 signal).
    ELF_MACHINE_CHANGED = "elf_machine_changed"  # e_machine differs → BREAKING
    ELF_CLASS_CHANGED = "elf_class_changed"  # 32↔64-bit → BREAKING
    ELF_ABI_FLAGS_CHANGED = "elf_abi_flags_changed"  # decoded float-ABI/EABI drift → BREAKING
    ELF_OSABI_CHANGED = "elf_osabi_changed"  # EI_OSABI differs → RISK

    # A4: STB_GNU_UNIQUE binding transitions. Uniqueness is enforced process-wide
    # and inhibits dlclose(); losing it removes an ODR-uniqueness guarantee.
    SYMBOL_BINDING_BECAME_UNIQUE = "symbol_binding_became_unique"  # → RISK
    SYMBOL_BINDING_LOST_UNIQUE = "symbol_binding_lost_unique"  # → RISK

    # ── G23 Phase B1 — Itanium multi-inheritance vtable machinery (L0) ───────
    # Recovered from .dynsym thunk / VTT symbol names + sizes, no DWARF/headers.
    # These catch multi-inheritance / virtual-base breaks that the primary-vtable
    # _ZTV size diff (VTABLE_SLOT_COUNT_CHANGED) cannot see — e.g. a base reorder
    # that shifts this-adjustment thunk offsets without changing the slot count.
    VTABLE_THUNK_OFFSET_CHANGED = "vtable_thunk_offset_changed"  # this-adjustment baked into old vtables now wrong → BREAKING
    VTABLE_THUNK_SET_CHANGED = "vtable_thunk_set_changed"  # a persisting method gained/lost a vtable thunk (secondary-base override) → BREAKING
    VTT_SLOT_COUNT_CHANGED = "vtt_slot_count_changed"  # _ZTT size delta → virtual-base construction scaffolding changed → BREAKING
    # B2: L1 DWARF vtable-group reconstruction. The derived class's own base
    # declaration list is unchanged, but a base's *polymorphism* changed (a base
    # gained/lost virtuals), restructuring which bases own a secondary vtable
    # group — a cross-type effect the per-type field/base diff cannot see.
    SECONDARY_VTABLE_GROUP_CHANGED = "secondary_vtable_group_changed"  # secondary vtable group added/removed/reordered → BREAKING
    # A same-set reorder of virtual bases shifts the virtual-base offset table, so
    # this-pointer adjustments baked into old binaries land on the wrong subobject.
    VIRTUAL_BASE_OFFSET_CHANGED = "virtual_base_offset_changed"  # vbase offset table reordered → BREAKING

    # ── G23 Phase D — ecosystem detectors ───────────────────────────────────
    # D3: an exported symbol whose mangled name embeds an unnamed type — a lambda
    # closure (`Ul…E_`) or an unnamed struct/enum (`Ut…_`). Their mangling is
    # TU- and compiler-ordering-fragile (recompiling can renumber them), so
    # exporting them is an ABI time bomb. Hygiene RISK, reported when newly
    # introduced.
    UNNAMED_TYPE_IN_PUBLIC_ABI = "unnamed_type_in_public_abi"  # → RISK
    # D2: a function's `long double` parameter/return representation changed
    # (ppc64 IEEE128 ↔ IBM double-double, or -mlong-double-64) — same source
    # signature, different FP format. Detected from the Itanium long-double
    # mangling token (e/g/u9__ieee128) on a removed↔added pair, or from the
    # DWARF byte size on a persisting symbol.
    LONG_DOUBLE_ABI_CHANGED = "long_double_abi_changed"  # → BREAKING
    # D1: Linux kernel module ABI (kABI) facts from Module.symvers / genksyms.
    KABI_SYMBOL_REMOVED = "kabi_symbol_removed"  # exported kernel symbol gone → BREAKING
    KABI_CRC_CHANGED = "kabi_crc_changed"  # genksyms CRC changed → modversions reject the module → BREAKING
    KABI_SYMBOL_NAMESPACE_CHANGED = "kabi_symbol_namespace_changed"  # export namespace gained/moved → module needs MODULE_IMPORT_NS → BREAKING
    KABI_EXPORT_TYPE_CHANGED = "kabi_export_type_changed"  # EXPORT_SYMBOL ↔ EXPORT_SYMBOL_GPL → API_BREAK
    KABI_SYMBOL_ADDED = "kabi_symbol_added"  # new exported kernel symbol → COMPATIBLE
    # ── Python-level API of an extension module (G23) ─────────────────────────
    # Emitted by diff_python_api.py from the Python-visible surface recovered
    # from a `.pyi` type stub — the functions/classes/methods/signatures a
    # consumer `import`s. Invisible to the C-ABI/export-table view: two builds
    # can be binary-identical yet break every caller. These are source-level
    # (API_BREAK) or behavioural-risk (RISK) findings, never binary breaks.
    PYTHON_API_FUNCTION_REMOVED = "python_api_function_removed"  # a public top-level function disappeared from the module's Python API → callers importing it break → API_BREAK
    PYTHON_API_FUNCTION_ADDED = "python_api_function_added"  # a new public top-level function → additive, existing callers unaffected → COMPATIBLE
    PYTHON_API_CLASS_REMOVED = "python_api_class_removed"  # a public class disappeared from the module's Python API → callers referencing it break → API_BREAK
    PYTHON_API_CLASS_ADDED = "python_api_class_added"  # a new public class → additive → COMPATIBLE
    PYTHON_API_METHOD_REMOVED = "python_api_method_removed"  # a public method disappeared from a class that still exists → callers of it break → API_BREAK
    PYTHON_API_METHOD_ADDED = "python_api_method_added"  # a new public method on an existing class → additive → COMPATIBLE
    PYTHON_API_PARAMETER_REMOVED = "python_api_parameter_removed"  # a parameter was dropped from a function/method signature → callers passing it hit a TypeError → API_BREAK
    PYTHON_API_PARAMETER_ADDED = "python_api_parameter_added"  # a new *required* (no-default) parameter was added → every existing call now raises a missing-argument TypeError → API_BREAK
    PYTHON_API_PARAMETER_RENAMED = "python_api_parameter_renamed"  # a parameter was renamed → callers passing it by keyword hit an unexpected-keyword TypeError → API_BREAK
    PYTHON_API_DEFAULT_REMOVED = "python_api_default_removed"  # a parameter lost its default value → callers relying on the default now raise a missing-argument TypeError → API_BREAK
    PYTHON_API_PARAMETER_TYPE_CHANGED = "python_api_parameter_type_changed"  # a parameter's type annotation changed → type-checker/behavioural risk, not a hard runtime break → RISK
    PYTHON_API_RETURN_TYPE_CHANGED = "python_api_return_type_changed"  # a function/method's return annotation changed → callers may mishandle the result → RISK
    PYTHON_API_PARAMETER_KIND_CHANGED = "python_api_parameter_kind_changed"  # a parameter's binding changed — positional↔keyword-only, keyword→positional-only, or the positional order/position shifted — so existing call sites bind arguments differently even though the names are unchanged → API_BREAK
    PYTHON_API_CALLABLE_KIND_CHANGED = "python_api_callable_kind_changed"  # a callable's protocol changed — def↔async def (callers must/mustn't await), or method↔property / static↔class↔instance binding — so existing call/access sites break even with an unchanged parameter list → API_BREAK
    PYTHON_API_OVERLOAD_REMOVED = "python_api_overload_removed"  # an @overload signature variant was dropped from an overloaded function/method → typed callers that relied on that call shape lose it → API_BREAK
    PYTHON_API_STUB_INVALID = "python_api_stub_invalid"  # a shipped .pyi stub could not be parsed or exceeded safety limits → API_BREAK

    # ── Toolchain / runtime environment drift (binutils & glibc skew) ────────
    # Artifacts of relinking on a different binutils or building against a
    # different glibc/sysroot rather than a source-level interface change.
    # The per-provider-lib synthesis of SYMBOL_VERSION_REQUIRED_ADDED noise:
    # one headline finding naming the old→new deployment floor (e.g.
    # GLIBC_2.28 → GLIBC_2.34) with the imported symbols that pulled it up.
    RUNTIME_FLOOR_RAISED = "runtime_floor_raised"  # max required version node per provider lib rose → binary no longer loads on older runtimes → RISK
    # Packed relative relocations (DT_RELR, `-z pack-relative-relocs`,
    # binutils ≥ 2.38 default on some distros). A DT_RELR binary requires
    # glibc ≥ 2.36 (or an equivalent loader) — glibc marks this with the
    # synthetic GLIBC_ABI_DT_RELR verneed.
    DT_RELR_INTRODUCED = "dt_relr_introduced"  # → RISK (raises loader floor)
    DT_RELR_REMOVED = "dt_relr_removed"  # → COMPATIBLE (broadens loader compatibility)
    # DT_RPATH ↔ DT_RUNPATH flip (ld --enable-new-dtags default drift):
    # DT_RPATH applies to the whole dependency subtree and ignores
    # LD_LIBRARY_PATH; DT_RUNPATH applies only to direct deps and is
    # overridden by LD_LIBRARY_PATH — same paths, different lookup semantics.
    RPATH_TYPE_CHANGED = "rpath_type_changed"  # → RISK
    # A symbol-hash table style (.hash SysV / .gnu.hash GNU) present in the
    # old binary is gone (ld --hash-style default drift). Loaders/tools that
    # only support the dropped style can no longer resolve symbols.
    HASH_STYLE_REMOVED = "hash_style_removed"  # → RISK
    # time64/LFS ABI flip: time_t/off_t-family public typedefs flipped width
    # together (_TIME_BITS=64 / _FILE_OFFSET_BITS=64, glibc ≥ 2.34 option) —
    # one root cause behind mass parameter/field width churn on 32-bit targets.
    TIME64_ABI_CHANGED = "time64_abi_changed"  # → BREAKING

    # ── Coverage extension: dynamic-loader / import-surface facts ────────────
    IMPORTED_SYMBOL_ADDED = "imported_symbol_added"  # binary gained an undefined (imported) symbol — new obligation on the consumer's link environment → RISK
    IMPORTED_SYMBOL_REMOVED = "imported_symbol_removed"  # binary dropped an undefined (imported) symbol — one fewer external obligation → COMPATIBLE (quality)
    INTERPRETER_CHANGED = "interpreter_changed"  # PT_INTERP program interpreter path changed → RISK
    BIND_NOW_DISABLED = "bind_now_disabled"  # DT_BIND_NOW/DF_BIND_NOW/DF_1_NOW dropped — eager→lazy binding, unresolved symbols surface at call time instead of load time → RISK
    ELF_ENDIANNESS_CHANGED = "elf_endianness_changed"  # EI_DATA byte order flipped (LSB ↔ MSB) → BREAKING
    X86_ISA_BASELINE_RAISED = "x86_isa_baseline_raised"  # GNU_PROPERTY_X86_ISA_1_NEEDED gained a level (e.g. x86-64-v2 → v3) — old CPUs can no longer run the library → RISK
    OS_DEPLOYMENT_FLOOR_RAISED = "os_deployment_floor_raised"  # minimum OS/kernel floor raised (Mach-O minos, PE subsystem version, ELF NT_GNU_ABI_TAG) → RISK
    DYNAMIC_LOADING_FLAGS_CHANGED = "dynamic_loading_flags_changed"  # DF_1_NODELETE / DF_1_NOOPEN / DF_1_ORIGIN toggled — dlopen/dlclose contract changed → RISK
    EXPORTED_OBJECT_ALIGNMENT_REDUCED = "exported_object_alignment_reduced"  # exported data object's address alignment dropped — copy-relocation / aligned-access hazard → RISK
    ELF_INIT_FINI_CHANGED = "elf_init_fini_changed"  # DT_INIT/DT_FINI/DT_INIT_ARRAY/DT_FINI_ARRAY presence changed — load/unload-time code contract changed → RISK
    ALLOCATOR_REPLACEMENT_ADDED = "allocator_replacement_added"  # library newly exports global operator new/delete — hijacks allocation for the whole process → RISK
    ALLOCATOR_REPLACEMENT_REMOVED = "allocator_replacement_removed"  # library stopped exporting global operator new/delete — consumers relying on the replacement get the default allocator → RISK

    # ── Coverage extension: PE/COFF (Windows) ────────────────────────────────
    PE_HARDENING_WEAKENED = "pe_hardening_weakened"  # DllCharacteristics lost NX/ASLR/CFG/HIGH_ENTROPY_VA hardening bits → RISK
    PE_HARDENING_IMPROVED = "pe_hardening_improved"  # DllCharacteristics gained hardening bits → COMPATIBLE (quality)
    LIBRARY_VERSION_DOWNGRADED = "library_version_downgraded"  # embedded library version regressed (PE VS_FIXEDFILEINFO / Mach-O LC_ID_DYLIB current_version) → RISK

    # ── Coverage extension: Mach-O (macOS) ───────────────────────────────────
    MACHO_FILETYPE_CHANGED = "macho_filetype_changed"  # Mach-O filetype changed (e.g. MH_DYLIB → MH_BUNDLE): no longer linkable the same way → BREAKING
    MACHO_LINKAGE_FLAGS_CHANGED = "macho_linkage_flags_changed"  # MH_TWOLEVEL / MH_WEAK_DEFINES / MH_BINDS_TO_WEAK / MH_NO_REEXPORTED_DYLIBS flipped → RISK
    MACHO_REEXPORT_CHANGED = "macho_reexport_changed"  # LC_REEXPORT_DYLIB target repointed — same re-export slot now sourced from a different dylib → RISK

    # ── Coverage extension: language-level contracts ─────────────────────────
    FUNC_VARIADIC_ADDED = "func_variadic_added"  # function gained a C ellipsis (...) — variadic call convention differs (%al on SysV x86-64, stack on AArch64 Darwin) → BREAKING
    FUNC_VARIADIC_REMOVED = "func_variadic_removed"  # function lost its C ellipsis (...) — callers passing extra args break → BREAKING
    FUNC_CONTRACT_ATTRIBUTE_ADDED = "func_contract_attribute_added"  # function gained a semantic contract attribute (nonnull/noreturn/format/alloc_size/malloc/warn_unused_result/...) → RISK
    FUNC_CONTRACT_ATTRIBUTE_REMOVED = "func_contract_attribute_removed"  # function lost a semantic contract attribute callers may rely on → RISK
    VAR_ALIGNMENT_CHANGED = "var_alignment_changed"  # exported variable's declared alignment changed → BREAKING
    FUNC_EXCEPTION_SPEC_CHANGED = "func_exception_spec_changed"  # dynamic exception specification (throw(...)) changed in a way not covered by the noexcept kinds → RISK

    # ── Composition compatibility (Wave A: runtime binding / loader / PE / wchar) ──
    RUNTIME_SYMBOL_PROVIDER_CHANGED = "runtime_symbol_provider_changed"  # a consumer's symbol reference resolves to a different provider DSO across environments → RISK
    RUNTIME_WEAK_RESOLUTION_CHANGED = "runtime_weak_resolution_changed"  # a weak symbol reference flipped between resolved and unresolved across environments → RISK
    NEEDED_ORDER_CHANGED = "needed_order_changed"  # DT_NEEDED entries reordered with the dependency set unchanged — can silently change which DSO wins a non-versioned lookup → RISK
    SYMBOLIC_BINDING_MODE_CHANGED = "symbolic_binding_mode_changed"  # DT_SYMBOLIC/DF_SYMBOLIC toggled — self-references resolve to own definitions before global scope → RISK
    TEXT_RELOCATION_INTRODUCED = "text_relocation_introduced"  # DF_TEXTREL/DT_TEXTREL gained — loader must write into the text segment, defeating W^X/text-segment sharing → RISK
    TEXT_RELOCATION_REMOVED = "text_relocation_removed"  # DF_TEXTREL/DT_TEXTREL dropped — text segment stays read-only/shared again → COMPATIBLE (quality)
    PE_ORDINAL_RETARGETED = "pe_ordinal_retargeted"  # a consumer's ordinal-only PE import now resolves to a different exported function → BREAKING
    PE_IMPORT_LOAD_MODE_CHANGED = "pe_import_load_mode_changed"  # an imported DLL function moved between eager (IAT) and delay-loaded → RISK
    WCHAR_MODEL_CHANGED = "wchar_model_changed"  # -fshort-wchar drift changes wchar_t size/signedness with no symbol-level signal → RISK

    @classmethod
    def _missing_(cls, value: object) -> ChangeKind | None:
        # Back-compat: accept the pre-rename serialized value so reports and
        # policy files written before the evidence→buildsource rename still
        # deserialize. ``evidence_coverage_asymmetric`` was renamed to
        # ``layer_coverage_asymmetric``; the meaning is unchanged.
        if value == "evidence_coverage_asymmetric":
            return cls.EVIDENCE_COVERAGE_ASYMMETRIC
        return None


class HasKind(Protocol):
    kind: ChangeKind


# Verdict is imported from change_registry (single source of truth).


class Confidence(str, Enum):
    """Evidence confidence level for a comparison result."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EvidenceTier(str, Enum):
    """Canonical analysis tier achieved for a comparison.

    Unlike :data:`DiffResult.evidence_tiers` — a list of the *raw* data
    sources that were available (``"elf"``, ``"dwarf"``, ``"header"``,
    ``"pe"``, ``"macho"``) — this is a single, ordered label summarizing
    *how deep* the analysis could go. Consumers should key trust decisions
    off this scalar rather than re-deriving depth from the raw list.

    Ordering (shallow → deep):

    - ``ELF_ONLY`` — symbol-table-only. Binary metadata is present
      (ELF/PE/Mach-O export tables) but there is no DWARF debug info and no
      header/AST surface. Only symbol add/remove and version changes are
      observable; struct layout, enum values, and type changes are not.
    - ``DWARF_AWARE`` — DWARF (or equivalent debug info) is present, enabling
      struct layout, enum, and calling-convention analysis, but no
      header/AST surface is available to cross-check declared API intent.
    - ``HEADER_AWARE`` — header/AST information (functions/types/enums from a
      parsed source surface) is present. This is the richest tier and the
      only one that can reason about declared-but-not-emitted API,
      inline/template changes, and macro contracts.
    """

    ELF_ONLY = "elf_only"
    DWARF_AWARE = "dwarf_aware"
    HEADER_AWARE = "header_aware"

    @property
    def rank(self) -> int:
        """Numeric depth (higher = deeper analysis). Useful for comparisons."""
        return _EVIDENCE_TIER_RANK[self]


_EVIDENCE_TIER_RANK: dict[EvidenceTier, int] = {
    EvidenceTier.ELF_ONLY: 0,
    EvidenceTier.DWARF_AWARE: 1,
    EvidenceTier.HEADER_AWARE: 2,
}


class EvidenceStatus(str, Enum):
    """The epistemic status of a single finding — *how* it was proven, not just
    *what* it is (its ``Verdict``/severity already say that).

    A per-report-format overlay (JSON ``evidence_status`` / SARIF
    ``evidenceStatus``), derived from data the checker already computes rather
    than a new classification pass: mostly a relabeling of the existing
    :class:`Verdict` a finding resolved to, per the ADR-028 D3 authority rule
    (artifact evidence is authoritative; build/source evidence corroborates).

    - ``ARTIFACT_PROVEN`` — the finding resolved to ``BREAKING``: L0/L1/L2
      artifact evidence (or an explicit policy override) confirms a shipped
      ABI break.
    - ``SOURCE_CONTRACT`` — resolved to ``API_BREAK``: a source-level break
      that needs a recompile or a policy decision, not necessarily a shipped
      ABI break.
    - ``CONTEXTUAL_RISK`` — resolved to ``COMPATIBLE_WITH_RISK``: build/source/
      deployment context suggests risk without proving a break.
    - ``CONSUMER_PROVEN`` — not derivable from the verdict alone: set
      explicitly when runtime/``appcompat`` evidence demonstrates a *specific*
      consumer actually depends on what changed (see ``reporter.appcompat_to_json``).
    - ``NOT_CHECKABLE`` — the finding **is** the "missing evidence" signal
      (``ChangeKind.EVIDENCE_REQUIRED_MISSING``, ADR-033 D7), not a break.

    ``COMPATIBLE``/``NO_CHANGE`` findings (additions, clean comparisons) carry
    no status — nothing to explain the epistemic strength of.
    """

    ARTIFACT_PROVEN = "artifact_proven"
    SOURCE_CONTRACT = "source_contract"
    CONTEXTUAL_RISK = "contextual_risk"
    CONSUMER_PROVEN = "consumer_proven"
    NOT_CHECKABLE = "not_checkable"


# ---------------------------------------------------------------------------
# Classification sets — DERIVED from change_registry.py (single source of truth)
# ---------------------------------------------------------------------------
# These sets are computed from the registry entries. To add a new ChangeKind,
# add ONE entry in change_registry.py — these sets update automatically.


def _kinds_for(verdict_val: str) -> set[ChangeKind]:
    """Map registry verdict string values back to ChangeKind enum members."""
    raw = _REGISTRY.kinds_for_verdict(getattr(Verdict, verdict_val))
    return {ChangeKind(v) for v in raw}


BREAKING_KINDS: set[ChangeKind] = _kinds_for("BREAKING")

COMPATIBLE_KINDS: set[ChangeKind] = _kinds_for("COMPATIBLE")

RISK_KINDS: frozenset[ChangeKind] = frozenset(_kinds_for("COMPATIBLE_WITH_RISK"))

API_BREAK_KINDS: set[ChangeKind] = _kinds_for("API_BREAK")

# ---------------------------------------------------------------------------
# Compatible sub-categories: additions vs quality/behavioral issues
# ---------------------------------------------------------------------------

ADDITION_KINDS: frozenset[ChangeKind] = frozenset(
    ChangeKind(v) for v in _REGISTRY.addition_kinds()
)

#: Quality / behavioral issues — COMPATIBLE_KINDS that are NOT additions.
QUALITY_KINDS: frozenset[ChangeKind] = frozenset(COMPATIBLE_KINDS - ADDITION_KINDS)

# ---------------------------------------------------------------------------
# Policy-specific downgrade sets — DERIVED from change_registry policy_overrides
# ---------------------------------------------------------------------------


def _policy_override_kinds(policy: str) -> frozenset[ChangeKind]:
    """Return kinds that have a policy override for the given policy name."""
    return frozenset(ChangeKind(v) for v in _REGISTRY.policy_overrides_for(policy))


# sdk_vendor: source-level-only kinds downgraded API_BREAK → COMPATIBLE.
SDK_VENDOR_COMPAT_KINDS: frozenset[ChangeKind] = _policy_override_kinds("sdk_vendor")

# Deprecated alias kept for external consumers; will be removed in v2.0.
SDK_VENDOR_DOWNGRADED_KINDS: frozenset[ChangeKind] = SDK_VENDOR_COMPAT_KINDS

# plugin_abi: calling-convention kinds downgraded BREAKING → COMPATIBLE.
PLUGIN_ABI_DOWNGRADED_KINDS: frozenset[ChangeKind] = _policy_override_kinds(
    "plugin_abi"
)

# Integrity assertions: catch miscategorisation at import time.
# Use explicit raises (not assert) so these are never stripped by python -O.
# All checks below use ``if not …: raise`` instead of ``assert`` so that
# running under ``python -O`` does not silently disable them.
if not SDK_VENDOR_COMPAT_KINDS <= API_BREAK_KINDS:
    raise AssertionError(
        "SDK_VENDOR_COMPAT_KINDS must be a strict subset of API_BREAK_KINDS; "
        f"offending kinds: {SDK_VENDOR_COMPAT_KINDS - API_BREAK_KINDS}"
    )
if not PLUGIN_ABI_DOWNGRADED_KINDS <= BREAKING_KINDS:
    raise AssertionError(
        "PLUGIN_ABI_DOWNGRADED_KINDS must be a strict subset of BREAKING_KINDS; "
        f"offending kinds: {PLUGIN_ABI_DOWNGRADED_KINDS - BREAKING_KINDS}"
    )
if not ADDITION_KINDS <= COMPATIBLE_KINDS:
    raise AssertionError(
        "ADDITION_KINDS must be a subset of COMPATIBLE_KINDS; "
        f"offending kinds: {ADDITION_KINDS - COMPATIBLE_KINDS}"
    )
if ADDITION_KINDS | QUALITY_KINDS != COMPATIBLE_KINDS:
    raise AssertionError(
        "ADDITION_KINDS | QUALITY_KINDS must equal COMPATIBLE_KINDS; "
        f"missing: {COMPATIBLE_KINDS - (ADDITION_KINDS | QUALITY_KINDS)}, "
        f"extra: {(ADDITION_KINDS | QUALITY_KINDS) - COMPATIBLE_KINDS}"
    )

if not RISK_KINDS.isdisjoint(BREAKING_KINDS):
    raise AssertionError(
        "RISK_KINDS must not overlap with BREAKING_KINDS; "
        f"offending kinds: {RISK_KINDS & BREAKING_KINDS}"
    )
if not RISK_KINDS.isdisjoint(COMPATIBLE_KINDS):
    raise AssertionError(
        "RISK_KINDS must not overlap with COMPATIBLE_KINDS; "
        f"offending kinds: {RISK_KINDS & COMPATIBLE_KINDS}"
    )
if not RISK_KINDS.isdisjoint(API_BREAK_KINDS):
    raise AssertionError(
        "RISK_KINDS must not overlap with API_BREAK_KINDS; "
        f"offending kinds: {RISK_KINDS & API_BREAK_KINDS}"
    )

# Completeness check: every ChangeKind must be classified in exactly one set.
# Unclassified kinds silently default to BREAKING at runtime (fail-safe), but
# this makes the *intent* invisible and risks false negatives if a new kind is
# added but forgotten here.  Use explicit raise (not assert) so this is never
# stripped by python -O.
_ALL_CLASSIFIED: frozenset[ChangeKind] = (
    frozenset(BREAKING_KINDS)
    | frozenset(COMPATIBLE_KINDS)
    | frozenset(API_BREAK_KINDS)
    | RISK_KINDS
)
_UNCLASSIFIED = set(ChangeKind) - _ALL_CLASSIFIED
if _UNCLASSIFIED:
    raise AssertionError(
        "Every ChangeKind must appear in exactly one of BREAKING_KINDS, "
        "COMPATIBLE_KINDS, API_BREAK_KINDS, or RISK_KINDS. "
        f"Unclassified kinds (will default to BREAKING at runtime): {_UNCLASSIFIED}"
    )

# No kind should appear in more than one primary set (BREAKING, COMPATIBLE,
# API_BREAK).  RISK_KINDS disjointness is already checked above.
_BREAKING_COMPAT_OVERLAP = frozenset(BREAKING_KINDS) & frozenset(COMPATIBLE_KINDS)
if _BREAKING_COMPAT_OVERLAP:
    raise AssertionError(
        "BREAKING_KINDS and COMPATIBLE_KINDS must be disjoint; "
        f"offending kinds: {_BREAKING_COMPAT_OVERLAP}"
    )
_BREAKING_API_OVERLAP = frozenset(BREAKING_KINDS) & frozenset(API_BREAK_KINDS)
if _BREAKING_API_OVERLAP:
    raise AssertionError(
        "BREAKING_KINDS and API_BREAK_KINDS must be disjoint; "
        f"offending kinds: {_BREAKING_API_OVERLAP}"
    )
_COMPAT_API_OVERLAP = frozenset(COMPATIBLE_KINDS) & frozenset(API_BREAK_KINDS)
if _COMPAT_API_OVERLAP:
    raise AssertionError(
        "COMPATIBLE_KINDS and API_BREAK_KINDS must be disjoint; "
        f"offending kinds: {_COMPAT_API_OVERLAP}"
    )


@dataclass(frozen=True)
class PolicyEntry:
    default_verdict: Verdict
    severity: str
    doc_slug: str
    impact: str = ""  # human-readable impact explanation


# Impact explanations — DERIVED from change_registry.py
IMPACT_TEXT: dict[ChangeKind, str] = {
    ChangeKind(k): v for k, v in _REGISTRY.impact_text().items()
}


POLICY_REGISTRY: dict[ChangeKind, PolicyEntry] = (
    {
        k: PolicyEntry(Verdict.BREAKING, "error", k.value, IMPACT_TEXT.get(k, ""))
        for k in BREAKING_KINDS
    }
    | {
        k: PolicyEntry(Verdict.API_BREAK, "warning", k.value, IMPACT_TEXT.get(k, ""))
        for k in API_BREAK_KINDS
    }
    | {
        k: PolicyEntry(
            Verdict.COMPATIBLE_WITH_RISK, "warning", k.value, IMPACT_TEXT.get(k, "")
        )
        for k in RISK_KINDS
    }
    | {
        k: PolicyEntry(Verdict.COMPATIBLE, "warning", k.value, IMPACT_TEXT.get(k, ""))
        for k in COMPATIBLE_KINDS
    }
)


def policy_for(kind: ChangeKind) -> PolicyEntry:
    """Get policy metadata for a ChangeKind.

    Unknown kinds are treated as BREAKING by default (fail-safe).
    """
    return POLICY_REGISTRY.get(kind, PolicyEntry(Verdict.BREAKING, "error", kind.value))


def impact_for(kind: ChangeKind) -> str:
    """Return human-readable impact explanation for a ChangeKind, or empty string."""
    return IMPACT_TEXT.get(kind, "")


def policy_registry_markdown() -> str:
    """Build a markdown snippet for docs from the policy registry."""
    lines = [
        "| ChangeKind | Default verdict | Severity | Doc slug |",
        "|---|---|---|---|",
    ]
    for kind in sorted(ChangeKind, key=lambda k: k.value):
        entry = policy_for(kind)
        lines.append(
            f"| `{kind.value}` | `{entry.default_verdict.value}` | "
            f"`{entry.severity}` | `{entry.doc_slug}` |"
        )
    return "\n".join(lines)


VALID_BASE_POLICIES: frozenset[str] = frozenset(
    {"strict_abi", "sdk_vendor", "plugin_abi"}
)
"""Canonical set of valid built-in policy names. Import from here — do not redefine."""


def policy_kind_sets(
    policy: str,
) -> tuple[
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
    frozenset[ChangeKind],
]:
    """Return (breaking, api_break, compatible, risk) kind sets for the given policy name.

    This is the single source of truth for policy → kind-set mapping.
    Used by compute_verdict(), DiffResult properties, and report classification.
    Unknown policy names fall back to strict_abi.
    """
    if policy == "sdk_vendor":
        return (
            frozenset(BREAKING_KINDS),
            frozenset(API_BREAK_KINDS - SDK_VENDOR_COMPAT_KINDS),
            frozenset(COMPATIBLE_KINDS | SDK_VENDOR_COMPAT_KINDS),
            frozenset(RISK_KINDS),
        )
    if policy == "plugin_abi":
        # plugin_abi is for in-process host/plugin contracts.
        # Deployment-floor increases (e.g. new GLIBC requirement) can prevent
        # plugin loading in the host environment and are treated as BREAKING
        # under this policy (not COMPATIBLE_WITH_RISK).
        return (
            frozenset((BREAKING_KINDS - PLUGIN_ABI_DOWNGRADED_KINDS) | RISK_KINDS),
            frozenset(API_BREAK_KINDS),
            frozenset(COMPATIBLE_KINDS | PLUGIN_ABI_DOWNGRADED_KINDS),
            frozenset(),
        )
    return (
        frozenset(BREAKING_KINDS),
        frozenset(API_BREAK_KINDS),
        frozenset(COMPATIBLE_KINDS),
        frozenset(RISK_KINDS),
    )


def effective_category(
    change: HasKind,
    breaking: frozenset[ChangeKind],
    api_break: frozenset[ChangeKind],
    compatible: frozenset[ChangeKind],
    risk: frozenset[ChangeKind],
) -> Verdict:
    """The verdict category a single *change* contributes (ADR-025 D4.1).

    This is the **one** place a finding's category is decided. When the finding
    carries a per-finding ``effective_verdict`` override (set by the A4
    pattern-aware modulation pass), that wins; otherwise the category derives
    from ``change.kind``'s membership in the policy kind sets — exactly today's
    behaviour. Unclassified kinds fail safe to ``BREAKING``.

    Every classification site (``compute_verdict``, the ``DiffResult``
    properties, the reporter, the severity helpers, and the bundle verdict) must
    route through this helper so a demotion is honoured consistently across all
    outputs and both exit-code paths.
    """
    # Require a real Verdict: ``isinstance`` (not ``is not None``) rejects
    # MagicMock test doubles whose attribute access auto-creates a truthy mock,
    # mirroring the ``frozen_namespace_violation`` guard in policy_file.
    override = getattr(change, "effective_verdict", None)
    if isinstance(override, Verdict):
        return override
    kind = change.kind
    if kind in breaking:
        return Verdict.BREAKING
    if kind in api_break:
        return Verdict.API_BREAK
    if kind in risk:
        return Verdict.COMPATIBLE_WITH_RISK
    if kind in compatible:
        return Verdict.COMPATIBLE
    return Verdict.BREAKING  # unclassified → fail-safe


_VERDICT_TO_EVIDENCE_STATUS: dict[Verdict, EvidenceStatus] = {
    Verdict.BREAKING: EvidenceStatus.ARTIFACT_PROVEN,
    Verdict.API_BREAK: EvidenceStatus.SOURCE_CONTRACT,
    Verdict.COMPATIBLE_WITH_RISK: EvidenceStatus.CONTEXTUAL_RISK,
}


def evidence_status_for_change(
    change: HasKind, verdict: Verdict
) -> EvidenceStatus | None:
    """The :class:`EvidenceStatus` label for *change*, given its resolved verdict.

    ``EVIDENCE_REQUIRED_MISSING`` (ADR-033 D7) is a policy-gate finding *about*
    absent evidence, not a proven break — it always reads ``NOT_CHECKABLE``
    regardless of which verdict category it resolves to. Every other kind
    derives its status purely from *verdict* (see
    :data:`_VERDICT_TO_EVIDENCE_STATUS`), so this stays consistent with
    ``effective_category`` by construction — there is no second classification
    pass to drift out of sync.

    ``CONSUMER_PROVEN`` (appcompat/runtime-demonstrated) is never returned
    here: it isn't derivable from a verdict alone, so callers that reclassify
    a finding via consumer evidence (``reporter.appcompat_to_json``) set it
    explicitly instead of calling this function.
    """
    if getattr(change, "kind", None) == ChangeKind.EVIDENCE_REQUIRED_MISSING:
        return EvidenceStatus.NOT_CHECKABLE
    return _VERDICT_TO_EVIDENCE_STATUS.get(verdict)


def compute_verdict(
    changes: Sequence[HasKind], *, policy: str = "strict_abi"
) -> Verdict:
    """Compute verdict from a list of changes, honoring the given policy profile.

    Policy profiles:
    - ``strict_abi`` (default): full BREAKING / API_BREAK sets apply.
    - ``sdk_vendor``: source-level-only kinds (rename, access) downgraded
      from API_BREAK → COMPATIBLE (no warning for SDK consumers).
    - ``plugin_abi``: calling-convention kinds (CALLING_CONVENTION_CHANGED,
      FRAME_REGISTER_CHANGED, VALUE_ABI_TRAIT_CHANGED) downgraded from
      BREAKING → COMPATIBLE. Only valid when plugin and host are always
      rebuilt together from the same toolchain.

    Unknown policy names fall back to ``strict_abi``.
    """
    if not changes:
        return Verdict.NO_CHANGE

    sets = policy_kind_sets(policy)
    # Per-finding effective category (ADR-025 D4.1): a finding's own
    # ``effective_verdict`` override wins over its kind's category; the overall
    # verdict is the worst contributed category. With no overrides this is
    # identical to the historical kind-set intersection.
    verdicts = {effective_category(c, *sets) for c in changes}
    if Verdict.BREAKING in verdicts:
        return Verdict.BREAKING
    if Verdict.API_BREAK in verdicts:
        return Verdict.API_BREAK
    if Verdict.COMPATIBLE_WITH_RISK in verdicts:
        return Verdict.COMPATIBLE_WITH_RISK  # binary-compat, deployment risk only
    return Verdict.COMPATIBLE


# ---------------------------------------------------------------------------
# Deprecated aliases — kept for external consumers; will be removed in v2.0
# ---------------------------------------------------------------------------
#: Deprecated: use :data:`Verdict.API_BREAK`
SOURCE_BREAK: Verdict = Verdict.API_BREAK  # deprecated alias

#: Deprecated: use :data:`API_BREAK_KINDS`
SOURCE_BREAK_KINDS = API_BREAK_KINDS  # noqa: E305
