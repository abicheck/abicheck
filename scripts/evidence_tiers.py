#!/usr/bin/env python3
"""Evidence-tier model for the abicheck example catalog.

abicheck reasons over *five sources of information* about a library, layered
from the least to the most that a release engineer can hand it. Each source is
labelled with the same ``L0``–``L4`` evidence-layer codes used across the
docs (see ``docs/concepts/evidence-and-detectability.md`` and
``docs/concepts/evidence-pack.md``):

================  =====================================  =========================
Source            Evidence layer                          abicheck input
================  =====================================  =========================
just binary       L0 — exported symbol table / linker    a stripped ``.so``/``.dll``
debug symbols     L1 — DWARF / PDB / BTF / CTF            a ``-g`` build, no headers
headers           L2 — public-header AST (castxml)        ``-H include/``
build data        L3 — compile DB / flags / target graph  ``-p build/``
sources           L4 — per-TU source ABI replay           an BuildSourcePack (ADR-030)
================  =====================================  =========================

This module is the **single source of truth** for *which evidence layer each
example case is designed to exercise* — i.e. the minimum source you must feed
abicheck before the case's break (or its correct no-change verdict) becomes
visible. ``benchmark_comparison.py --evidence-tiers`` consumes it to run the
catalog at each tier and ``examples/ground_truth.json`` stores the computed
``min_evidence`` per case; ``tests/test_evidence_tiers.py`` keeps the two in
sync. It is pure-stdlib and side-effect-free so it can be imported without a
compiler, castxml, or any external tool.

The per-kind tiers below are a **designed default**, not a guarantee: they
encode where each change is *intended* to first become visible. The empirical
truth is measured by ``--evidence-tiers``, whose drift report flags any case
whose real first-detection tier differs from the value here (that is how
``toolchain_flag_drift`` was corrected from L3 to L1 — compilers record their
flags in debug info, so a ``-g`` build sees the drift at L1). Treat a drift
warning as a prompt to re-examine the mapping, not as a test failure.
"""

from __future__ import annotations

from typing import Any

# Ordered tiers, weakest evidence first. The index is the comparison key.
TIER_ORDER: list[str] = ["L0", "L1", "L2", "L3", "L4"]

TIER_LABELS: dict[str, str] = {
    "L0": "binary only (exported symbols / linker metadata)",
    "L1": "binary + debug info (DWARF/PDB layout)",
    "L2": "binary + debug + public headers (castxml AST)",
    "L3": "+ build context (compile DB / flags)",
    "L4": "+ source ABI replay (BuildSourcePack)",
}


def tier_rank(tier: str) -> int:
    """Position of *tier* in :data:`TIER_ORDER` (lower = weaker evidence)."""
    return TIER_ORDER.index(tier)


