# SPDX-License-Identifier: Apache-2.0
# Copyright (C) 2024 CodeRabbit Inc.
"""The generated detector specification matrix stays complete and in sync."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from abicheck.checker_policy import ChangeKind

REPO_DIR = Path(__file__).resolve().parent.parent


def _load_gen():
    path = REPO_DIR / "scripts" / "gen_detector_spec.py"
    spec = importlib.util.spec_from_file_location("gen_detector_spec", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(REPO_DIR / "scripts"))
    try:
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(str(REPO_DIR / "scripts"))
    return mod


def test_every_changekind_in_spec():
    gen = _load_gen()
    rows = gen.build_spec()
    kinds_in_spec = {r["kind"] for r in rows}
    all_kinds = {k.value for k in ChangeKind}
    assert kinds_in_spec == all_kinds, (
        f"spec missing: {all_kinds - kinds_in_spec}; extra: {kinds_in_spec - all_kinds}"
    )


def test_every_row_has_a_known_category():
    gen = _load_gen()
    valid = {"breaking", "api_break", "risk", "addition", "quality"}
    bad = [r["kind"] for r in gen.build_spec() if r["category"] not in valid]
    assert not bad, f"rows with unknown category (unpartitioned?): {bad}"


def test_generated_files_in_sync():
    """The committed docs/reference/detector-spec.{md,json} are up to date."""
    gen = _load_gen()
    assert gen.main(["--check"]) == 0, (
        "detector spec is stale — run: python scripts/gen_detector_spec.py"
    )


# Baseline *set* of kinds with no declared evidence tier (EVIDENCE_TIER_BY_KIND
# is intentionally partial). Tracking the set, not just the count, closes the
# hole where mapping one kind and adding another unmapped kind keeps the count
# flat: any kind NOT in this set must have a tier, so a newly-added ChangeKind
# without one fails. Mapping a kind (removing it from the unspecified set) is
# always welcome — trim it from this baseline when you do.
UNSPECIFIED_TIER_BASELINE: frozenset[str] = frozenset({
    'abi_relevant_build_flag_changed', 'abi_surface_explosion', 'anon_field_changed',
    'api_depends_on_consumer_env', 'base_class_position_changed', 'base_class_virtual_changed',
    'behavioural_default_changed', 'build_context_changed',
    'build_option_reaches_public_symbol', 'bundle_intra_dep_resolved_to_different_version',
    'bundle_intra_type_changed', 'bundle_library_added', 'bundle_library_removed',
    'bundle_manifest_instantiation_added', 'call_graph_public_entry_reachability_changed',
    'common_symbol_risk', 'compat_version_changed', 'constant_added', 'constant_removed',
    'constexpr_value_changed', 'ctor_explicit_removed', 'cxx_standard_floor_raised',
    'default_argument_changed', 'dwarf_info_missing', 'enum_last_member_value_changed',
    'evidence_required_missing', 'executable_stack', 'field_access_changed',
    'field_became_const', 'field_became_mutable', 'field_became_volatile', 'field_lost_const',
    'field_lost_mutable', 'field_lost_volatile', 'fortify_source_weakened',
    'frame_register_changed', 'func_became_inline', 'func_deleted', 'func_deleted_dwarf',
    'func_deleted_elf_fallback', 'func_likely_renamed', 'func_lost_inline',
    'func_noexcept_added', 'func_noexcept_removed', 'func_ref_qual_changed',
    'func_virtual_became_pure', 'func_virtual_removed', 'func_visibility_protected_changed',
    'generated_file_dependency_unstable', 'generated_header_changed',
    'generated_header_reaches_public_api', 'handle_type_changed', 'header_parse_context_drift',
    'hidden_friend_added', 'ifunc_introduced', 'ifunc_removed',
    'include_graph_public_header_drift', 'inline_body_changed', 'layer_coverage_asymmetric',
    'layout_unverifiable', 'libcpp_abi_version_changed', 'link_export_policy_changed',
    'mandatory_template_param_added', 'method_access_changed', 'odr_source_conflict',
    'opaque_invariant_broken', 'overload_added', 'overload_set_rerouted',
    'param_became_va_list', 'param_lost_va_list', 'param_renamed', 'param_restrict_changed',
    'pie_disabled', 'polymorphic_type_non_virtual_dtor', 'protected_visibility_changed',
    'public_api_exposes_stl_by_value', 'public_macro_value_changed',
    'public_reachability_changed', 'public_surface_grew', 'public_surface_shrank',
    'public_typedef_target_changed', 'removed_const_overload', 'return_pointer_level_changed',
    'rpath_changed', 'soname_bump_recommended', 'soname_bump_unnecessary', 'soname_changed',
    'source_binary_provenance_mismatch', 'source_decl_binary_symbol_mismatch',
    'source_level_kind_changed', 'source_to_binary_mapping_changed', 'standard_layout_lost',
    'std_reexport_removed', 'stdlib_implementation_changed', 'struct_alignment_changed',
    'struct_field_offset_changed', 'struct_field_removed', 'struct_field_type_changed',
    'sycl_backend_driver_req_changed', 'sycl_implementation_changed',
    'sycl_pi_entrypoint_added', 'sycl_pi_entrypoint_removed', 'sycl_pi_version_changed',
    'sycl_plugin_added', 'sycl_plugin_removed', 'sycl_plugin_search_path_changed',
    'sycl_runtime_version_changed', 'symbol_binding_changed', 'symbol_elf_visibility_changed',
    'symbol_leaked_from_dependency_changed', 'symbol_moved_version_node',
    'symbol_renamed_batch', 'symbol_size_changed_const_object', 'symbol_size_changed_internal',
    'symbol_type_changed', 'symbol_version_alias_changed', 'symbol_version_defined_added',
    'symbol_version_required_added', 'symbol_version_required_added_compat',
    'symbol_version_required_removed', 'tail_padding_reuse_changed', 'template_body_changed',
    'template_param_type_changed', 'template_return_type_changed', 'toolchain_version_changed',
    'trivially_copyable_lost', 'type_added', 'type_alignment_changed',
    'type_field_added_compatible', 'type_field_removed', 'type_field_type_changed',
    'type_lost_final', 'type_visibility_changed', 'typedef_version_sentinel',
    'undocumented_export_ratio_increased', 'uninstantiated_template_removed',
    'union_field_added', 'union_field_type_changed', 'unspecified_return_now_named',
    'var_access_changed', 'var_access_widened', 'var_lost_const', 'var_value_changed',
    'vector_abi_changed', 'version_script_missing', 'virtual_method_added', 'visibility_leak',
    'vptr_introduced', 'vtable_symbol_identity_changed', 'writable_executable_segment',
})


def test_no_new_unspecified_evidence_tier_kinds():
    """A ChangeKind not in the baseline must have a declared evidence tier.

    Set-based (not count-based): mapping a baselined kind shrinks the set and
    still passes, but a newly-added kind with no tier is not in the baseline and
    fails — closing the map-one/add-one hole.
    """
    gen = _load_gen()
    unspecified = {
        r["kind"] for r in gen.build_spec()
        if r["min_evidence"] == gen.UNSPECIFIED_TIER
    }
    new_unmapped = unspecified - UNSPECIFIED_TIER_BASELINE
    assert not new_unmapped, (
        f"ChangeKind(s) shipped without an evidence tier: {sorted(new_unmapped)}. "
        f"Add a tier in scripts/evidence_tiers.py (or, if intentionally tier-less, "
        f"add to UNSPECIFIED_TIER_BASELINE)."
    )
