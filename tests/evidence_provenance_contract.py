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

"""Executable classification of every ``ChangeKind`` for G39's per-finding
evidence-provider model (``Change.evidence_provenance``).

Why this file exists
---------------------

G39 (``docs/contribute/plans/g39-per-finding-evidence-provider-model.md``)
adds ``Change.evidence_provenance``: which evidence tier(s)/provider(s)
actually produced and corroborated one specific finding. Phase 1 wires real
producers to set it, one detector-module slice at a time (never the whole
inventory in one PR -- see the plan's own phasing discipline, sized
specifically to avoid a repeat of the incident this pattern already exists
to prevent: PR #753 shipped ``canonical_finding_id`` with three ``ChangeKind``
entries silently missing from its own classification list, and nothing
failed for hours because an *absent* entry produces no failure anywhere --
PR #759 had to add them after review caught it by hand).

This module is the mechanical half of that discipline, mirroring
``tests/canonical_identity_contract.py`` exactly: a new ``ChangeKind`` (or a
``ChangeKind`` whose producing detector starts setting ``evidence_provenance``)
must be placed in exactly one bucket below, or
``tests/test_evidence_provenance_completeness.py`` fails CI. The judgement
(which bucket a kind belongs in) stays manual; only the exhaustiveness is
enforced.

The contract
------------

``PROVENANCE_STATIC``
    The kind's producing detector sets a constant ``evidence_provenance``
    tuple, the same value for every instance of this kind (the common case
    for L0/L1 detectors, whose provenance is a static fact of which module
    ran, not a per-finding derivation).

``PROVENANCE_PER_FINDING``
    The kind's producing detector computes ``evidence_provenance`` per
    instance, inspecting the specific finding's own evidence (the common
    case for L2+ detectors, where the correct provenance genuinely varies
    finding-by-finding within one kind -- see the plan's own "layout
    findings" investigation for why this is not merely a per-kind constant).

``PROVENANCE_UNVERIFIED``
    ``evidence_provenance`` is not yet wired for this kind's producing call
    site(s) -- Phase 1 has not reached it. This is a backlog, not a verdict:
    every kind starts here until its producer is wired. Entries leave only
    by being verified and moved, the same discipline
    ``canonical_identity_contract.UNVERIFIED`` already establishes.
"""

from __future__ import annotations

#: Producers set a constant evidence_provenance tuple for every instance of
#: this kind. G39 Phase 1's first slice (L0/L1-only detectors,
#: diff_platform_elf_dynamic._diff_security_hardening): both kinds read
#: only old_elf.has_stack_canary/has_fortify_source, themselves derived
#: purely from .dynsym import/symbol names (elf_metadata._finalize_
#: hardening) -- both:l0:elf_symtab on every instance, verified by
#: tests/_detector_mutations.py's _m_stack_canary_removed/
#: _m_fortify_source_weakened.
#: G39 Phase 1's second sub-slice: the remaining kinds in the same
#: diff_platform_elf_dynamic detectors. Each traced to elf_metadata's
#: exact field-derivation code (`_parse_segments`/`_finalize_hardening`/
#: `_parse_dynamic`): relro_weakened/pie_disabled are genuine composites
#: (program-header + .dynamic, and .dynamic + ELF-header, respectively);
#: writable_executable_segment/executable_stack/executable_stack_removed
#: are pure program-header reads. Verified by tests/_detector_mutations.py's
#: _m_relro_weakened/_m_pie_disabled/_m_writable_executable_segment/
#: _m_executable_stack_introduced/_m_executable_stack_removed.
PROVENANCE_STATIC: frozenset[str] = frozenset(
    {
        "executable_stack",
        "executable_stack_removed",
        "fortify_source_weakened",
        "pie_disabled",
        "relro_weakened",
        "stack_canary_removed",
        "writable_executable_segment",
    }
)

#: Producers compute evidence_provenance per instance. Empty until Phase 1's
#: L2+ slice wires one.
PROVENANCE_PER_FINDING: frozenset[str] = frozenset()