# ── Per-ChangeKind primary evidence layer ────────────────────────────────────
# The layer at which a kind first becomes detectable. A case whose expected
# kinds span several layers inherits the *strongest* (highest-rank) layer: the
# whole break is only fully visible once every contributing kind is.
EVIDENCE_TIER_BY_KIND: dict[str, str] = {
    # ── L0: visible in the exported symbol table / linker metadata alone ──
    "func_removed": "L0",
    "func_removed_elf_only": "L0",
    "func_added": "L0",
    "var_added": "L0",
    "var_removed": "L0",
    "versioned_symbol_scheme_detected": "L0",  # bulk removed↔added churn in the export table
    "func_visibility_changed": "L0",
    "func_language_linkage_changed": "L0",
    "soname_missing": "L0",
    "macho_cpu_type_changed": "L0",
    "pe_forwarder_changed": "L0",
    "pe_machine_changed": "L0",
    "symbol_version_defined_removed": "L0",
    "symbol_size_changed": "L0",
    "symbol_binding_strengthened": "L0",
    "needed_removed": "L0",
    "needed_added": "L0",
    "runpath_changed": "L0",
    "relro_weakened": "L0",
    "stack_canary_removed": "L0",
    "executable_stack_removed": "L0",
    "symbol_version_node_removed": "L0",
    "glibcxx_dual_abi_flip_detected": "L0",
    "abi_tag_changed": "L0",
    "inline_namespace_moved": "L0",
    "inline_namespace_version_bumped": "L0",
    "tag_type_renamed": "L0",
    "cpu_dispatch_isa_dropped": "L0",
    "sycl_overload_set_removed": "L0",
    "experimental_graduated": "L0",
    "experimental_removed_without_replacement": "L0",
    "bundle_intra_dep_removed": "L0",
    "bundle_intra_dep_signature_changed": "L0",
    "bundle_manifest_instantiation_removed": "L0",
    "bundle_provider_changed": "L0",
    "bundle_soname_skew": "L0",
    # ── L1: needs debug info (layout, offsets, sizes, enum values, calling conv) ──
    "struct_size_changed": "L1",
    "struct_packing_changed": "L1",
    "type_size_changed": "L1",
    "type_field_offset_changed": "L1",
    "type_field_added": "L1",
    "type_base_changed": "L1",
    "type_kind_changed": "L1",
    "type_vtable_changed": "L1",
    "type_removed": "L1",
    "typedef_base_changed": "L1",
    "typedef_removed": "L1",
    "union_field_removed": "L1",
    "field_bitfield_changed": "L1",
    "field_renamed": "L1",
    "flexible_array_member_changed": "L1",
    "enum_member_value_changed": "L1",
    "enum_member_removed": "L1",
    "enum_member_added": "L1",
    "enum_underlying_size_changed": "L1",
    "enum_member_renamed": "L1",
    "calling_convention_changed": "L1",
    "tls_var_size_changed": "L1",
    "var_became_const": "L1",
    "var_type_changed": "L1",
    "func_cv_changed": "L1",
    "func_static_changed": "L1",
    "func_params_changed": "L1",
    "func_return_changed": "L1",
    "param_pointer_level_changed": "L1",
    "atomic_qualifier_changed": "L1",
    "char8t_migration": "L1",
    "bit_int_width_changed": "L1",
    "value_abi_trait_changed": "L1",
    "struct_return_convention_changed": "L1",
    "integer_model_changed": "L1",
    "type_became_opaque": "L1",
    "func_virtual_added": "L1",
    "func_pure_virtual_added": "L1",
    "used_reserved_field": "L1",
    # Build flags are recorded redundantly in debug info (DW_AT_producer /
    # .GCC.command.line), so a `-g` build exposes toolchain flag drift at L1 — a
    # compile DB (L3) is only required when debug info is stripped/absent.
    # Empirically confirmed by `benchmark_comparison.py --evidence-tiers`.
    "toolchain_flag_drift": "L1",
    # ── L2: needs the public-header AST (source-only API, scoping, decls) ──
    "ctor_explicit_added": "L2",
    "type_became_final": "L2",
    "hidden_friend_removed": "L2",
    "default_template_arg_changed": "L2",
    "cpo_kind_changed": "L2",
    "instantiation_missing_from_binary": "L2",
    "serialization_tag_changed": "L2",
    "internal_type_leaks_via_public_api": "L2",
    "internal_template_leaks_via_public_api": "L2",
    "inline_body_references_renamed_member": "L2",
    "constant_changed": "L2",
    "param_default_value_changed": "L2",
    "param_default_value_removed": "L2",
    # ── L3: build-system context (compile DB) uniquely required ──
    # The dedicated L3 build-evidence kinds (abi_relevant_build_flag_changed,
    # toolchain_version_changed, link_export_policy_changed, …) are produced by
    # the BuildSourcePack build diff (ADR-029). The runtime-model flips below are
    # only proven from the captured build options (a flag flip with no necessary
    # binary footprint), so they are genuinely L3.
    "exceptions_mode_changed": "L3",
    "rtti_mode_changed": "L3",
    "tls_model_changed": "L3",
    "threadsafe_statics_mode_changed": "L3",
}

