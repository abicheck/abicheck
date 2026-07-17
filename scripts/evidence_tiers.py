#!/usr/bin/env python3
"""Evidence-tier model for the abicheck example catalog.

abicheck reasons over *six sources of information* about a library, layered
from the least to the most that a release engineer can hand it. Each source is
labelled with the same ``L0``–``L5`` evidence-layer codes used across the
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
source graph      L5 — decl-dependency / call edges       the L5 graph in the pack (ADR-031)
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
TIER_ORDER: list[str] = ["L0", "L1", "L2", "L3", "L4", "L5"]

# Sentinel for a case whose canonical verdict is proven by an independent
# oracle (e.g. a source_smoke) but which no evidence tier L0-L5 currently
# reaches — a real, tracked detector gap, not a floor a stronger source would
# clear. Deliberately outside TIER_ORDER: it is not comparable via tier_rank,
# and callers that gate on "min_evidence in TIER_ORDER" (see detected_at)
# already skip the comparison for it. ground_truth.json marks these cases
# with `"detectability": "none"`; do not hand-set this value in
# EVIDENCE_TIER_BY_KIND or KINDLESS_CASE_TIER.
UNDETECTABLE: str = "none"

TIER_LABELS: dict[str, str] = {
    "L0": "binary only (exported symbols / linker metadata)",
    "L1": "binary + debug info (DWARF/PDB layout)",
    "L2": "binary + debug + public headers (castxml AST)",
    "L3": "+ build context (compile DB / flags)",
    "L4": "+ source ABI replay (BuildSourcePack)",
    "L5": "+ source graph (decl-dependency / call edges)",
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
    # Binary-only C++ layout: the _ZTV / _ZTI object sizes encode vtable slot
    # count and inheritance shape, readable from .dynsym without DWARF/headers.
    "vtable_slot_count_changed": "L0",
    "rtti_inheritance_changed": "L0",
    # CPython extension modules: the import table (undefined Py* symbols) and the
    # PyInit_* export are both readable from the binary alone — no debug info or
    # headers needed to see the stable-ABI contract (G14).
    "python_stable_abi_violation": "L0",
    "python_abi3_dropped": "L0",
    "python_gil_abi_changed": "L0",
    "python_abi3_floor_raised": "L0",
    # G23 Phase B1 — Itanium thunk / VTT surface, from .dynsym names/sizes alone.
    "vtable_thunk_offset_changed": "L0",
    "vtable_thunk_set_changed": "L0",
    "vtt_slot_count_changed": "L0",
    # G23 Phase B2 — L1 DWARF vtable-group reconstruction.
    "secondary_vtable_group_changed": "L1",
    "virtual_base_offset_changed": "L1",
    # Catalog batch case165–169. Virtuality of *inheritance* (DW_AT_virtuality
    # on the base DIE) and record vtables are visible from a -g build, and the
    # ADR-027 anti-pattern needs the same vtable + factory-return info — L1.
    # A *member function's* virtuality flip, however, is not: the headerless
    # dump builds symbol-only Function records (no is_virtual), so
    # FUNC_VIRTUAL_REMOVED needs the header AST; L1 alone sees only the
    # generic vtable-layout fallout. The &/&& ref-qualifier is likewise
    # recorded only in the header AST (the binary just shows the renamed
    # symbol). overload_added groups by qualified name parsed structurally
    # from the mangled export table alone.
    "func_virtual_removed": "L2",
    "base_class_virtual_changed": "L1",
    "polymorphic_type_non_virtual_dtor": "L1",
    "func_ref_qual_changed": "L2",
    # is_explicit (like the ref-qualifier above) is castxml header-AST-only —
    # DWARF carries no equivalent attribute — so the ambiguity heuristic that
    # reads it needs L2.
    "ctor_overload_ambiguity_risk": "L2",
    "overload_added": "L0",
    # G23 Phase D — ecosystem detectors (all read symbol-level manifests / names).
    "unnamed_type_in_public_abi": "L0",  # exported mangled symbol names
    "long_double_abi_changed": "L0",  # Itanium long-double mangling token
    "kabi_symbol_removed": "L0",  # Module.symvers manifest
    "kabi_crc_changed": "L0",
    "kabi_symbol_namespace_changed": "L0",
    "kabi_export_type_changed": "L0",
    "kabi_symbol_added": "L0",
    # G23 Phase A — Linux ELF artifact facts. All read purely from the dynamic
    # section, symbol table, ELF header, or .note.gnu.property — no DWARF/headers.
    "static_tls_introduced": "L0",
    "static_tls_removed": "L0",
    "cet_protection_weakened": "L0",
    "branch_protection_weakened": "L0",
    "cet_protection_improved": "L0",
    "branch_protection_improved": "L0",
    "elf_machine_changed": "L0",
    "elf_class_changed": "L0",
    "elf_abi_flags_changed": "L0",
    "elf_osabi_changed": "L0",
    "symbol_binding_became_unique": "L0",
    "symbol_binding_lost_unique": "L0",
    # Toolchain / runtime environment drift (binutils & glibc skew). The
    # verneed roll-up and the linker-artifact facts are pure dynamic-section /
    # section-header reads; the time64/LFS flip needs typedef evidence
    # (DWARF at minimum) to see the underlying width change.
    "runtime_floor_raised": "L0",
    "platform_baseline_floor_raised": "L0",
    "symbol_version_required_added": "L0",
    "symbol_version_defined_added": "L0",
    "dt_relr_introduced": "L0",
    "dt_relr_removed": "L0",
    # Wheel tag / deployment-claim checks (G27) — ELF verneed and Mach-O
    # load-command reads, same evidence class as platform_baseline_floor_raised.
    "musllinux_glibc_dependency_detected": "L0",
    "macos_deployment_target_raised": "L0",
    "wheel_tag_architecture_mismatch": "L0",
    "wheel_rpath_not_portable": "L0",
    "wheel_closure_dependency_violation": "L0",
    # NumPy C-API compatibility envelope (G26) — pure binary-evidence
    # (rodata string) scans, no header/source/DWARF needed.
    "numpy_capi_consumption_added": "L0",
    "numpy_capi_consumption_removed": "L0",
    "numpy_target_floor_raised": "L0",
    "numpy_metadata_understates_required_version": "L0",
    "numpy_abi_major_incompatible": "L0",
    "rpath_type_changed": "L0",
    "hash_style_removed": "L0",
    "time64_abi_changed": "L1",
    # Composition compatibility (Wave A). Runtime-binding rebound and ordered
    # DT_NEEDED/DF_SYMBOLIC/DF_TEXTREL facts are all read from the dynamic
    # section / symbol tables of the (possibly multiple) resolved DSOs alone.
    "runtime_symbol_provider_changed": "L0",
    "runtime_weak_resolution_changed": "L0",
    "needed_order_changed": "L0",
    "symbolic_binding_mode_changed": "L0",
    "text_relocation_introduced": "L0",
    "text_relocation_removed": "L0",
    # PE ordinal/import-table facts — read from the export/import directories
    # alone, no debug info or headers needed.
    "pe_ordinal_retargeted": "L0",
    "pe_import_load_mode_changed": "L0",
    # wchar_t model drift is read from DW_AT_producer, like toolchain_flag_drift.
    "wchar_model_changed": "L1",
    # Canonical kinds that older catalog rows previously left implicit.
    "union_field_added": "L1",
    "symbol_binding_changed": "L0",
    "ifunc_introduced": "L0",
    "type_field_type_changed": "L1",
    "method_access_changed": "L2",
    "enum_last_member_value_changed": "L1",
    "type_alignment_changed": "L1",
    "soname_changed": "L0",
    "symbol_elf_visibility_changed": "L0",
    "base_class_position_changed": "L1",
    # Python-level API of an extension module (G23): recovered from a `.pyi`
    # type stub — a declared-API surface analogous to public headers, and like
    # headers invisible in the binary/debug info. The `.so` export table shows
    # only `PyInit_*`, so these signature changes need the header-equivalent L2
    # stub source to be seen at all.
    "python_api_function_removed": "L2",
    "python_api_function_added": "L2",
    "python_api_class_removed": "L2",
    "python_api_class_added": "L2",
    "python_api_method_removed": "L2",
    "python_api_method_added": "L2",
    "python_api_parameter_removed": "L2",
    "python_api_parameter_added": "L2",
    "python_api_parameter_renamed": "L2",
    "python_api_default_removed": "L2",
    "python_api_parameter_type_changed": "L2",
    "python_api_return_type_changed": "L2",
    "python_api_parameter_kind_changed": "L2",
    "python_api_callable_kind_changed": "L2",
    "python_api_overload_removed": "L2",
    "python_api_stub_invalid": "L2",
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
    "suppression_would_hide_public_break": "L1",  # ADR-044: needs struct/field layout (internal_leak.compute_leak_paths) to judge public reachability
    "struct_size_changed": "L1",
    "struct_packing_changed": "L1",
    "type_size_changed": "L1",
    "type_field_offset_changed": "L1",
    "type_field_added": "L1",
    # Same DWARF-layout evidence as type_field_added; distinguished only by
    # severity (compatible append vs. breaking), not by evidence source.
    "type_field_added_compatible": "L1",
    "type_base_changed": "L1",
    # Fine-grained class-layout descriptor: a base subobject moving (e.g. an
    # empty-base optimization lost) is read from DWARF DW_TAG_inheritance
    # offsets, or from the castxml record layout when headers are supplied.
    "base_class_offset_changed": "L1",
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
    # Same is_virtual/is_pure_virtual DWARF comparison as func_pure_virtual_added
    # (diff_types.py); only the old function's is_virtual value picks which of
    # the two kinds fires, not the evidence source.
    "func_virtual_became_pure": "L1",
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
    # ── L2: CastXML schema-completeness (all castxml/header-only facts) ──
    "field_default_initializer_removed": "L2",
    "field_default_initializer_changed": "L2",
    "type_became_abstract": "L2",
    "type_lost_abstract": "L2",
    "enum_became_scoped": "L2",
    "enum_lost_scoped": "L2",
    "func_override_specifier_added": "L2",
    "func_override_specifier_removed": "L2",
    "func_deprecated_added": "L2",
    "func_deprecated_removed": "L2",
    "var_deprecated_added": "L2",
    "var_deprecated_removed": "L2",
    "type_deprecated_added": "L2",
    "type_deprecated_removed": "L2",
    "enum_deprecated_added": "L2",
    "enum_deprecated_removed": "L2",
    "field_deprecated_added": "L2",
    "field_deprecated_removed": "L2",
    # ── L2: ADR-035 D4 cross-source validation that needs binary exports ↔
    # header decls ↔ header provenance (no compile DB) ──
    "exported_not_public": "L2",
    "public_not_exported": "L2",
    "private_header_leak": "L2",
    # ── L3: build-system context (compile DB) uniquely required ──
    # The dedicated L3 build-evidence kinds (abi_relevant_build_flag_changed,
    # toolchain_version_changed, link_export_policy_changed, …) are produced by
    # the BuildSourcePack build diff (ADR-029). The runtime-model flips below are
    # only proven from the captured build options (a flag flip with no necessary
    # binary footprint), so they are genuinely L3.
    "abi_relevant_build_flag_changed": "L3",
    "exceptions_mode_changed": "L3",
    "rtti_mode_changed": "L3",
    "tls_model_changed": "L3",
    "threadsafe_statics_mode_changed": "L3",
    # Language-agnostic layout/codegen flag flips (ADR-028 L3 follow-up): each is
    # proven only from the captured build options, not from any artifact.
    "enum_size_flag_changed": "L3",
    "struct_packing_mode_changed": "L3",
    "lto_mode_changed": "L3",
    "char_signedness_changed": "L3",
    "whole_program_vtables_mode_changed": "L3",
    "sanitizer_mode_changed": "L3",
    "float_abi_changed": "L3",
    "stdlib_debug_mode_changed": "L3",
    # ADR-035 D4 cross-source check that compares L2 header context against the
    # L3 build flags — only visible once the build evidence is present.
    "header_build_context_mismatch": "L3",
    # ── L4: ADR-035 D4 cross-source checks that read the source-replay surface /
    # source graph carried in a BuildSourcePack (no artifact layer sees them) ──
    # odr_type_variant reads the L4 surface's recorded per-TU ODR conflicts.
    "odr_type_variant": "L4",
    # Source-replay removals / constexpr body change (ADR-030 L4): a removed
    # macro/inline/typedef leaves no artifact footprint, and a constexpr function
    # body change alters only compile-time evaluation — all L4-only.
    "public_macro_removed": "L4",
    "inline_function_removed": "L4",
    "public_typedef_removed": "L4",
    "concept_tightened": "L4",
    # Fact-set/coverage compatibility of the L4 evidence itself (ADR-038 C.8):
    # only meaningful once L4 source-fact records carry fact_set/coverage.
    "source_fact_coverage_incomplete": "L4",
    # An uninstantiated function template's body/signature change (ADR-026):
    # no symbol is ever emitted and castxml doesn't parse the body, so only
    # L4 source-ABI replay observes it (see case122's known_gap).
    "template_body_changed": "L4",
    # ── L5: needs the L5 source graph's decl-dependency edges (the check skips
    # cleanly when no call-graph pass populated the graph), so its minimum
    # evidence is the graph tier, not the L4 replay surface that carries it. ──
    "public_to_internal_dependency": "L5",
    # Version-over-version source-graph deltas (ADR-031 L5): a public entry newly
    # reaching an internal decl, a new inter-target dependency, or an exported
    # symbol's owning source moving are all derived from the L5 graph.
    "public_api_internal_dependency_added": "L5",
    "target_dependency_added": "L5",
    "exported_symbol_source_owner_changed": "L5",
    # ── ADR-035 D8 single-release hygiene audit ──
    # unversioned_exported_symbol is pure ELF (export table vs .gnu.version_d);
    # rtti_for_internal_type needs header provenance to know a type is internal.
    "unversioned_exported_symbol": "L0",
    "rtti_for_internal_type": "L2",
    # identity_collision_detected reads the L4 surface's recorded USR collisions
    # (ADR-041 P1 #5) — same evidence source as odr_type_variant.
    "identity_collision_detected": "L4",
    # ── Coverage extension: dynamic-loader / platform metadata (all read from
    # the binary's headers, dynamic section, notes, or symbol tables alone). ──
    "imported_symbol_added": "L0",
    "imported_symbol_removed": "L0",
    "interpreter_changed": "L0",
    "bind_now_disabled": "L0",
    "elf_endianness_changed": "L0",
    "x86_isa_baseline_raised": "L0",
    "os_deployment_floor_raised": "L0",
    "dynamic_loading_flags_changed": "L0",
    "exported_object_alignment_reduced": "L0",
    "elf_init_fini_changed": "L0",
    "allocator_replacement_added": "L0",
    "allocator_replacement_removed": "L0",
    "pe_hardening_weakened": "L0",
    "pe_hardening_improved": "L0",
    "library_version_downgraded": "L0",
    "macho_filetype_changed": "L0",
    "macho_linkage_flags_changed": "L0",
    "macho_reexport_changed": "L0",
    # ── Coverage extension: language contracts, extracted by the header AST
    # dumpers (castxml/clang) — first visible at the header tier. ──
    "func_variadic_added": "L2",
    "func_variadic_removed": "L2",
    "func_contract_attribute_added": "L2",
    "func_contract_attribute_removed": "L2",
    "var_alignment_changed": "L2",
    "func_exception_spec_changed": "L2",
}

# Cases with no ``expected_kinds`` (NO_CHANGE baselines, scoped-internal cases,
# and breaks whose detector predates per-kind ground truth) get an explicit
# layer: the minimum evidence at which abicheck reaches the *correct* verdict —
# which for the scoped NO_CHANGE cases means the header scoping that *prevents*
# a false positive.
KINDLESS_CASE_TIER: dict[str, str] = {
    "case04_no_change": "L0",
    "case118_internal_struct_field_added_scoped": "L2",
    "case119_internal_struct_field_removed_scoped": "L2",
    "case120_internal_struct_reordered_scoped": "L2",
    # ADR-039: a context-free header parse false-positives a #ifdef-guarded
    # field; the binary is blind (identical builds) and only build context (the
    # active -D defines) clears the phantom via --reconcile-build-context.
    "case164_preproc_conditional_field": "L3",
    # An internal enum's value change is only visible via DWARF (L1), but
    # proving it's confined to a private-header origin (so it can be scoped
    # out instead of reported) needs the header AST (L2).
    "case184_internal_enum_churn_scoped": "L2",
    # The pointee-const suppression is a header-AST-level type comparison
    # (cv_qualifiers_only_differ); DWARF alone doesn't carry the distinction
    # abicheck relies on here.
    "case186_c_api_pointee_const_abi_neutral": "L2",
}


def compute_min_evidence(case_name: str, info: dict[str, Any]) -> str:
    """Return the minimum evidence layer (``L0``..``L5``) for one case.

    The value is the strongest layer among the case's expected kinds, or the
    explicit :data:`KINDLESS_CASE_TIER` entry when the case declares no kinds.
    Raises ``KeyError`` if a kind or kind-less case is unmapped, so a new case
    cannot be added silently without an evidence-tier decision.

    Audit/cross-check cases use the same canonical ``expected_kinds`` field as
    ordinary comparisons. Their workflow differs; their truth does not. Those
    kinds are mapped in :data:`EVIDENCE_TIER_BY_KIND`, so their tier is derived
    in exactly the same way.

    A case marked ``"detectability": "none"`` in ground_truth.json returns
    :data:`UNDETECTABLE` regardless of its ``expected_kinds``: those kinds are
    the tool's actual (insufficient) observation under the known gap, not a
    calibration target that proves the canonical verdict, so they must not be
    read as an ordinary L0-L5 floor (see case111).
    """
    if info.get("detectability") == "none":
        return UNDETECTABLE
    kinds = list(info.get("expected_kinds", []))
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
