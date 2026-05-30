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

from .change_registry import REGISTRY as _REGISTRY
from .change_registry import Verdict as Verdict


class ChangeKind(str, Enum):
    # Function / variable changes
    FUNC_REMOVED = "func_removed"  # public symbol removed → BREAKING
    FUNC_REMOVED_ELF_ONLY = "func_removed_elf_only"  # ELF-only symbol removed (visibility cleanup, not hard break)
    FUNC_ADDED = "func_added"  # new public symbol → COMPATIBLE
    FUNC_RETURN_CHANGED = "func_return_changed"  # return type changed → BREAKING
    FUNC_PARAMS_CHANGED = "func_params_changed"  # parameter types changed → BREAKING
    FUNC_NOEXCEPT_ADDED = "func_noexcept_added"  # noexcept added → BREAKING (C++17 P0012R1: noexcept is part of function type)
    FUNC_NOEXCEPT_REMOVED = "func_noexcept_removed"  # noexcept removed → BREAKING (can widen exception spec)
    FUNC_VIRTUAL_ADDED = (
        "func_virtual_added"  # became virtual → vtable change → BREAKING
    )
    FUNC_VIRTUAL_REMOVED = "func_virtual_removed"  # → BREAKING

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

    # ELF security / bad practice
    EXECUTABLE_STACK = "executable_stack"  # PT_GNU_STACK has PF_X — NX protection disabled (bad practice)

    # Symbol metadata drift (ELF .dynsym)
    SYMBOL_BINDING_CHANGED = "symbol_binding_changed"  # GLOBAL→WEAK (breaking)
    SYMBOL_BINDING_STRENGTHENED = (
        "symbol_binding_strengthened"  # WEAK→GLOBAL (compatible)
    )
    SYMBOL_TYPE_CHANGED = "symbol_type_changed"  # FUNC→OBJECT, etc.
    SYMBOL_SIZE_CHANGED = "symbol_size_changed"  # st_size changed
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

    # SYCL Plugin Interface (PI) — ADR-020
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
    # or symbol. This is the oneDAL-style break where users of the public
    # API still observe ABI differences because the public type inherits
    # from / embeds-by-value / uses-as-template-argument the internal type.
    INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API = "internal_type_leaks_via_public_api"

    # ── oneDAL-shaped breaks added in case77–case89 ──────────────────────
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
    INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API = (
        "internal_template_leaks_via_public_api"
    )
    # See examples/case88_cpo_kind_changed/README.md
    CPO_KIND_CHANGED = "cpo_kind_changed"
    OVERLOAD_SET_REROUTED = "overload_set_rerouted"
    MANDATORY_TEMPLATE_PARAM_ADDED = "mandatory_template_param_added"
    UNSPECIFIED_RETURN_NOW_NAMED = "unspecified_return_now_named"

    # ── Build-configuration / probe-harness patterns (PR-C) ──────────────
    # See examples/case97_api_depends_on_consumer_env/README.md
    API_DEPENDS_ON_CONSUMER_ENV = "api_depends_on_consumer_env"
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


class HasKind(Protocol):
    kind: ChangeKind


# Verdict is imported from change_registry (single source of truth).


class Confidence(str, Enum):
    """Evidence confidence level for a comparison result."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


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

    breaking, api_break, compatible, risk = policy_kind_sets(policy)
    kinds = {c.kind for c in changes}
    if kinds & breaking:
        return Verdict.BREAKING
    if kinds & api_break:
        return Verdict.API_BREAK
    # At this point: no BREAKING, no API_BREAK kinds remain.
    # All remaining kinds are in compatible ∪ risk.
    # RISK + BREAKING → already returned BREAKING above; RISK + API_BREAK → API_BREAK above.
    if kinds - compatible - risk == set():
        if kinds & risk:
            return Verdict.COMPATIBLE_WITH_RISK  # binary-compat, deployment risk only
        return Verdict.COMPATIBLE
    # Unclassified change kinds default to BREAKING (fail-safe)
    return Verdict.BREAKING


# ---------------------------------------------------------------------------
# Deprecated aliases — kept for external consumers; will be removed in v2.0
# ---------------------------------------------------------------------------
#: Deprecated: use :data:`Verdict.API_BREAK`
SOURCE_BREAK: Verdict = Verdict.API_BREAK  # deprecated alias

#: Deprecated: use :data:`API_BREAK_KINDS`
SOURCE_BREAK_KINDS = API_BREAK_KINDS  # noqa: E305
