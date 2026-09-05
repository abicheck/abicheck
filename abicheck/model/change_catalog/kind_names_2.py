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

"""Second of three (2/3) of ChangeKind's (name, value) pairs (ADR-061 D9 / model-vs-policy split).

Split purely by original declaration-order position -- 2 of 3 roughly-equal
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

KIND_NAMES_2: tuple[tuple[str, str, str | None], ...] = (
    (
        "FUNC_BECAME_INLINE",
        "func_became_inline",
        "── Inline attribute changes (ABICC issue #125) ───────────────────────────── -- function became inline — symbol may disappear from DSO",
    ),
    (
        "FUNC_LOST_INLINE",
        "func_lost_inline",
        "function lost inline — low-risk, signature/linkage unchanged (compatible)",
    ),
    (
        "FUNC_DELETED_ELF_FALLBACK",
        "func_deleted_elf_fallback",
        '── PR #89: ELF fallback for = delete (issue #100) ─────────────────────────── -- Emitted when castxml metadata lacks deleted="1" but the symbol disappears -- from the ELF .dynsym while the header model still declares the function. -- This is a best-effort fallback; lower confidence than FUNC_DELETED.',
    ),
    (
        "TEMPLATE_PARAM_TYPE_CHANGED",
        "template_param_type_changed",
        "── PR: Template inner-type deep analysis (issues #38 / #73) ───────────── -- Emitted when a function param or return type is a template specialization -- whose inner type argument(s) change, e.g. vector<int> → vector<double>.",
    ),
    ("TEMPLATE_RETURN_TYPE_CHANGED", "template_return_type_changed", None),
    (
        "TYPEDEF_VERSION_SENTINEL",
        "typedef_version_sentinel",
        "── Version-stamped typedef sentinel ──────────────────────────────────── -- Emitted when a typedef whose name encodes a version number -- (e.g. png_libpng_version_1_6_46) is removed.  These are compile-time -- sentinels only and are never exported as ELF symbols — NOT an ABI break.",
    ),
    (
        "SYMBOL_ELF_VISIBILITY_CHANGED",
        "symbol_elf_visibility_changed",
        "── ELF st_other visibility transitions ──────────────────────────────────── -- DEFAULT→PROTECTED etc.",
    ),
    (
        "SYMBOL_RENAMED_BATCH",
        "symbol_renamed_batch",
        "── Symbol rename detection ──────────────────────────────────────────────── -- Emitted when multiple symbols are removed and corresponding prefixed/suffixed -- versions are added, indicating a namespace refactoring. Old consumers linked -- against the unprefixed symbols will get undefined symbol errors.",
    ),
    (
        "FUNC_LIKELY_RENAMED",
        "func_likely_renamed",
        "binary fingerprint match: same code, different name",
    ),
    (
        "SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED",
        "symbol_leaked_from_dependency_changed",
        "── Symbol origin detection ──────────────────────────────────────────────── -- Emitted when a symbol that changed (removed, type-changed, etc.) is detected -- as likely originating from a dependency library (libstdc++, libgcc, libc, …) -- rather than being natively defined by this library.  This is a real ABI fact -- but the root cause is dependency versioning, not the library's own API. -- Verdict: COMPATIBLE_WITH_RISK (not BREAKING — direct consumers do not link -- against these symbols; they resolve through the dependency directly).",
    ),
    (
        "FUNC_REF_QUAL_CHANGED",
        "func_ref_qual_changed",
        "── Gap analysis: proposed new checks ────────────────────────────────── -- &/&& ref-qualifier changed",
    ),
    (
        "FUNC_LANGUAGE_LINKAGE_CHANGED",
        "func_language_linkage_changed",
        'extern "C" ↔ C++',
    ),
    (
        "SYMBOL_VERSION_ALIAS_CHANGED",
        "symbol_version_alias_changed",
        "default version alias changed",
    ),
    ("TLS_VAR_SIZE_CHANGED", "tls_var_size_changed", "TLS variable size changed"),
    (
        "PROTECTED_VISIBILITY_CHANGED",
        "protected_visibility_changed",
        "STV_PROTECTED ↔ DEFAULT",
    ),
    (
        "GLIBCXX_DUAL_ABI_FLIP_DETECTED",
        "glibcxx_dual_abi_flip_detected",
        "dual ABI toggle diagnostic",
    ),
    (
        "INLINE_NAMESPACE_MOVED",
        "inline_namespace_moved",
        "inline namespace version change",
    ),
    (
        "VTABLE_SYMBOL_IDENTITY_CHANGED",
        "vtable_symbol_identity_changed",
        "vtable/typeinfo symbol rename",
    ),
    (
        "ABI_SURFACE_EXPLOSION",
        "abi_surface_explosion",
        "dramatic ABI surface growth/shrink",
    ),
    (
        "SYMBOL_VERSION_NODE_REMOVED",
        "symbol_version_node_removed",
        "ELF symbol-version policy checks",
    ),
    ("SYMBOL_MOVED_VERSION_NODE", "symbol_moved_version_node", None),
    ("SONAME_BUMP_RECOMMENDED", "soname_bump_recommended", None),
    ("SONAME_BUMP_UNNECESSARY", "soname_bump_unnecessary", None),
    ("VERSION_SCRIPT_MISSING", "version_script_missing", None),
    (
        "FLEXIBLE_ARRAY_MEMBER_CHANGED",
        "flexible_array_member_changed",
        "── Flexible array member detection (libabigail parity) ──────────────",
    ),
    (
        "FUNC_DELETED_DWARF",
        "func_deleted_dwarf",
        "── DWARF-based = delete detection (P3 gap) ───────────────────────── -- DW_AT_deleted in DWARF5+, or absent from DWARF but present in headers",
    ),
    (
        "SYCL_IMPLEMENTATION_CHANGED",
        "sycl_implementation_changed",
        "SYCL Plugin Interface (PI) — ADR-020b",
    ),
    ("SYCL_PI_VERSION_CHANGED", "sycl_pi_version_changed", None),
    ("SYCL_PI_ENTRYPOINT_REMOVED", "sycl_pi_entrypoint_removed", None),
    ("SYCL_PI_ENTRYPOINT_ADDED", "sycl_pi_entrypoint_added", None),
    ("SYCL_PLUGIN_REMOVED", "sycl_plugin_removed", None),
    ("SYCL_PLUGIN_ADDED", "sycl_plugin_added", None),
    ("SYCL_PLUGIN_SEARCH_PATH_CHANGED", "sycl_plugin_search_path_changed", None),
    ("SYCL_RUNTIME_VERSION_CHANGED", "sycl_runtime_version_changed", None),
    ("SYCL_BACKEND_DRIVER_REQ_CHANGED", "sycl_backend_driver_req_changed", None),
    (
        "INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API",
        "internal_type_leaks_via_public_api",
        '── Internal-namespace leak via public API ─────────────────────────── -- A type that lives in an "internal" namespace (e.g. ::detail::, ::impl::, -- ::internal::) has changed and is reachable from a public exported type -- or symbol. This is the detail-namespace leak break where users of the -- public API still observe ABI differences because the public type inherits -- from / embeds-by-value / uses-as-template-argument the internal type.',
    ),
    (
        "INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API",
        "internal_symbol_required_by_public_api",
        "ADR-044 P1 items 1-2: the call-graph analogue of the leak above. An -- already artifact-proven BREAKING change (e.g. func_removed — never -- API_BREAK_KINDS, most of which have no removed linker symbol at all, -- e.g. inline_function_removed) on an internal-namespaced decl is -- called/referenced from a public entry -- point over a DECL_CALLS_DECL/DECL_REFERENCES_DECL edge in the optional -- L5 source graph (--sources/--build-info/--header-graph) — the exact -- oneDAL dispatcher shape this ADR's P0 slice explicitly left open (no -- layout/type-graph evidence exists for a pure call, so -- INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API's walk cannot see it). Per the -- authority rule (ADR-028 D3/ADR-041), this graph edge only explains and -- correlates an already artifact-proven break; it never manufactures one.",
    ),
    (
        "INSTANTIATION_MISSING_FROM_BINARY",
        "instantiation_missing_from_binary",
        "── library-family-shaped breaks added in case77–case89 ────────────────────── -- See examples/case79_missing_template_instantiation/README.md",
    ),
    (
        "SERIALIZATION_TAG_CHANGED",
        "serialization_tag_changed",
        "See examples/case81_serialization_tag_reassigned/README.md",
    ),
    (
        "SYCL_OVERLOAD_SET_REMOVED",
        "sycl_overload_set_removed",
        "See examples/case82_sycl_overload_set_removed/README.md",
    ),
    (
        "CPU_DISPATCH_ISA_DROPPED",
        "cpu_dispatch_isa_dropped",
        "See examples/case83_cpu_dispatch_isa_dropped/README.md",
    ),
    (
        "BUNDLE_SONAME_SKEW",
        "bundle_soname_skew",
        "See examples/case84_bundle_soname_skew/README.md",
    ),
    (
        "TAG_TYPE_RENAMED",
        "tag_type_renamed",
        "See examples/case86_tag_struct_renamed/README.md",
    ),
    (
        "DEFAULT_TEMPLATE_ARG_CHANGED",
        "default_template_arg_changed",
        "See examples/case87_default_template_arg_changed/README.md",
    ),
    (
        "INLINE_BODY_REFERENCES_RENAMED_MEMBER",
        "inline_body_references_renamed_member",
        "See examples/case89_inline_accessor_renamed_pimpl_member/README.md",
    ),
    (
        "BUNDLE_INTRA_DEP_REMOVED",
        "bundle_intra_dep_removed",
        "── Bundle / multi-library findings (ADR-023) ──────────────────────── -- Reported by the bundle layer in addition to per-library changes. -- See abicheck/bundle.py.",
    ),
    ("BUNDLE_INTRA_DEP_SIGNATURE_CHANGED", "bundle_intra_dep_signature_changed", None),
    ("BUNDLE_INTRA_TYPE_CHANGED", "bundle_intra_type_changed", None),
    ("BUNDLE_PROVIDER_CHANGED", "bundle_provider_changed", None),
    (
        "BUNDLE_MANIFEST_INSTANTIATION_REMOVED",
        "bundle_manifest_instantiation_removed",
        None,
    ),
    (
        "BUNDLE_MANIFEST_INSTANTIATION_ADDED",
        "bundle_manifest_instantiation_added",
        None,
    ),
    ("BUNDLE_LIBRARY_REMOVED", "bundle_library_removed", None),
    ("BUNDLE_LIBRARY_ADDED", "bundle_library_added", None),
    (
        "BUNDLE_INTRA_DEP_VERSION_DRIFT",
        "bundle_intra_dep_resolved_to_different_version",
        None,
    ),
    (
        "BUNDLE_UNRESOLVED_INTRA_DEPENDENCY",
        "bundle_unresolved_intra_dependency",
        "ADR-056: audit-scoped (no old side) bundle finding, scan --artifact-set. -- Deliberately distinct from BUNDLE_INTRA_DEP_REMOVED, which implies a -- diff-confirmed removal; this fires from a single-side resolution graph.",
    ),
    (
        "BUNDLE_VARIANT_COVERAGE_REGRESSED",
        "bundle_variant_coverage_regressed",
        "G38 Phase 3 (docs/contribute/plans/g38-bundle-facts-model-and- -- multibuild-comparability.md): a build variant (e.g. the CPU-only build -- of a bundle that also ships an ONEDAL_DATA_PARALLEL/DPC build) present -- in the old release's variant set has no matching variant in the new -- release. RISK, not BREAKING: the variant may simply have been dropped -- from the release intentionally, but a consumer pinned to it needs to -- see the coverage gap. See abicheck/bundle_multibuild.py.",
    ),
    (
        "BUNDLE_INTRA_DEP_SIGNATURE_UNVERIFIED",
        "bundle_intra_dep_signature_unverified",
        "G38 Phase 4: a sibling library's import resolves by name to a -- provider's export (the same C-linkage match BUNDLE_INTRA_DEP_ -- SIGNATURE_CHANGED uses), but neither side has DWARF/header evidence -- for that exact symbol, so agreement can be neither confirmed nor -- denied. RISK, distinct from both \"no change\" (evidence agrees) and -- the confirmed BREAKING BUNDLE_INTRA_DEP_SIGNATURE_CHANGED (evidence -- disagrees). See abicheck/bundle_signature_evidence.py.",
    ),
    (
        "BUNDLE_DUPLICATE_PROVIDER",
        "bundle_duplicate_provider",
        "PR H (CLI cleanup phase two, ADR-056): the same default-visibility -- symbol name is exported by 2+ libraries in one --artifact-set audit. -- Which library a consumer resolves against then depends on load order -- / symbol interposition, not a declared contract. RISK -- an audit has -- no old side to say whether this is new or long-standing. Linker- -- synthesized per-object boilerplate (_edata/_end/...) is excluded. See -- abicheck/bundle_detectors.py:_detect_duplicate_providers.",
    ),
    (
        "BUNDLE_MANIFEST_ENTRY_UNSATISFIED",
        "bundle_manifest_entry_unsatisfied",
        "PR H (CLI cleanup phase two, ADR-056): the audit-mode (no old side) -- sibling of BUNDLE_MANIFEST_INSTANTIATION_REMOVED -- an opt-in -- scan --artifact-set --manifest ownership promise (missing entirely, or -- matched but provided by a library other than the one the manifest -- names) is unsatisfied by this one declared set. RISK, not BREAKING: -- there is no diff to confirm a regression, only that the promise does -- not hold right now. See abicheck/bundle_detectors.py:_detect_manifest_ownership.",
    ),
    (
        "CTOR_EXPLICIT_ADDED",
        "ctor_explicit_added",
        "── Explicit specifier transitions on constructors / conversion ops ─ -- Source-level contract: an `explicit` specifier added to a previously- -- implicit converting constructor invalidates user code that depended on -- implicit conversion (e.g. `Foo f = 42;` or pass-by-value at call site). -- Removing `explicit` is the dual; existing code keeps compiling, but -- implicit conversion may now select a different overload and cause -- behavioral drift. Neither change alters the mangled name.",
    ),
    ("CTOR_EXPLICIT_REMOVED", "ctor_explicit_removed", None),
    (
        "CTOR_OVERLOAD_AMBIGUITY_RISK",
        "ctor_overload_ambiguity_risk",
        "A class gained a 2nd+ non-explicit single-argument (converting) -- constructor. This cannot be proven a break from a snapshot alone (that -- needs the consumer's actual call-site context — see -- examples/case111_enumerable_thread_specific_lambda_ambiguity), so it is -- a best-effort RISK heuristic, not a certain API_BREAK: a call site with -- an argument type convertible to more than one of the class's converting -- constructors becomes ambiguous and stops compiling, or silently resolves -- to a different constructor than before. → RISK",
    ),
    (
        "EXPERIMENTAL_GRADUATED",
        "experimental_graduated",
        "── Namespace-shape patterns (oneDPL / header-only follow-up) ──────── -- See examples/case99_experimental_graduated/README.md",
    ),
    (
        "EXPERIMENTAL_REMOVED_WITHOUT_REPLACEMENT",
        "experimental_removed_without_replacement",
        "See examples/case100_experimental_removed_without_replacement/README.md",
    ),
    (
        "STD_REEXPORT_REMOVED",
        "std_reexport_removed",
        "Example case deferred — detector + unit tests live in PR #247.",
    ),
    (
        "INLINE_NAMESPACE_VERSION_BUMPED",
        "inline_namespace_version_bumped",
        "Specialisation of INLINE_NAMESPACE_MOVED for header-declared -- symbols whose qualified name path explicitly carries a versioned -- inline namespace segment (``::_V1::`` → ``::_V2::``). Fires at the -- declaration level so it is detectable even when the library ships -- no .so (header-only / template libraries).",
    ),
    (
        "INTERNAL_TEMPLATE_LEAKS_VIA_PUBLIC_API",
        "internal_template_leaks_via_public_api",
        "── Template / overload-set patterns (PR-B follow-up) ──────────────── -- See examples/case85_internal_template_signature_changed/README.md",
    ),
    (
        "CPO_KIND_CHANGED",
        "cpo_kind_changed",
        "See examples/case88_cpo_kind_changed/README.md",
    ),
    ("OVERLOAD_SET_REROUTED", "overload_set_rerouted", None),
    (
        "OVERLOAD_ADDED",
        "overload_added",
        'a new overload added to a previously *unique* (non-overloaded) public name. -- Binary-compatible (old binaries unaffected) but source-risky: taking the -- function\'s address (`&f`) becomes ambiguous and overload resolution at -- existing call sites may silently change. KDE "Binary Compatibility Issues -- With C++" lists this under changes to avoid. → COMPATIBLE_WITH_RISK.',
    ),
    ("MANDATORY_TEMPLATE_PARAM_ADDED", "mandatory_template_param_added", None),
    ("UNSPECIFIED_RETURN_NOW_NAMED", "unspecified_return_now_named", None),
    (
        "API_DEPENDS_ON_CONSUMER_ENV",
        "api_depends_on_consumer_env",
        "── Build-configuration / probe-harness patterns (PR-C) ────────────── -- See examples/case97_api_depends_on_consumer_env/README.md",
    ),
    ("CONCEPT_TIGHTENED", "concept_tightened", None),
    ("CXX_STANDARD_FLOOR_RAISED", "cxx_standard_floor_raised", None),
    ("BEHAVIOURAL_DEFAULT_CHANGED", "behavioural_default_changed", None),
    (
        "HIDDEN_FRIEND_REMOVED",
        "hidden_friend_removed",
        "Hidden friends (in-class `friend` declarations, typically inline). -- Inline-defined hidden friends are findable only via ADL on one of -- their argument types; removing one is a source-level break for any -- consumer that wrote `a + b` (or similar operator/ADL usage). When -- the friend was also defined out-of-line, removal additionally fires -- FUNC_REMOVED at the binary level; the two findings are complementary.",
    ),
    ("HIDDEN_FRIEND_ADDED", "hidden_friend_added", None),
    (
        "INTEGER_MODEL_CHANGED",
        "integer_model_changed",
        "── modern-C++ / numerical-library ABI hazards (gap analysis) ───────────",
    ),
    ("ABI_TAG_CHANGED", "abi_tag_changed", None),
    ("CHAR8T_MIGRATION", "char8t_migration", None),
    ("BIT_INT_WIDTH_CHANGED", "bit_int_width_changed", None),
    ("ATOMIC_QUALIFIER_CHANGED", "atomic_qualifier_changed", None),
    (
        "PUBLIC_API_EXPOSES_STL_BY_VALUE",
        "public_api_exposes_stl_by_value",
        "── API-surface intelligence anti-patterns (ADR-027 A2 / D2.2) ────────── -- Graph-shaped findings recognised from the declaration graph rather than a -- per-symbol diff. The two RISK kinds are single-snapshot anti-patterns -- (reported by `surface-report`, and at diff time only when newly -- introduced); the two BREAKING kinds are idiom *transitions* emitted by the -- A4 pattern-verdict pass when an opacity/handle guarantee callers relied on -- is lost.",
    ),
    ("POLYMORPHIC_TYPE_NON_VIRTUAL_DTOR", "polymorphic_type_non_virtual_dtor", None),
    ("OPAQUE_INVARIANT_BROKEN", "opaque_invariant_broken", None),
    ("HANDLE_TYPE_CHANGED", "handle_type_changed", None),
    (
        "PUBLIC_SURFACE_GREW",
        "public_surface_grew",
        "── API-surface metric drift (ADR-027 A1 / D1.2) ──────────────────────── -- Aggregate, informational signals emitted only with --surface-metrics. -- COMPATIBLE: never breaking on their own; useful for CI dashboards and -- release notes.",
    ),
    ("PUBLIC_SURFACE_SHRANK", "public_surface_shrank", None),
    (
        "UNDOCUMENTED_EXPORT_RATIO_INCREASED",
        "undocumented_export_ratio_increased",
        None,
    ),
    (
        "BUILD_CONTEXT_CHANGED",
        "build_context_changed",
        "── Build-context evidence (ADR-028 L3 / ADR-029 D9) ──────────────────── -- Emitted only by the build-evidence diff over two BuildSourcePacks. These are -- source/build-context findings, not artifact-backed ABI breaks: per -- ADR-028 D3 they default to COMPATIBLE (quality) or RISK and never to -- BREAKING. When a build-context change actually breaks the ABI, the -- artifact diff (L0/L1/L2) emits the BREAKING finding separately; these -- kinds explain and localize it. -- non-ABI build metadata drift → COMPATIBLE (quality)",
    ),
    (
        "ABI_RELEVANT_BUILD_FLAG_CHANGED",
        "abi_relevant_build_flag_changed",
        "ABI-affecting flag changed → RISK",
    ),
    (
        "HEADER_PARSE_CONTEXT_DRIFT",
        "header_parse_context_drift",
        "headers parsed under different context than the build → RISK",
    ),
    (
        "TOOLCHAIN_VERSION_CHANGED",
        "toolchain_version_changed",
        "compiler/stdlib/sysroot changed → RISK",
    ),
    (
        "GENERATED_FILE_DEPENDENCY_UNSTABLE",
        "generated_file_dependency_unstable",
        "generated-file dependency risk → RISK",
    ),
    (
        "LINK_EXPORT_POLICY_CHANGED",
        "link_export_policy_changed",
        "version script / export map / .def changed → RISK",
    ),
    (
        "EXCEPTIONS_MODE_CHANGED",
        "exceptions_mode_changed",
        "── Runtime-model / build-mode flips (ADR-028 L3 — gap-analysis follow-up) ─ -- Emitted by the build-evidence diff when a runtime-model build flag flips -- between versions. Like the other L3 kinds these are never BREAKING on their -- own (ADR-028 D3): the artifact diff proves an actual break; these flag the -- elevated risk and localize the cause. They default to RISK. -- -fexceptions ↔ -fno-exceptions flip → RISK",
    ),
    ("RTTI_MODE_CHANGED", "rtti_mode_changed", "-frtti ↔ -fno-rtti flip → RISK"),
    (
        "TLS_MODEL_CHANGED",
        "tls_model_changed",
        "-ftls-model / -fextern-tls-init flip → RISK",
    ),
    (
        "THREADSAFE_STATICS_MODE_CHANGED",
        "threadsafe_statics_mode_changed",
        "-fno-threadsafe-statics flip → RISK",
    ),
    (
        "ENUM_SIZE_FLAG_CHANGED",
        "enum_size_flag_changed",
        "-fshort-enums flip → enum storage size changes → RISK",
    ),
    (
        "STRUCT_PACKING_MODE_CHANGED",
        "struct_packing_mode_changed",
        "-fpack-struct / /Zp flip → member offsets shift → RISK",
    ),
    (
        "LTO_MODE_CHANGED",
        "lto_mode_changed",
        "-flto ↔ no-LTO flip → cross-TU codegen/vtable emission differs → RISK",
    ),
    (
        "CHAR_SIGNEDNESS_CHANGED",
        "char_signedness_changed",
        "-fsigned-char ↔ -funsigned-char flip → plain-char sign flips → RISK",
    ),
    (
        "WHOLE_PROGRAM_VTABLES_MODE_CHANGED",
        "whole_program_vtables_mode_changed",
        "-fwhole-program-vtables flip → vtable/typeinfo elision differs → RISK",
    ),
    (
        "SANITIZER_MODE_CHANGED",
        "sanitizer_mode_changed",
        "-fsanitize= flip → object layout/instrumentation/runtime contract differs → RISK",
    ),
    (
        "FLOAT_ABI_CHANGED",
        "float_abi_changed",
        "-mfloat-abi= flip → float calling convention differs (ARM) → RISK",
    ),
    (
        "STDLIB_DEBUG_MODE_CHANGED",
        "stdlib_debug_mode_changed",
        "_GLIBCXX_DEBUG / _ITERATOR_DEBUG_LEVEL flip → std container layout differs → RISK",
    ),
    (
        "STRUCT_RETURN_CONVENTION_CHANGED",
        "struct_return_convention_changed",
        "Struct-return convention (-freg-struct-return / -fpcc-struct-return). Unlike -- the flag-only RISK kinds above this is artifact-proven from DWARF/ABI facts, -- so it defaults to BREAKING; the flag-only signal stays as the generic -- ABI_RELEVANT_BUILD_FLAG_CHANGED (RISK). -- aggregate return passing changed → BREAKING",
    ),
    (
        "PUBLIC_MACRO_VALUE_CHANGED",
        "public_macro_value_changed",
        "── Source ABI replay evidence (ADR-028 L4 / ADR-030 D6) ──────────────── -- Emitted only by the source-replay diff over two linked source ABI -- surfaces (source/source_abi.json). These cover source/API facts weakly or -- not represented in final artifacts: macro constants, default arguments, -- inline/template bodies, constexpr values, uninstantiated templates. Per -- ADR-028 D3 / ADR-030 D6 they are source/API findings, never sole authority -- for a shipped-ABI BREAKING verdict — they default to API_BREAK or RISK. -- public macro constant changed → API_BREAK",
    ),
    (
        "DEFAULT_ARGUMENT_CHANGED",
        "default_argument_changed",
        "default argument value changed → API_BREAK",
    ),
    (
        "INLINE_BODY_CHANGED",
        "inline_body_changed",
        "public inline body changed, no symbol change → RISK",
    ),
    (
        "CONSTEXPR_VALUE_CHANGED",
        "constexpr_value_changed",
        "public constexpr value changed → API_BREAK",
    ),
    (
        "TEMPLATE_BODY_CHANGED",
        "template_body_changed",
        "uninstantiated template body changed → RISK",
    ),
    (
        "UNINSTANTIATED_TEMPLATE_REMOVED",
        "uninstantiated_template_removed",
        "public template removed → API_BREAK",
    ),
    (
        "SOURCE_DECL_BINARY_SYMBOL_MISMATCH",
        "source_decl_binary_symbol_mismatch",
        "decl no longer maps to a symbol → RISK",
    ),
    (
        "SOURCE_BINARY_PROVENANCE_MISMATCH",
        "source_binary_provenance_mismatch",
        "source tree likely does not match the binary → RISK",
    ),
    (
        "ODR_SOURCE_CONFLICT",
        "odr_source_conflict",
        "same type name differs across TUs → RISK",
    ),
    (
        "GENERATED_HEADER_CHANGED",
        "generated_header_changed",
        "generated public header changed → RISK",
    ),
    (
        "PUBLIC_TYPEDEF_TARGET_CHANGED",
        "public_typedef_target_changed",
        "public typedef/alias underlying type changed → API_BREAK",
    ),
    (
        "PUBLIC_MACRO_REMOVED",
        "public_macro_removed",
        "public macro removed from the headers → API_BREAK",
    ),
    (
        "INLINE_FUNCTION_REMOVED",
        "inline_function_removed",
        "public header-only inline function removed (no exported symbol) → API_BREAK",
    ),
    (
        "PUBLIC_TYPEDEF_REMOVED",
        "public_typedef_removed",
        "public typedef/alias removed (no exported symbol) → API_BREAK",
    ),
    (
        "SOURCE_FACT_COVERAGE_INCOMPLETE",
        "source_fact_coverage_incomplete",
        "a mandatory fact family was partial/failed, or the two sides' fact-set identity is incompatible (ADR-038 C.8) → RISK",
    ),
    (
        "PUBLIC_REACHABILITY_CHANGED",
        "public_reachability_changed",
        "── Source graph evidence (ADR-028 L5 / ADR-031 D6) ───────────────────── -- Emitted only by the source-graph diff over two L5 graph summaries -- (graph/source_graph_summary.json). Per ADR-031 D6 these *explain and -- prioritize* impact — they never, on their own, decide or suppress an -- artifact-proven ABI break; all default to RISK (COMPATIBLE_WITH_RISK). -- decl entered/left the public-API reachability closure → RISK",
    ),
    (
        "SOURCE_TO_BINARY_MAPPING_CHANGED",
        "source_to_binary_mapping_changed",
        "a persisting decl now maps to a different exported symbol → RISK",
    ),
    (
        "GENERATED_HEADER_REACHES_PUBLIC_API",
        "generated_header_reaches_public_api",
        "a generated file entered the public declaration closure → RISK",
    ),
    (
        "CALL_GRAPH_PUBLIC_ENTRY_REACHABILITY_CHANGED",
        "call_graph_public_entry_reachability_changed",
        "impl reachable from an exported entry changed → COMPATIBLE (quality)",
    ),
    (
        "INCLUDE_GRAPH_PUBLIC_HEADER_DRIFT",
        "include_graph_public_header_drift",
        "the include closure of a public header changed → RISK",
    ),
    (
        "BUILD_OPTION_REACHES_PUBLIC_SYMBOL",
        "build_option_reaches_public_symbol",
        "a changed ABI-relevant option reaches a public symbol → RISK",
    ),
    (
        "PUBLIC_API_INTERNAL_DEPENDENCY_ADDED",
        "public_api_internal_dependency_added",
        "a public entry newly reaches an internal (non-public) decl via the L5 graph → RISK",
    ),
    (
        "TARGET_DEPENDENCY_ADDED",
        "target_dependency_added",
        "the library gained an inter-target build/link dependency → RISK",
    ),
    (
        "EXPORTED_SYMBOL_SOURCE_OWNER_CHANGED",
        "exported_symbol_source_owner_changed",
        "an exported symbol's owning source/TU changed (implementation relocated) → RISK",
    ),
    (
        "DECLARATION_RENAMED",
        "declaration_renamed",
        "G31 Phase B (ADR-048): graph-node reconciliation outcomes, distinct -- from a plain add+remove pair in the L5 graph diff. Emitted only when -- buildsource.graph_reconcile.reconcile_graph_diff finds an unambiguous -- (canonical-id, alias, or unique-structural-context) old/new match for -- a declaration/type that the raw node-id diff would otherwise report as -- an unrelated removal + addition. Pure enrichment/classification -- metadata — never overrides or suppresses an artifact-proven finding -- (ADR-028 D3); all default to RISK (COMPATIBLE_WITH_RISK). -- graph-reconciled: same entity, new qualified name → RISK",
    ),
)
