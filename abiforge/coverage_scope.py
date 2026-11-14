"""The edit corpus's ChangeKind scope: which kinds it can show, and why not the rest.

``SCOPE_BUCKETS`` partitions every ``ChangeKind`` into named buckets, in or
out of the corpus's scope, each with its reason (``abiforge.coverage`` reads
it for the denominator and every report). Split out of ``abiforge.coverage``,
which owns the measurement: this module owns only the scope decision.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "HEADER_VISIBLE_SOURCE_KINDS",
    "SCOPE_BUCKETS",
    "SCOPE_BUCKET_BY_ID",
    "ScopeBucket",
]


@dataclass(frozen=True)
class ScopeBucket:
    """One named group of ChangeKinds, in or out of the corpus's scope, and why.

    A *named* bucket claims the kinds whose value starts with one of its
    ``prefixes`` or is one of its ``names``; named buckets must not overlap
    (``test_every_kind_is_in_exactly_one_scope_bucket``). A *module* bucket
    takes every kind its taxonomy ``module`` declares that no named bucket
    claims, so a platform- or ecosystem-specific kind is excluded by what it
    is about, wherever the catalog filed it.
    """

    id: str
    in_scope: bool
    reason: str
    prefixes: tuple[str, ...] = ()
    names: frozenset[str] = frozenset()
    module: str | None = None

    @property
    def named(self) -> bool:
        return self.module is None

    def claims(self, value: str) -> bool:
        return value in self.names or value.startswith(self.prefixes or ("\0",))


_ELF_ONE_LIBRARY = "one Linux ELF shared library with DWARF and public headers shows it"

# The corpus builds one Linux ELF .so per side, from C or C++, and gives
# abicheck at most binary + DWARF + public headers.
SCOPE_BUCKETS: tuple[ScopeBucket, ...] = (
    # --- named, out of scope
    ScopeBucket(
        "pe_coff", False, "PE/COFF only; the corpus targets Linux ELF", ("pe_",)
    ),
    ScopeBucket(
        "apple",
        False,
        "Mach-O / Apple deployment only; the corpus targets Linux ELF",
        ("macho_",),
        frozenset(
            {
                "compat_version_changed",
                "os_deployment_floor_raised",
                "macos_deployment_target_raised",
            }
        ),
    ),
    ScopeBucket(
        "python_packaging",
        False,
        "Python, NumPy C-API and wheel packaging; the corpus builds native libraries",
        ("python_", "numpy_", "wheel_", "musllinux_"),
    ),
    ScopeBucket(
        "sycl",
        False,
        "SYCL runtime/plugin metadata; the corpus builds plain C/C++",
        ("sycl_",),
    ),
    ScopeBucket(
        "kernel_abi",
        False,
        "Linux kernel module ABI (Module.symvers), not a userspace .so",
        ("kabi_",),
    ),
    ScopeBucket(
        "multi_library",
        False,
        "multi-library bundle scope (intra-bundle dependencies, providers, "
        "SONAME skew, manifests): reachable by a bundle seed building several "
        "libraries per side, which is not modeled yet; a corpus cell is one library",
        ("bundle_",),
    ),
    ScopeBucket(
        "needs_source_graph",
        False,
        "needs the L5 source graph (call/reference edges from public entries), "
        "though the catalog files it under symbols",
        names=frozenset({"internal_symbol_required_by_public_api"}),
    ),
    ScopeBucket(
        "no_extractor_fact",
        False,
        "no extractor records the fact the detector reads, so no edit of a real "
        "library reaches it: every backend and DWARF flatten an anonymous "
        "member into its parent's fields",
        names=frozenset({"anon_field_changed"}),
    ),
    ScopeBucket(
        "non_x86_build_mode",
        False,
        "-mfloat-abi is an ARM option: no x86/x86-64 build has a float ABI to "
        "change, and the corpus's toolchains target x86",
        names=frozenset({"float_abi_changed"}),
    ),
    # --- named, out of scope: L3 build kinds the compile DB a build_data
    # config gives (one compile command per side) cannot reach.
    ScopeBucket(
        "l3_link_facts",
        False,
        "read from L3 link units or build-system target metadata (version "
        "script / SONAME per link target, generator graph, several compile "
        "units per target); a compile_commands.json records compile commands "
        "only, and a corpus library is one translation unit",
        names=frozenset(
            {
                "compile_context_conflict",
                "generated_file_dependency_unstable",
                "link_export_policy_changed",
            }
        ),
    ),
    ScopeBucket(
        "l3_non_abi_or_toolchain_identity",
        False,
        "a change of non-ABI build metadata (include order, output paths) or "
        "of the toolchain identity (--target / --sysroot): two builds of one "
        "corpus library differ in neither without changing the toolchain the "
        "cell is defined by",
        names=frozenset({"build_context_changed", "toolchain_version_changed"}),
    ),
    ScopeBucket(
        "l2_l3_context_reconciliation",
        False,
        "a disagreement between the header parse and the build's own context "
        "(or DWARF): the harness parses headers with exactly the build's "
        "context by construction, so a correct cell never reaches it",
        names=frozenset(
            {
                "header_binary_context_mismatch",
                "header_build_context_mismatch",
                "header_parse_context_drift",
            }
        ),
    ),
    ScopeBucket(
        "release_inventory_or_policy",
        False,
        "judged against a release inventory, a declared support promise, a "
        "standard floor from a build configuration matrix or a documented "
        "default: inputs outside two builds of one library",
        names=frozenset(
            {
                "behavioural_default_changed",
                "cxx_standard_floor_raised",
                "support_promise_component_introduced",
                "support_promise_component_retired",
            }
        ),
    ),
    ScopeBucket(
        "asymmetric_evidence",
        False,
        "the two sides were analyzed with different evidence; every corpus "
        "cell gives both sides one evidence level (an asymmetric-evidence "
        "axis is not modeled)",
        names=frozenset(
            {"layer_coverage_asymmetric", "source_fact_coverage_incomplete"}
        ),
    ),
    # --- named, out of scope: L4/L5 source kinds two builds of one corpus
    # library with sources (a ``sources`` config) cannot reach.
    ScopeBucket(
        "source_replay_twin",
        False,
        "a source-replay copy of a header-tier finding: a sources config always "
        "carries headers, and compare drops the replay twin for the header-tier "
        "kind that names the same entity (compare/source_replay_twins.REPLAY_TWINS: "
        "CONSTANT_CHANGED, TYPEDEF_REMOVED, FUNC_REMOVED/visibility); only a "
        "headerless source-only dump, which the corpus does not model, keeps it",
        names=frozenset(
            {
                "constexpr_value_changed",
                "public_typedef_removed",
            }
        ),
    ),
    ScopeBucket(
        "comparison_policy_input",
        False,
        "decided against an input the corpus never gives compare: a policy "
        "file's require_evidence, a suppression file, or a probe environment "
        "matrix (several configurations of one version)",
        names=frozenset(
            {
                "api_depends_on_consumer_env",
                "evidence_required_missing",
                "suppression_reachability_unknown",
                "suppression_would_hide_public_break",
            }
        ),
    ),
    ScopeBucket(
        "build_target_graph",
        False,
        "read from build-system target metadata (CMake file API, Bazel, ...): "
        "target public headers (TARGET_HAS_PUBLIC_HEADER), target-to-target "
        "and dependency-role edges, a compile unit's target (option-to-symbol "
        "edges) and declared generated files; a compile_commands.json, all a "
        "corpus build writes, has none of them",
        names=frozenset(
            {
                "build_option_reaches_public_symbol",
                "generated_header_changed",
                "generated_header_reaches_public_api",
                "include_graph_public_header_drift",
                "public_dependency_role_changed",
                "public_dependency_role_unknown",
                "target_dependency_added",
            }
        ),
    ),
    ScopeBucket(
        "multi_translation_unit",
        False,
        "an exported symbol's declaring file moving between two installed "
        "headers: the dump is given one public header per seed, and moving a "
        "declaration into the private header reads as exported_not_public "
        "instead (verified on a probe of that shape)",
        names=frozenset({"exported_symbol_source_owner_changed"}),
    ),
    ScopeBucket(
        "private_header_provenance",
        False,
        "not reached on the private-header seed (cxx_tree), which reaches "
        "their siblings: a public record naming a private-header type reads as "
        "internal_type_leaks_via_public_api, not private_header_leak, and an "
        "identity collision needs two entities sharing one canonical id",
        names=frozenset({"identity_collision_detected", "private_header_leak"}),
    ),
    ScopeBucket(
        "source_artifact_coherence",
        False,
        "a disagreement between the source tree and the binary built from it "
        "(a stale or foreign DSO): the harness compiles the DSO from exactly "
        "the tree it hands over, so a correct cell never reaches it",
        names=frozenset(
            {"source_binary_provenance_mismatch", "source_surface_dso_mismatch"}
        ),
    ),
    ScopeBucket(
        "source_graph_reconciliation",
        False,
        "a renamed exported function is deliberately left unpaired: its "
        "canonical identity is its mangled name, and ADR-048's structural tier "
        "does not resolve a rename that also changes the node's position (its "
        "own symbol neighbour is renamed with it) -- the conservative "
        "true-add/true-remove; pairing on position alone was tried and paired "
        "an unrelated removed and added function as 'renamed', since a "
        "source_decl node carries no signature to tell them apart. A type "
        "rename does reconcile (declaration_renamed, and "
        "public_api_internal_type_renamed for a private-header target), but "
        "the header tier rates renaming the type a public record points to "
        "BREAKING (struct_field_type_changed), stricter than the edit's ground "
        "truth (a source break: the record's layout and every symbol are "
        "unchanged), so no operator asserts it until that is settled; moved "
        "and the identity_* outcomes need an id that changes with its name "
        "and file kept, or a second installed header",
        names=frozenset(
            {
                "declaration_identity_reconciled",
                "declaration_identity_reconciled_unresolved",
                "declaration_identity_unchanged",
                "declaration_moved",
                "declaration_renamed",
                "public_api_internal_type_renamed",
            }
        ),
    ),
    ScopeBucket(
        "source_surface_boundary",
        False,
        "the L4/L5 surface records only declarations a public header reaches: "
        "an existing export that gains or loses its header declaration is "
        "absent from one side's graph, so it reads as an addition/removal "
        "(FUNC_ADDED, PUBLIC_SURFACE_GREW), never as a persisting declaration "
        "crossing the public boundary (verified on two builds)",
        names=frozenset({"public_reachability_changed"}),
    ),
    ScopeBucket(
        "consumer_or_runtime_stack",
        False,
        "judged against consumer artifacts given to compare (--consumer) or the "
        "loaded dependency stack (which library provides a symbol, weak "
        "resolution across libraries); corpus consumers feed only the "
        "execution oracle, and a cell builds one library",
        names=frozenset(
            {
                "consumer_required_symbol_removed",
                "runtime_symbol_provider_changed",
                "runtime_weak_resolution_changed",
            }
        ),
    ),
    # --- named, in scope: L4/L5 source kinds a sources config reaches
    # (abiforge.operators.source_evidence).
    ScopeBucket(
        "l4_l5_source_evidence",
        True,
        "seen only by L4 source replay or the L5 source graph; a sources config "
        "gives each side's source tree (abicheck dump --sources)",
        names=frozenset(
            {
                "call_graph_public_entry_reachability_changed",
                "default_argument_changed",
                "inline_body_changed",
                "public_api_internal_dependency_added",
                "public_macro_removed",
                "public_macro_value_changed",
                "public_surface_grew",
                "public_surface_shrank",
                "public_to_internal_dependency",
                "public_virtual_dispatch_set_changed",
                "template_body_changed",
                "undocumented_export_ratio_increased",
                "uninstantiated_template_removed",
            }
        ),
    ),
    # --- named, in scope: L4/L5 kinds only a multi-file, multi-TU source
    # tree shows (abiforge.operators.source_tree, the cxx_tree seed).
    ScopeBucket(
        "l4_l5_source_tree",
        True,
        "seen only in a source tree with a private header and two translation "
        "units: private-header provenance, per-TU type definitions, "
        "source-to-binary mapping and anonymous-type coordinates",
        names=frozenset(
            {
                "declaration_coordinates_shifted",
                "odr_source_conflict",
                "odr_type_variant",
                "rtti_for_internal_type",
                "source_decl_binary_symbol_mismatch",
                "source_to_binary_mapping_changed",
            }
        ),
    ),
    ScopeBucket(
        "binary_runtime_floor",
        True,
        "the highest provider version node a library's imports need, read "
        "from its .gnu.version_r: the binary alone shows it, although the "
        "catalog files it under build",
        names=frozenset({"runtime_floor_raised"}),
    ),
    # --- named, in scope: L3 build-mode kinds a build_data config reaches
    # (abiforge.operators.build_data).
    ScopeBucket(
        "l3_build_mode",
        True,
        "a compile mode recorded only in the build's compile commands; a "
        "build_data config gives each side's compile_commands.json",
        names=frozenset(
            {
                "abi_relevant_build_flag_changed",
                "exceptions_mode_changed",
                "lto_mode_changed",
                "rtti_mode_changed",
                "sanitizer_mode_changed",
                "stdlib_debug_mode_changed",
                "struct_packing_mode_changed",
                "threadsafe_statics_mode_changed",
                "tls_model_changed",
                "whole_program_vtables_mode_changed",
            }
        ),
    ),
    ScopeBucket(
        "non_x86_elf",
        False,
        "an ELF header or note fact no x86/x86-64 build can change "
        "(AArch64 BTI/PAC, e_flags, byte order, a non-GNU OS ABI); the "
        "corpus's toolchains target x86",
        names=frozenset(
            {
                "branch_protection_improved",
                "branch_protection_weakened",
                "elf_abi_flags_changed",
                "elf_endianness_changed",
                "elf_osabi_changed",
            }
        ),
    ),
    ScopeBucket(
        "executable_only",
        False,
        "a property of an executable (PT_INTERP, DF_1_PIE); a shared library "
        "linked with -shared has neither",
        names=frozenset({"interpreter_changed", "pie_disabled"}),
    ),
    ScopeBucket(
        "pe_macho_version",
        False,
        "a PE file version or Mach-O current_version; an ELF library has no such field",
        names=frozenset({"library_version_downgraded"}),
    ),
    ScopeBucket(
        "unlinkable_symbol_shape",
        False,
        "STT_COMMON exists only in relocatable objects: the linker allocates "
        "a common symbol in .bss, so a linked shared library never has one",
        names=frozenset({"common_symbol_risk"}),
    ),
    ScopeBucket(
        "declared_deployment_floor",
        False,
        "judged against a declared deployment floor (`.abicheck.yml` "
        "runtime_floors), a policy input the corpus does not give",
        names=frozenset({"platform_baseline_floor_raised"}),
    ),
    ScopeBucket(
        "libcxx_abi_v2_runtime",
        False,
        "needs a libc++ built with LIBCXX_ABI_VERSION=2 (std::__2): distribution "
        "libc++ pins _LIBCPP_ABI_VERSION=1 in __config_site, so no corpus "
        "toolchain can build the other ABI version",
        names=frozenset({"libcpp_abi_version_changed"}),
    ),
    ScopeBucket(
        "stored_layout_generation",
        False,
        "a record with a layout descriptor on one side and no size at all on "
        "the other: a stored snapshot from an extractor generation that "
        "recorded no layout. Every library the corpus builds and dumps live "
        "records a size on both sides (DWARF and every header backend), and a "
        "pair with headers on one side only is compared at binary depth",
        names=frozenset({"layout_unverifiable"}),
    ),
    # --- named, in scope: source-taxonomy kinds a public header alone shows.
    # Macro values, constexpr values, default-argument values, a removed public
    # typedef and an uninstantiated template stay out (module bucket below):
    # abicheck reads them from L4 sources only
    # (docs/learn/evidence-and-detectability.md).
    ScopeBucket(
        "header_visible_source",
        True,
        "a public header alone shows it (L2), although the catalog files it as source",
        names=frozenset(
            {
                "concept_constraint_changed",
                "concept_tightened",
                "exported_not_public",
                "header_only_function_params_changed",
                "inline_function_removed",
                "internal_template_leaks_via_public_api",
                "internal_type_leaks_via_public_api",
                "public_not_exported",
                "public_typedef_target_changed",
                "unversioned_exported_symbol",
            }
        ),
    ),
    # --- module buckets: the module's kinds no named bucket claims
    ScopeBucket("elf_symbols", True, _ELF_ONE_LIBRARY, module="symbols"),
    ScopeBucket("elf_types", True, _ELF_ONE_LIBRARY, module="types"),
    ScopeBucket("elf_platform", True, _ELF_ONE_LIBRARY, module="platform"),
    ScopeBucket(
        "source_graph", False, "needs the L5 source graph", module="source_graph_risk"
    ),
    ScopeBucket(
        "disposition",
        False,
        "a policy disposition, not an observed change",
        module="disposition",
    ),
)
SCOPE_BUCKET_BY_ID: dict[str, ScopeBucket] = {b.id: b for b in SCOPE_BUCKETS}
HEADER_VISIBLE_SOURCE_KINDS = SCOPE_BUCKET_BY_ID["header_visible_source"].names
