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

"""The H4 heuristic registry: oracle cells and non-heuristic exemptions.

Since design-hardening Phase 5 the *runtime* registry
(``abicheck.model.name_heuristics``, catalogued by
``abicheck.policy.name_heuristics``) is the registration point. Every key
produced by :func:`_family_f4_inventory.heuristic_site_inventory` is either

* resolved by :func:`_family_f4_resolve.resolve_site` to a registered
  heuristic (derived from the live registry -- no row here), or
* an exemption row in :data:`HEURISTICS` naming a category from
  :data:`EXEMPTION_REASONS`: a spelling check that is *not* a naming
  convention (a mangling grammar, a C++ type spelling, abicheck's own key
  format, a user-written rule, a format sniff, a platform-defined name).

There is no "uncategorized"/``convention`` category any more: a site that
decides from a naming convention must be registered at runtime with its
effect, and a severity-raising one with its structural fact.
:data:`COVERED` holds the FP/FN/control cells per registered heuristic id.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Covered:
    """FP/FN/control oracle cells for one registered heuristic."""

    fp_cells: tuple[str, ...]
    fn_cells: tuple[str, ...]
    control_cells: tuple[str, ...]
    bugs: tuple[int, ...] = ()


#: Keyed by runtime heuristic id. Every severity-raising heuristic must be here.
COVERED: dict[str, Covered] = {
    "enum_sentinel": Covered(
        fp_cells=("sentinel.fp.mid_list_max_name",),
        fn_cells=(
            "sentinel.fn.near_miss_MAXIMUM_SIZE",
            "sentinel.fn.near_miss_BACKEND",
            "sentinel.fn.near_miss_BLAST",
        ),
        control_cells=("sentinel.control.true_sentinel",),
        bugs=(1411,),
    ),
    "serialization_tag": Covered(
        fp_cells=("tag.fp.format_tag_lookalike",),
        fn_cells=("tag.fn.untagged_value_swap",),
        control_cells=("tag.control.registry_swap",),
        bugs=(1411,),
    ),
    "experimental_namespace": Covered(
        fp_cells=("experimental.fp.promotion_signature_mismatch",),
        fn_cells=(
            "experimental.fn.segment_near_miss_suffix",
            "experimental.fn.segment_near_miss_camel",
        ),
        control_cells=("experimental.control.promotion",),
        bugs=(1411,),
    ),
    "internal_namespace": Covered(
        fp_cells=(
            "internal_ns.fp.reached_by_pointer_detail",
            "internal_ns.fp.reached_by_pointer_impl",
        ),
        fn_cells=("internal_ns.fn.segment_near_miss",),
        control_cells=("internal_ns.control.header_seed_vetoed",),
        bugs=(1231,),
    ),
    "internal_type_leak": Covered(
        fp_cells=("internal_leak.fp.unreachable",),
        fn_cells=("internal_leak.fn.segment_near_miss",),
        control_cells=("internal_leak.control.reached_by_value",),
    ),
    "internal_template_leak": Covered(
        fp_cells=("internal_template.fp.instantiation_added",),
        fn_cells=("internal_template.fn.segment_near_miss",),
        control_cells=("internal_template.control.instantiation_removed",),
    ),
    "pimpl_renamed_member": Covered(
        fp_cells=("pimpl.fp.no_inline_accessor",),
        fn_cells=("pimpl.fn.segment_near_miss",),
        control_cells=("pimpl.control.inline_accessor",),
    ),
    "experimental_removal": Covered(
        fp_cells=("experimental.control.promotion",),
        fn_cells=(
            "experimental.fn.segment_near_miss_suffix",
            "experimental.fn.segment_near_miss_camel",
        ),
        control_cells=("experimental.fp.promotion_signature_mismatch",),
    ),
    "prefix_rename": Covered(
        fp_cells=("prefix_rename.fp.old_names_kept",),
        fn_cells=("prefix_rename.fn.suffixed",),
        control_cells=("prefix_rename.control.prefixed",),
    ),
    "sycl_overload_removal": Covered(
        fp_cells=("sycl.fp.family_kept",),
        fn_cells=("sycl.fn.near_miss_queue",),
        control_cells=("sycl.control.family_removed",),
    ),
    "bundle_soname_cohort": Covered(
        fp_cells=("bundle.fp.soname_unread",),
        fn_cells=("bundle.fn.lockstep_bump",),
        control_cells=("bundle.control.skew",),
    ),
    "inline_namespace_move": Covered(
        fp_cells=("inline_ns.fp.added_beside_old",),
        fn_cells=("inline_ns.fn.segment_near_miss",),
        control_cells=("inline_ns.control.moved",),
    ),
}

EXEMPTION_REASONS: dict[str, str] = {
    "grammar": (
        "parses a documented symbol-encoding grammar (Itanium/MSVC mangling, "
        "Mach-O underscore, ELF decoration, GCC [abi:] tags); the spelling is "
        "the representation, not a naming convention -- F3 owns its identity cells"
    ),
    "spelling": (
        "tokenizes or canonicalizes a C/C++ spelling (cv-qualifiers, "
        "declarators, operators, template arguments, auto/lambda/decltype); the "
        "decision is made on the canonical form, not on a name's shape"
    ),
    "own_format": (
        "parses abicheck's own serialized key/schema/field-name format (graph "
        "node ids, change keys, {ctor}/{dtor} placeholders, report fields)"
    ),
    "user_rule": (
        "a user-stated selector/glob/prefix from a policy, suppression file or "
        "caller argument; the rule is the contract the user asked for"
    ),
    "sniff": (
        "input-format sniffing (file extension/JSON shape) that selects a "
        "loader and decides no finding"
    ),
    "platform": (
        "a toolchain/platform naming contract (GLIBC_ version nodes, $ORIGIN, "
        "kABI _GPL, cxx11 ABI tag, the std:: namespace) where the name *is* "
        "the documented interface"
    ),
}

#: One row per inventoried site that is NOT a registered heuristic, naming its
#: exemption category (maintained by hand -- the completeness test says what
#: to add or drop).
HEURISTICS: dict[str, str] = {
    "abicheck.checker::compare::affix.startswith:'rename:'": "own_format",
    "abicheck.checker_types::<module>::re:'^[A-Za-z0-9][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9._-]*#[A-Za-z0-9][A...": "own_format",
    'abicheck.classify::AbiJsonClassifier::re:\'(?=[\\\\s\\\\S]*"schema_version"\\\\s*:\\\\s*\\\\d+)(?=[\\\\s\\\\S]*"sections"\\\\s*...': "sniff",
    "abicheck.classify::AbiJsonClassifier::re:'(^|[,{])\\\\s*\"library\"\\\\s*:'": "sniff",
    "abicheck.classify::BinaryExtensionClassifier.accepts::affix.endswith:ext": "sniff",
    "abicheck.classify::BinaryExtensionClassifier::re:'\\\\.so(?:\\\\.|$)'": "sniff",
    "abicheck.classify::FallbackSniffClassifier.accepts::affix.startswith:'{'": "sniff",
    "abicheck.compare.edge_query::<module>::re:'Z(?:T[VISTFH]|Th[n0-9_]*|Tv[n0-9_]*|Tc[hv0-9n_]*|GV)?N?[KVr]*(?:S[ta...": "own_format",
    "abicheck.compare.edge_query::EdgeEvidence._resolve_debug::affix.startswith:DEBUG_TYPE_PREFIX": "own_format",
    "abicheck.compare.edge_query::EdgeEvidence._resolve_exports::affix.startswith:BINARY_SYMBOL_PREFIX": "own_format",
    "abicheck.compare.edge_query::EdgeEvidence._resolve_header_edge::affix.startswith:'header://'": "own_format",
    "abicheck.compare.edge_query::EdgeEvidence._resolve_linker_name::affix.startswith:'symbol://'": "own_format",
    "abicheck.compare.export_join::_macho_shifted::affix.startswith:'_'": "grammar",
    "abicheck.compare.export_owner_resolution::_placeholder_owner::affix.startswith:_CTOR_PLACEHOLDER_PREFIX": "own_format",
    "abicheck.compare.export_owner_resolution::_placeholder_owner::affix.startswith:_DTOR_PLACEHOLDER_PREFIX": "own_format",
    "abicheck.compare.namespace_move::_declaring_entity::affix.endswith:suffix": "spelling",
    "abicheck.compare.namespace_shape_detectors::_batch_demangle_public::affix.startswith:'_Z'": "grammar",
    "abicheck.compare.namespace_shape_detectors::_qualified_function_name::affix.startswith:'_Z'": "grammar",
    "abicheck.compare.naming_conventions::_strip_param_decorators::re:'\\\\bconst\\\\b|\\\\bvolatile\\\\b'": "spelling",
    "abicheck.compare.opaque_struct_downgrade::struct_change_record_name::affix.endswith:f'::{c.field_name}'": "spelling",
    "abicheck.compare.opaque_types::<module>::re:'(?:\\\\s+|\\\\bconst\\\\b|\\\\bvolatile\\\\b)*'": "spelling",
    "abicheck.compare.opaque_types::<module>::re:'\\\\s*'": "spelling",
    "abicheck.compare.opaque_types::<module>::re:'\\\\w+'": "spelling",
    "abicheck.compare.opaque_types::_skip_member_pointer_owner_scope::affix.startswith:'::'": "spelling",
    "abicheck.compare.spelling_pattern::<module>::re:'[A-Za-z0-9_]+'": "spelling",
    "abicheck.compare.spelling_pattern::_build_flat_spelling_pattern::re:_bounded(alternation)": "spelling",
    "abicheck.compare.spelling_pattern::_build_spelling_pattern::re:_bounded(body)": "spelling",
    "abicheck.compare.template_surface::<module>::re:'\\\\[abi:[^\\\\]]*\\\\]'": "grammar",
    "abicheck.compare.template_surface::<module>::re:'\\\\boperator\\\\s*(?:<=>|<<=|>>=|<<|>>|<=|>=|->\\\\*?|<|>)'": "spelling",
    "abicheck.compare.template_surface::_needs_demangle::affix.startswith:'_Z'": "grammar",
    "abicheck.compare.template_surface::_needs_demangle::affix.startswith:'operator'": "spelling",
    "abicheck.diff_abi_tags::<module>::re:'B(\\\\d+)([A-Za-z0-9_]+)'": "grammar",
    "abicheck.diff_abi_tags::<module>::vocab:_CXX11_ABI_MARKERS": "platform",
    "abicheck.diff_atomic::<module>::re:'\\\\b_Atomic\\\\b'": "spelling",
    "abicheck.diff_bit_int::<module>::re:'\\\\b_BitInt\\\\s*\\\\(\\\\s*(\\\\d+)\\\\s*\\\\)'": "spelling",
    "abicheck.diff_char8t::<module>::re:'\\\\bchar8_t\\\\b'": "spelling",
    "abicheck.diff_cpp_patterns::<module>::re:'::operator(?![A-Za-z0-9_])'": "spelling",
    "abicheck.diff_cpp_patterns::_extract_soname_major::re:'-(\\\\d+)\\\\.dll$'": "spelling",
    "abicheck.diff_cpp_patterns::_extract_soname_major::re:'\\\\.(\\\\d+)\\\\.dylib$'": "spelling",
    "abicheck.diff_cpp_patterns::_extract_soname_major::re:'\\\\.so\\\\.(\\\\d+)$'": "spelling",
    "abicheck.diff_cpp_patterns::detect_bundle_soname_skew::affix.startswith:cohort_prefix": "user_rule",
    "abicheck.diff_cxx_rules::<module>::vocab:_LESS_THAN_LED_OPERATOR_TOKENS": "spelling",
    "abicheck.diff_cxx_rules::<module>::vocab:_OPERATOR_ANGLE_TOKENS": "spelling",
    "abicheck.diff_cxx_rules::_conversion_operator_marker_index::affix.startswith:_CONVERSION_OPERATOR_MARKER": "spelling",
    "abicheck.diff_cxx_rules::_less_than_led_operator_token_len::affix.startswith:tok": "spelling",
    "abicheck.diff_cxx_rules::_operator_angle_token_len::affix.startswith:tok": "spelling",
    "abicheck.diff_cxx_rules::_split_at_top_level_separators::affix.startswith:'::'": "spelling",
    "abicheck.diff_cxx_rules::owner_class_of::affix.startswith:'operator '": "spelling",
    "abicheck.diff_cxx_rules::qualified_name_scope_components::affix.startswith:'operator '": "spelling",
    "abicheck.diff_default_value_reliability::<module>::re:'^expr:[0-9a-f]{16}$'": "own_format",
    "abicheck.diff_elf_layout::<module>::re:'^_ZTh(?P<offset>n?\\\\d+)_(?P<base>.+)$'": "grammar",
    "abicheck.diff_elf_layout::<module>::re:'^_ZTv(?P<offset>n?\\\\d+_n?\\\\d+)_(?P<base>.+)$'": "grammar",
    "abicheck.diff_elf_layout::<module>::re:f'^_ZTc(?P<offset>{_THUNK_C_CALL}{_THUNK_C_CALL})(?P<base>.+)$'": "grammar",
    "abicheck.diff_elf_layout::_base_is_runtime::affix.startswith:('NSt', 'NKSt', 'St', 'Ss', 'Si', 'So')": "platform",
    "abicheck.diff_elf_layout::_is_runtime::affix.startswith:_RUNTIME_RTTI_PREFIXES": "platform",
    "abicheck.diff_elf_layout::_method_name::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_elf_layout::_parse_thunk::affix.startswith:'_ZTc'": "grammar",
    "abicheck.diff_elf_layout::_parse_thunk::affix.startswith:'_ZTh'": "grammar",
    "abicheck.diff_elf_layout::_parse_thunk::affix.startswith:'_ZTv'": "grammar",
    "abicheck.diff_elf_layout::_sized_rtti::affix.startswith:prefix": "platform",
    "abicheck.diff_filtering::_embedded_stdlib_fields::affix.endswith:'&'": "spelling",
    "abicheck.diff_filtering::_embedded_stdlib_fields::affix.endswith:'*'": "spelling",
    "abicheck.diff_filtering::_filter_reserved_field_renames::affix.startswith:f'{struct_name}::'": "own_format",
    "abicheck.diff_helpers::<module>::re:'<\\\\s*(?:unnamed|anonymous)(?:\\\\s+(union|struct|class|enum)\\\\b)?'": "spelling",
    "abicheck.diff_helpers::_normalize_type_spelling::re:'[\\\\s*&]+$'": "spelling",
    "abicheck.diff_helpers::_normalize_type_spelling::re:'\\\\s*([*&])\\\\s*'": "spelling",
    "abicheck.diff_helpers::_normalize_type_spelling::re:'^(const|volatile)(\\\\s+(const|volatile))?\\\\s+'": "spelling",
    "abicheck.diff_helpers::_normalize_type_spelling::re:'^(struct|class|union)\\\\s+'": "spelling",
    "abicheck.diff_helpers::canonicalize_record_symbol::affix.endswith:suffix": "spelling",
    "abicheck.diff_kabi::_is_gpl::affix.endswith:'_GPL'": "platform",
    "abicheck.diff_layout::_index._keep::affix.startswith:STDLIB_TYPE_NAMESPACE_PREFIXES": "platform",
    "abicheck.diff_long_double::_exported::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_namespaces::_looks_like_real_mangled_name::affix.startswith:'?'": "grammar",
    "abicheck.diff_namespaces::_looks_like_real_mangled_name::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_namespaces::_looks_like_real_mangled_name::affix.startswith:'__Z'": "grammar",
    "abicheck.diff_platform::_diff_inline_namespace::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_platform::_diff_vtable_identity._rtti_key::affix.startswith:p": "grammar",
    "abicheck.diff_platform::_diff_vtable_identity::affix.startswith:p": "grammar",
    "abicheck.diff_platform_elf_symbols::<module>::re:'^_Zn[wa][jmy]Pv|^_Zd[la]Pv(?:S_|Pv)'": "grammar",
    "abicheck.diff_platform_elf_symbols::<module>::vocab:_ALLOCATOR_MANGLING_PREFIXES": "platform",
    "abicheck.diff_platform_elf_symbols::_check_object_alignment_reduced::affix.startswith:('_ZTV', '_ZTI', '_ZTS', '_ZTT')": "grammar",
    "abicheck.diff_platform_elf_symbols::_check_symbol_size_change::affix.startswith:('_ZTV', '_ZTI', '_ZTS', '_ZTT')": "grammar",
    "abicheck.diff_platform_elf_symbols::_diff_allocator_replacement._is_global_allocator::affix.startswith:_ALLOCATOR_MANGLING_PREFIXES": "platform",
    "abicheck.diff_platform_elf_symbols::_diff_elf_symbol_versioning._old_max_for_prefix::affix.startswith:prefix + '_'": "platform",
    "abicheck.diff_platform_elf_symbols::_floor_evidence_symbols::affix.startswith:prefix + '_'": "platform",
    "abicheck.diff_platform_elf_symbols::_is_const_unbounded_string_object::re:'\\\\s+'": "spelling",
    "abicheck.diff_platform_elf_symbols::_is_internal_data_symbol::affix.startswith:'_'": "grammar",
    "abicheck.diff_platform_elf_symbols::_is_internal_data_symbol::affix.startswith:('_Z', '__Z')": "grammar",
    "abicheck.diff_platform_elf_symbols::_max_parseable_tag::affix.startswith:prefix + '_'": "platform",
    "abicheck.diff_stdlib_impl::<module>::re:'(?<![A-Za-z0-9_:])std::'": "platform",
    "abicheck.diff_stdlib_impl::<module>::re:'(?<![A-Za-z0-9_:])std::__(ndk)?(\\\\d)'": "platform",
    "abicheck.diff_stdlib_impl::_detect_msvc_stl::affix.startswith:'?'": "grammar",
    "abicheck.diff_stdlib_impl::_enrich_from_demangled::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_stdlib_impl::_is_indirect_type::affix.endswith:'&'": "spelling",
    "abicheck.diff_stdlib_impl::_is_indirect_type::affix.endswith:'*'": "spelling",
    "abicheck.diff_stdlib_impl::_is_indirect_type::affix.endswith:(' const', ' volatile')": "spelling",
    "abicheck.diff_stdlib_impl::_public_type_embeds_stdlib_by_value::affix.endswith:'&'": "spelling",
    "abicheck.diff_stdlib_impl::_public_type_embeds_stdlib_by_value::affix.endswith:'*'": "spelling",
    "abicheck.diff_symbols::<module>::re:'\\\\b(?:const|volatile)\\\\b'": "spelling",
    "abicheck.diff_symbols_renames::<module>::re:'(?<![A-Za-z0-9_])operator(?![A-Za-z0-9_])'": "spelling",
    "abicheck.diff_symbols_renames::<module>::re:'^(C[123]|D[012])E'": "grammar",
    "abicheck.diff_symbols_renames::_ctor_dtor_variant::affix.startswith:'_ZN'": "grammar",
    "abicheck.diff_symbols_renames::_diff_fingerprint_renames::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_symbols_renames::_is_destructor_leaf::affix.startswith:'~'": "spelling",
    "abicheck.diff_symbols_renames::_plausible_rename::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_symbols_renames::_plausible_rename::affix.startswith:'~'": "spelling",
    "abicheck.diff_symbols_renames::_prefix_ends_at_a_name_boundary::affix.endswith:('::', '_')": "spelling",
    "abicheck.diff_symbols_renames::_strip_template_args::affix.endswith:'>'": "spelling",
    "abicheck.diff_symbols_variables::<module>::re:'\\\\b(?:const|volatile)\\\\b'": "spelling",
    "abicheck.diff_symbols_variables::<module>::re:'\\\\s*\\\\bconst\\\\b\\\\s*$'": "spelling",
    "abicheck.diff_symbols_variables::<module>::re:'^\\\\s*\\\\bconst\\\\b\\\\s*'": "spelling",
    "abicheck.diff_symbols_variables::_strip_trailing_declarator_const::re:'(?:\\\\s|const|volatile)*'": "spelling",
    "abicheck.diff_symbols_variables::_strip_trailing_declarator_const::re:'\\\\bconst\\\\b'": "spelling",
    "abicheck.diff_templates::<module>::re:'(?<![A-Za-z0-9_])decltype$'": "spelling",
    "abicheck.diff_templates::<module>::re:'(?<![A-Za-z0-9_])operator(?![A-Za-z0-9_])'": "spelling",
    "abicheck.diff_templates::<module>::re:'<[^<>]'": "spelling",
    "abicheck.diff_templates::<module>::vocab:_UNSPECIFIED_RETURN_MARKERS": "spelling",
    "abicheck.diff_templates::_batch_demangle_for_identity::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_templates::_canonical_identity_name::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_templates::_normalize_mach_o_mangled::affix.startswith:'__Z'": "grammar",
    "abicheck.diff_time64::<module>::re:'[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)*'": "spelling",
    "abicheck.diff_types::<module>::re:'^(.+)\\\\s*\\\\[\\\\s*0?\\\\s*\\\\]$'": "spelling",
    "abicheck.diff_types::_diff_overload_additions::affix.endswith:'{ctor}'": "own_format",
    "abicheck.diff_types::_diff_overload_additions::affix.endswith:'{dtor}'": "own_format",
    "abicheck.diff_types_surface::_is_abi_surface_type::affix.startswith:STDLIB_TYPE_NAMESPACE_PREFIXES": "platform",
    "abicheck.diff_unnamed_types::<module>::re:'Ut\\\\d*_'": "grammar",
    "abicheck.diff_unnamed_types::_exported_symbol_names::affix.startswith:'_Z'": "grammar",
    "abicheck.diff_unnamed_types::_unnamed_kind::affix.startswith:'Ul'": "grammar",
    "abicheck.diff_unnamed_types::_unnamed_kind::affix.startswith:'Ut'": "grammar",
    "abicheck.diff_versioning::<module>::vocab:_BASELINE_FLOOR_PREFIXES": "platform",
    "abicheck.diff_versioning::<module>::vocab:_GLIBC_INTERPRETER_MARKERS": "platform",
    "abicheck.diff_versioning::_scan_verneed_tags::affix.startswith:tag_prefix": "platform",
    "abicheck.diff_versioning::check_musllinux_glibc_dependency::affix.startswith:'GLIBC_'": "platform",
    "abicheck.diff_wheel_deployment::_armv7l_abi_violation::affix.startswith:'eabi'": "grammar",
    "abicheck.diff_wheel_deployment::_is_origin_relative_entry::affix.startswith:('$ORIGIN/', '${ORIGIN}/')": "platform",
    "abicheck.export_surface::<module>::re:'\\\\b(?:typename|template)\\\\b'": "spelling",
    "abicheck.export_surface::_is_tag_self_alias::affix.startswith:keyword": "spelling",
    "abicheck.export_surface::_toolchain_owned_aliases::affix.startswith:STDLIB_TYPE_NAMESPACE_PREFIXES": "platform",
    "abicheck.export_surface::_unresolved_type_edges::affix.startswith:STDLIB_TYPE_NAMESPACE_PREFIXES": "platform",
    "abicheck.internal_leak::<module>::re:'(\\\\*|&{1,2}|\\\\[\\\\d*\\\\]|\\\\bconst\\\\b|\\\\bvolatile\\\\b)'": "spelling",
    "abicheck.internal_leak::_bfs_collect_paths::affix.startswith:'indirect:'": "own_format",
    "abicheck.internal_leak::_build_call_graph_leak_change::affix.startswith:'overapprox:'": "own_format",
    "abicheck.internal_leak::_format_path::affix.startswith:'indirect:'": "own_format",
    "abicheck.internal_leak::_path_has_indirection::affix.startswith:'indirect:'": "own_format",
    "abicheck.internal_leak::_path_is_value_propagating::affix.startswith:('field:', 'base:', 'vbase:')": "own_format",
    "abicheck.internal_leak::_strip_decorators::re:'\\\\s+'": "spelling",
    "abicheck.internal_leak::compute_call_graph_leak_paths::affix.startswith:'_Z'": "grammar",
    "abicheck.internal_leak::compute_call_graph_leak_paths::affix.startswith:symbol_prefix": "grammar",
    "abicheck.policy.analysis_assurance_schema_staleness::_other_side_has_hybrid_deprecation_provenance::affix.endswith:':deprecated'": "own_format",
    "abicheck.policy.analysis_assurance_schema_staleness::_other_side_has_hybrid_deprecation_provenance::affix.endswith:':is_scoped'": "own_format",
    "abicheck.policy.contract_graph_encoding::<module>::vocab:_I1_DECL_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::<module>::vocab:_I1_TYPE_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::<module>::vocab:_SCHEMA1_DECL_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::<module>::vocab:_SCHEMA1_TYPE_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::graph_node_category::affix.startswith:_I1_DECL_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::graph_node_category::affix.startswith:_I1_TYPE_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::graph_node_category::affix.startswith:_SCHEMA1_DECL_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::graph_node_category::affix.startswith:_SCHEMA1_TYPE_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::graph_node_index::affix.startswith:'alias:'": "own_format",
    "abicheck.policy.contract_graph_encoding::graph_node_index::affix.startswith:'name:'": "own_format",
    "abicheck.policy.contract_graph_encoding::is_schema1_canonical::affix.startswith:_I1_DECL_PREFIXES + _I1_TYPE_PREFIXES": "own_format",
    "abicheck.policy.contract_graph_encoding::is_schema1_canonical::affix.startswith:_SCHEMA1_DECL_PREFIXES + _SCHEMA1_TYPE_PREFIXES": "own_format",
    "abicheck.policy.exit_decision::ExitDecision.exit_without_analysis_assurance::affix.endswith:'_contribution'": "own_format",
    "abicheck.policy.outcome::<module>::re:'^(0(\\\\.[0-9]+)*|1(\\\\.0+)?)$'": "own_format",
    "abicheck.policy.outcome_release::unclassified_release_contribution_fields::affix.endswith:'_contribution'": "own_format",
    "abicheck.policy.selectors::<module>::re:'<[^<>]*>'": "spelling",
    "abicheck.policy.selectors::_matches_source_location::re:':\\\\d+(?::\\\\d+)?$'": "user_rule",
    "abicheck.policy.selectors::_ns_match::affix.startswith:'_Z'": "grammar",
    "abicheck.policy.selectors_namespace_glob::<module>::re:'^\\\\(\\\\?s:(.*)\\\\)\\\\\\\\[Zz]$'": "user_rule",
    "abicheck.policy.selectors_namespace_glob::_SegmentGlobMatcher.__init__::re:'(?s:' + combined + ')\\\\Z'": "user_rule",
    "abicheck.policy.selectors_namespace_glob::_SegmentGlobMatcher.__init__::re:_translate_namespace_glob(pattern)": "user_rule",
    "abicheck.policy.selectors_namespace_glob::_compile_glob::re:fnmatch.translate(glob)": "user_rule",
    "abicheck.policy.selectors_namespace_glob::_compile_pattern::re:pattern": "user_rule",
    "abicheck.policy.selectors_namespace_glob::_compile_run::affix.endswith:'*'": "user_rule",
    "abicheck.policy.selectors_namespace_glob::_compile_run::re:'(?s:' + _fnmatch_segment_regex(source) + ')\\\\Z'": "user_rule",
    "abicheck.policy.type_spelling::<module>::re:'[*&]'": "spelling",
    "abicheck.policy.type_spelling::<module>::re:f'\\\\b{kw}\\\\b'": "spelling",
    "abicheck.post_processing::<module>::vocab:_EXT_INTERNAL_SYMBOL_PREFIXES": "own_format",
    "abicheck.post_processing::DemoteUnreachableInternalChurn.run._is_frozen::fnmatch:pat": "user_rule",
    "abicheck.post_processing::EscalateFrozenNamespaceViolations._candidate_forms::affix.startswith:'_Z'": "grammar",
    "abicheck.post_processing::EscalateFrozenNamespaceViolations._match::fnmatch:pat": "user_rule",
    "abicheck.post_processing::EscalateFrozenNamespaceViolations._tag::affix.startswith:'[frozen-namespace violation'": "own_format",
    "abicheck.post_processing::_is_off_python_surface::affix.startswith:'python_'": "own_format",
    "abicheck.post_processing::_is_off_python_surface::affix.startswith:_EXT_INTERNAL_SYMBOL_PREFIXES": "own_format",
    "abicheck.surface::<module>::vocab:_HIDDEN_FRIEND_KIND_NAMES": "own_format",
    "abicheck.surface::<module>::vocab:_MEMBER_LEVEL_TYPE_KIND_NAMES": "own_format",
    "abicheck.surface::<module>::vocab:_NEVER_FILTER_KIND_NAMES": "own_format",
    "abicheck.surface::<module>::vocab:_TYPE_LEVEL_KIND_NAMES": "own_format",
    "abicheck.surface::classify_change_surface::affix.startswith:'python_'": "own_format",
}