#: Not yet wired -- every ChangeKind starts here (Phase 1 has not run for
#: any kind yet). See the module docstring.
PROVENANCE_UNVERIFIED = frozenset(
    {
        "abi_relevant_build_flag_changed",
        "abi_surface_explosion",
        "abi_tag_changed",
        "allocator_replacement_added",
        "allocator_replacement_removed",
        "anon_field_changed",
        "api_depends_on_consumer_env",
        "atomic_qualifier_changed",
        "base_class_offset_changed",
        "base_class_position_changed",
        "base_class_virtual_changed",
        "behavioural_default_changed",
        "bind_now_disabled",
        "bit_int_width_changed",
        "branch_protection_improved",
        "branch_protection_weakened",
        "build_context_changed",
        "build_option_reaches_public_symbol",
        "bundle_intra_dep_removed",
        "bundle_intra_dep_resolved_to_different_version",
        "bundle_intra_dep_signature_changed",
        "bundle_intra_dep_signature_unverified",
        "bundle_intra_type_changed",
        "bundle_library_added",
        "bundle_library_removed",
        "bundle_manifest_instantiation_added",
        "bundle_manifest_instantiation_removed",
        "bundle_provider_changed",
        "bundle_soname_skew",
        "bundle_unresolved_intra_dependency",
        "bundle_variant_coverage_regressed",
        "call_graph_public_entry_reachability_changed",
        "calling_convention_changed",
        "cet_protection_improved",
        "cet_protection_weakened",
        "char8t_migration",
        "char_signedness_changed",
        "common_symbol_risk",
        "compat_version_changed",
        "compile_context_conflict",
        "concept_tightened",
        "constant_added",
        "constant_changed",
        "constant_removed",
        "constexpr_value_changed",
        "consumer_required_symbol_removed",
        "cpo_kind_changed",
        "cpu_dispatch_isa_dropped",
        "ctor_explicit_added",
        "ctor_explicit_removed",
        "ctor_overload_ambiguity_risk",
        "cxx_standard_floor_raised",
        "declaration_identity_reconciled",
        "declaration_moved",
        "declaration_renamed",
        "default_argument_changed",
        "default_template_arg_changed",
        "dt_relr_introduced",
        "dt_relr_removed",
        "dwarf_info_missing",
        "dynamic_loading_flags_changed",
        "elf_abi_flags_changed",
        "elf_class_changed",
        "elf_endianness_changed",
        "elf_init_fini_changed",
        "elf_machine_changed",
        "elf_osabi_changed",
        "enum_became_scoped",
        "enum_deprecated_added",
        "enum_deprecated_removed",
        "enum_last_member_value_changed",
        "enum_lost_scoped",
        "enum_member_added",
        "enum_member_removed",
        "enum_member_renamed",
        "enum_member_value_changed",
        "enum_size_flag_changed",
        "enum_underlying_size_changed",
        "evidence_required_missing",
        "exceptions_mode_changed",
        "experimental_graduated",
        "experimental_removed_without_replacement",
        "exported_not_public",
        "exported_object_alignment_reduced",
        "exported_symbol_source_owner_changed",
        "field_access_changed",
        "field_became_const",
        "field_became_mutable",
        "field_became_volatile",
        "field_bitfield_changed",
        "field_default_initializer_changed",
        "field_default_initializer_removed",
        "field_deprecated_added",
        "field_deprecated_removed",
        "field_lost_const",
        "field_lost_mutable",
        "field_lost_volatile",
        "field_renamed",
        "flexible_array_member_changed",
        "float_abi_changed",
        "frame_register_changed",
        "func_added",
        "func_became_inline",
        "func_contract_attribute_added",
        "func_contract_attribute_removed",
        "func_cv_changed",
        "func_deleted",
        "func_deleted_dwarf",
        "func_deleted_elf_fallback",
        "func_deprecated_added",
        "func_deprecated_removed",
        "func_exception_spec_changed",
        "func_language_linkage_changed",
        "func_likely_renamed",
        "func_lost_inline",
        "func_noexcept_added",
        "func_noexcept_removed",
        "func_override_specifier_added",
        "func_override_specifier_removed",
        "func_params_changed",
        "func_pure_virtual_added",
        "func_ref_qual_changed",
        "func_removed",
        "func_removed_elf_only",
        "func_return_changed",
        "func_static_changed",
        "func_variadic_added",
        "func_variadic_removed",
        "func_virtual_added",
        "func_virtual_became_pure",
        "func_virtual_removed",
        "func_visibility_changed",
        "func_visibility_protected_changed",
        "generated_file_dependency_unstable",
        "generated_header_changed",
        "generated_header_reaches_public_api",
        "glibcxx_dual_abi_flip_detected",
        "handle_type_changed",
        "hash_style_removed",
        "header_binary_context_mismatch",
        "header_build_context_mismatch",
        "header_parse_context_drift",
        "hidden_friend_added",
        "hidden_friend_removed",
        "identity_collision_detected",
        "ifunc_introduced",
        "ifunc_removed",
        "imported_symbol_added",
        "imported_symbol_removed",
        "include_graph_public_header_drift",
        "inline_body_changed",
        "inline_body_references_renamed_member",
        "inline_function_removed",
        "inline_namespace_moved",
        "inline_namespace_version_bumped",
        "instantiation_missing_from_binary",
        "integer_model_changed",
        "internal_symbol_required_by_public_api",
        "internal_template_leaks_via_public_api",
        "internal_type_leaks_via_public_api",
        "interpreter_changed",
        "kabi_crc_changed",
        "kabi_export_type_changed",
        "kabi_symbol_added",
        "kabi_symbol_namespace_changed",
        "kabi_symbol_removed",
        "layer_coverage_asymmetric",
        "layout_unverifiable",
        "libcpp_abi_version_changed",
        "library_version_downgraded",
        "link_export_policy_changed",
        "long_double_abi_changed",
        "lto_mode_changed",
        "macho_cpu_type_changed",
        "macho_filetype_changed",
        "macho_linkage_flags_changed",
        "macho_reexport_changed",
        "macos_deployment_target_raised",
        "mandatory_template_param_added",
        "method_access_changed",
        "musllinux_glibc_dependency_detected",
        "needed_added",
        "needed_order_changed",
        "needed_removed",
        "numpy_abi_major_incompatible",
        "numpy_capi_consumption_added",
        "numpy_capi_consumption_removed",
        "numpy_metadata_understates_required_version",
        "numpy_target_floor_raised",
        "odr_source_conflict",
        "odr_type_variant",
        "opaque_invariant_broken",
        "os_deployment_floor_raised",
        "overload_added",
        "overload_set_rerouted",
        "param_became_va_list",
        "param_default_value_changed",
        "param_default_value_removed",
        "param_lost_va_list",
        "param_pointer_level_changed",
        "param_renamed",
        "param_restrict_changed",
        "pe_forwarder_changed",
        "pe_hardening_improved",
        "pe_hardening_weakened",
        "pe_import_load_mode_changed",
        "pe_machine_changed",
        "pe_ordinal_retargeted",
        "platform_baseline_floor_raised",
        "polymorphic_type_non_virtual_dtor",
        "private_header_leak",
        "protected_visibility_changed",
        "public_api_exposes_stl_by_value",
        "public_api_internal_dependency_added",
        "public_macro_removed",
        "public_macro_value_changed",
        "public_not_exported",
        "public_reachability_changed",
        "public_surface_grew",
        "public_surface_shrank",
        "public_to_internal_dependency",
        "public_typedef_removed",
        "public_typedef_target_changed",
        "python_abi3_dropped",
        "python_abi3_floor_raised",
        "python_api_callable_kind_changed",
        "python_api_class_added",
        "python_api_class_removed",
        "python_api_default_removed",
        "python_api_function_added",
        "python_api_function_removed",
        "python_api_method_added",
        "python_api_method_removed",
        "python_api_overload_removed",
        "python_api_parameter_added",
        "python_api_parameter_kind_changed",
        "python_api_parameter_removed",
        "python_api_parameter_renamed",
        "python_api_parameter_type_changed",
        "python_api_return_type_changed",
        "python_api_stub_invalid",
        "python_gil_abi_changed",
        "python_stable_abi_violation",
        "removed_const_overload",
        "return_pointer_level_changed",
        "rpath_changed",
        "rpath_type_changed",
        "rtti_for_internal_type",
        "rtti_inheritance_changed",
        "rtti_mode_changed",
        "runpath_changed",
        "runtime_floor_raised",
        "runtime_symbol_provider_changed",
        "runtime_weak_resolution_changed",
        "sanitizer_mode_changed",
        "secondary_vtable_group_changed",
        "serialization_tag_changed",
        "soname_bump_recommended",
        "soname_bump_unnecessary",
        "soname_changed",
        "soname_missing",
        "source_binary_provenance_mismatch",
        "source_decl_binary_symbol_mismatch",
        "source_fact_coverage_incomplete",
        "source_level_kind_changed",
        "source_surface_dso_mismatch",
        "source_to_binary_mapping_changed",
        "standard_layout_lost",
        "static_tls_introduced",
        "static_tls_removed",
        "std_reexport_removed",
        "stdlib_debug_mode_changed",
        "stdlib_implementation_changed",
        "struct_alignment_changed",
        "struct_field_offset_changed",
        "struct_field_removed",
        "struct_field_type_changed",
        "struct_packing_changed",
        "struct_packing_mode_changed",
        "struct_return_convention_changed",
        "struct_size_changed",
        "suppression_reachability_unknown",
        "suppression_would_hide_public_break",
        "sycl_backend_driver_req_changed",
        "sycl_implementation_changed",
        "sycl_overload_set_removed",
        "sycl_pi_entrypoint_added",
        "sycl_pi_entrypoint_removed",
        "sycl_pi_version_changed",
        "sycl_plugin_added",
        "sycl_plugin_removed",
        "sycl_plugin_search_path_changed",
        "sycl_runtime_version_changed",
        "symbol_binding_became_unique",
        "symbol_binding_changed",
        "symbol_binding_lost_unique",
        "symbol_binding_strengthened",
        "symbol_elf_visibility_changed",
        "symbol_leaked_from_dependency_changed",
        "symbol_moved_version_node",
        "symbol_renamed_batch",
        "symbol_size_changed",
        "symbol_size_changed_const_object",
        "symbol_size_changed_internal",
        "symbol_type_changed",
        "symbol_version_alias_changed",
        "symbol_version_defined_added",
        "symbol_version_defined_removed",
        "symbol_version_node_removed",
        "symbol_version_required_added",
        "symbol_version_required_added_compat",
        "symbol_version_required_removed",
        "symbolic_binding_mode_changed",
        "tag_type_renamed",
        "tail_padding_reuse_changed",
        "target_dependency_added",
        "template_body_changed",
        "template_param_type_changed",
        "template_return_type_changed",
        "text_relocation_introduced",
        "text_relocation_removed",
        "threadsafe_statics_mode_changed",
        "time64_abi_changed",
        "tls_model_changed",
        "tls_var_size_changed",
        "toolchain_flag_drift",
        "toolchain_version_changed",
        "trivially_copyable_lost",
        "type_added",
        "type_alignment_changed",
        "type_base_changed",
        "type_became_abstract",
        "type_became_final",
        "type_became_opaque",
        "type_deprecated_added",
        "type_deprecated_removed",
        "type_field_added",
        "type_field_added_compatible",
        "type_field_offset_changed",
        "type_field_removed",
        "type_field_type_changed",
        "type_kind_changed",
        "type_lost_abstract",
        "type_lost_final",
        "type_removed",
        "type_size_changed",
        "type_visibility_changed",
        "type_vtable_changed",
        "typedef_base_changed",
        "typedef_removed",
        "typedef_version_sentinel",
        "undocumented_export_ratio_increased",
        "uninstantiated_template_removed",
        "union_field_added",
        "union_field_removed",
        "union_field_type_changed",
        "unnamed_type_in_public_abi",
        "unspecified_return_now_named",
        "unversioned_exported_symbol",
        "used_reserved_field",
        "value_abi_trait_changed",
        "var_access_changed",
        "var_access_widened",
        "var_added",
        "var_alignment_changed",
        "var_became_const",
        "var_deprecated_added",
        "var_deprecated_removed",
        "var_lost_const",
        "var_removed",
        "var_type_changed",
        "var_value_changed",
        "vector_abi_changed",
        "version_script_missing",
        "versioned_symbol_scheme_detected",
        "virtual_base_offset_changed",
        "virtual_method_added",
        "visibility_leak",
        "vptr_introduced",
        "vtable_slot_count_changed",
        "vtable_symbol_identity_changed",
        "vtable_thunk_offset_changed",
        "vtable_thunk_set_changed",
        "vtt_slot_count_changed",
        "wchar_model_changed",
        "wheel_closure_dependency_violation",
        "wheel_rpath_not_portable",
        "wheel_tag_architecture_mismatch",
        "whole_program_vtables_mode_changed",
        "x86_isa_baseline_raised",
    }
)

#: The partition the completeness test enforces.
ALL_BUCKETS = {
    "PROVENANCE_STATIC": PROVENANCE_STATIC,
    "PROVENANCE_PER_FINDING": PROVENANCE_PER_FINDING,
    "PROVENANCE_UNVERIFIED": PROVENANCE_UNVERIFIED,
}