# Cases with no ``expected_kinds`` (NO_CHANGE baselines, scoped-internal cases,
# and breaks whose detector predates per-kind ground truth) get an explicit
# layer: the minimum evidence at which abicheck reaches the *correct* verdict —
# which for the scoped NO_CHANGE cases means the header scoping that *prevents*
# a false positive.
KINDLESS_CASE_TIER: dict[str, str] = {
    "case04_no_change": "L0",
    "case13_symbol_versioning": "L0",
    "case14_cpp_class_size": "L1",
    "case15_noexcept_change": "L2",
    "case16_inline_to_non_inline": "L0",
    "case17_template_abi": "L1",
    "case18_dependency_leak": "L1",
    "case26_union_field_added": "L1",
    "case27_symbol_binding_weakened": "L0",
    "case29_ifunc_transition": "L0",
    "case30_field_qualifiers": "L1",
    "case34_access_level": "L2",
    "case36_anon_struct": "L1",
    "case38_virtual_methods": "L1",
    "case40_field_layout": "L1",
    "case41_type_changes": "L1",
    "case42_type_alignment_changed": "L1",
    "case26b_union_field_added_compatible": "L1",
    "case43_base_class_member_added": "L1",
    "case44_cyclic_type_member_added": "L1",
    "case45_multi_dim_array_change": "L1",
    "case46_pointer_chain_type_change": "L1",
    "case47_inline_to_outlined": "L0",
    "case48_leaf_struct_through_pointer": "L1",
    "case49_executable_stack": "L0",
    "case50_soname_inconsistent": "L0",
    "case51_protected_visibility": "L0",
    "case52_rpath_leak": "L0",
    "case60_base_class_position_changed": "L1",
    "case98_cxx_standard_floor_raised": "L0",
    "case105_concept_tightening": "L2",
    "case118_internal_struct_field_added_scoped": "L2",
    "case119_internal_struct_field_removed_scoped": "L2",
    "case120_internal_struct_reordered_scoped": "L2",
    # Documented gap (ADR-026): an uninstantiated template signature change is
    # invisible to every artifact layer; only source replay (L4) would see it.
    "case122_template_signature_uninstantiated": "L4",
}


def compute_min_evidence(case_name: str, info: dict[str, Any]) -> str:
    """Return the minimum evidence layer (``L0``..``L4``) for one case.

    The value is the strongest layer among the case's expected kinds, or the
    explicit :data:`KINDLESS_CASE_TIER` entry when the case declares no kinds.
    Raises ``KeyError`` if a kind or kind-less case is unmapped, so a new case
    cannot be added silently without an evidence-tier decision.
    """
    kinds = list(info.get("expected_kinds", [])) + list(
        info.get("expected_bundle_kinds", [])
    )
    if not kinds:
        if case_name not in KINDLESS_CASE_TIER:
            raise KeyError(f"no evidence tier mapped for kind-less case {case_name!r}")
        return KINDLESS_CASE_TIER[case_name]
    tiers = []
    for kind in kinds:
        if kind not in EVIDENCE_TIER_BY_KIND:
            raise KeyError(f"no evidence tier mapped for ChangeKind {kind!r}")
        tiers.append(EVIDENCE_TIER_BY_KIND[kind])
    return max(tiers, key=tier_rank)


def min_evidence_for_ground_truth(verdicts: dict[str, Any]) -> dict[str, str]:
    """Compute ``{case: min_evidence}`` for a ground_truth ``verdicts`` map."""
    return {case: compute_min_evidence(case, info) for case, info in verdicts.items()}


# Verdicts a tier emits *by default when it finds nothing*. A matching quiet
# verdict alone does not prove a tier discovered a change.
QUIET_VERDICTS = frozenset({"NO_CHANGE", "COMPATIBLE", "COMPATIBLE_WITH_RISK"})


def detected_at(
    tier_verdicts: dict[str, str],
    tier_kinds: dict[str, list[str]],
    expected: str,
    expected_kinds: list[str],
    min_evidence: str,
) -> str | None:
    """First tier (weakest evidence) that actually *discovers* a case.

    A tier qualifies when its verdict matches *expected*, plus one of two guards
    against crediting a tier that merely returned a quiet default:

    - **Kinded quiet cases** (``expected_kinds`` set, quiet verdict): the tier
      must have emitted every cataloged kind — a bare COMPATIBLE/NO_CHANGE for
      unrelated reasons is not enough.
    - **Kind-less quiet cases** (no ``expected_kinds``, quiet verdict): the tier
      must be at least the case's designed ``min_evidence``. Below that, a
      matching quiet verdict is the "found nothing yet" default, not a
      discovery (e.g. an L4-only invisible change still returns NO_CHANGE at L0).

    Active ``BREAKING``/``API_BREAK`` verdicts are genuine findings, so a verdict
    match suffices — and the empirical tier is left free to fall *below* the
    declared ``min_evidence`` so the drift report can flag a too-conservative map.
    Iterates the tiers present in *tier_verdicts*, weakest first.
    """
    want = set(expected_kinds)
    quiet = expected in QUIET_VERDICTS
    require_kinds = bool(want) and quiet
    floor = (
        tier_rank(min_evidence)
        if (quiet and not want and min_evidence in TIER_ORDER)
        else 0
    )
    for tier in TIER_ORDER:
        if tier not in tier_verdicts:
            continue
        if tier_rank(tier) < floor:
            continue
        if tier_verdicts.get(tier) != expected:
            continue
        if require_kinds and not want.issubset(set(tier_kinds.get(tier, []))):
            continue
        return tier
    return None
