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

"""Defect families: the second level of the bug-class registry.

See ``docs/contribute/plans/defect-family-harnesses.md``. The September 2026
fix history showed the registry growing by one narrowly named class per fix,
while the next escape was a sibling of an already registered mechanism at a
different site. A *family* groups those classes under one shared invariant
and one harness that enumerates its sites mechanically, so the next sibling
is caught by the harness instead of by the next field report.

Every ``BugClass.id`` must be assigned to exactly one family here
(``tests/test_regressions_families.py`` enforces exhaustiveness and rejects
stale ids). A class that fits no family goes to ``OTHER`` with a reason; a
growing ``OTHER`` bucket is the signal that a new family is needed.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Family:
    """One defect family and the harness that generalizes it."""

    key: str
    title: str
    invariant: str
    #: Test modules implementing the family harness. Empty only for a family
    #: whose harness is still planned — ``planned_reason`` must then say so.
    harness_tests: tuple[str, ...]
    planned_reason: str = ""


FAMILIES: dict[str, Family] = {
    f.key: f
    for f in (
        Family(
            key="F1",
            title="Unknown is not a value",
            invariant=(
                "Removing, failing or truncating any evidence input never makes "
                "the result cleaner, never adds a BREAKING finding, and never "
                "raises assurance."
            ),
            harness_tests=(
                "tests/test_family_f1_evidence_ablation.py",
                "tests/test_export_loss_binding_unit.py",
            ),
        ),
        Family(
            key="F2",
            title="Route parity",
            invariant=(
                "The same semantic request yields the same normalized report "
                "through every route (CLI, typed API, scalar vs release member, "
                "stored vs live operand, dry run vs execution)."
            ),
            harness_tests=(
                "tests/test_family_f2_route_parity.py",
                "tests/test_family_f2_release_member_parity.py",
            ),
        ),
        Family(
            key="F3",
            title="Identity is semantic",
            invariant=(
                "Identity keys are invariant under environment transforms "
                "(checkout location, symlinks, hash seed, input order, platform "
                "decoration) and distinct entities stay distinct."
            ),
            harness_tests=("tests/test_family_f3_identity.py",),
        ),
        Family(
            key="F4",
            title="Structure over spelling",
            invariant=(
                "No finding's severity rests only on a name, suffix or directory "
                "heuristic without a structural fact confirming or vetoing it."
            ),
            harness_tests=("tests/test_family_f4_heuristics.py",),
            planned_reason="",
        ),
        Family(
            key="F5",
            title="Optimization equals reference",
            invariant=(
                "Every cache, memo, fast path, narrowing and parallel fan-out "
                "produces output identical to the unoptimized serial run."
            ),
            harness_tests=("tests/test_family_f5_optimization_reference.py",),
        ),
        Family(
            key="F6",
            title="Compatible real-library pair has no break",
            invariant=(
                "A known-compatible real release pair produces no BREAKING or "
                "API_BREAK finding."
            ),
            harness_tests=("tests/test_family_f6_corpus.py",),
            planned_reason="",
        ),
        Family(
            key="F7",
            title="Test and harness integrity",
            invariant=(
                "A test proves the path it claims actually ran, its oracle is "
                "independent of the implementation, and fixtures cannot build "
                "states the types forbid."
            ),
            harness_tests=(
                "tests/test_regressions_families.py",
                "tests/test_family_f7_mutant_replay.py",
                "tests/test_conftest_cache_isolation.py",
            ),
        ),
        Family(
            key="F8",
            title="Target and toolchain parity",
            invariant=(
                "The same sources and headers built or dumped for another target, "
                "or through another toolchain's install layout, parse and yield "
                "the same declaration surface and the same artifact facts; "
                "target-specific evidence may add facts, never remove or "
                "relocate the library's own."
            ),
            harness_tests=("tests/test_family_f8_target_parity.py",),
        ),
        Family(
            key="F9",
            title="Host-interpreter parity",
            invariant=(
                "abicheck produces the same persisted tokens and the same "
                "thread isolation on every supported Python, GIL and "
                "free-threaded alike; behavior that newer interpreters change "
                "(how json encodes str subclasses, whether a thread inherits "
                "its parent's context) is pinned against a declared oracle, "
                "not a same-interpreter round trip."
            ),
            harness_tests=(
                "tests/test_str_enum_serialized_token.py",
                "tests/test_ast_memo_slot_thread_context.py",
            ),
        ),
        Family(
            key="F10",
            title="Process lifecycle on failure paths",
            invariant=(
                "After an owner fails or is interrupted, no child process it started "
                "outlives it and no shared budget slot stays held; a successful owner "
                "is never cancelled. The interpreter joins pool workers at exit, so an "
                "abandoned child delays the whole process, not only the failing call."
            ),
            harness_tests=(
                "tests/test_cancel_token.py",
                "tests/test_header_graph_prefetch.py",
            ),
        ),
        Family(
            key="F11",
            title="Measurement judges what it claims to measure",
            invariant=(
                "A budget or gate on a sampled quantity judges the quantity its name "
                "and documentation claim: a process tree's footprint is the sum of "
                "proportional set sizes, not a sum of resident sizes that counts "
                "shared pages once per process."
            ),
            harness_tests=(),
            planned_reason=(
                "One class so far; revisit when a second class has a budget read off "
                "the wrong sampled quantity and the two share one mechanism."
            ),
        ),
        Family(
            key="F12",
            title="Argv replay and public-root probe read the same operands",
            invariant=(
                "Every spelling a compiler driver accepts for one include-search "
                "operand is read by every consumer of the compile unit: the "
                "public-root probe reads each CL-mode spelling. Replay parity with "
                "the probe is tracked as the remaining gap in known-gaps.md, not "
                "yet met."
            ),
            harness_tests=(),
            planned_reason=(
                "One class so far; revisit when a second class has a consumer "
                "that reads a driver's option spellings differently from the "
                "replay, and the two share one mechanism."
            ),
        ),
        Family(
            key="F13",
            title="Build inputs route to their own units",
            invariant=(
                "Every source operand a build input names becomes exactly one "
                "compile unit owned by that operand, and a link with no source "
                "operand compiles nothing: no operand is dropped, and no link "
                "object is taken for a source, by collapsing a record onto its "
                "first operand."
            ),
            harness_tests=(),
            planned_reason=(
                "One class so far, pinned by its own seed test; revisit when a "
                "second build-input adapter (Bazel, Ninja, make) collapses operands "
                "the same way and a shared operand-shape harness can enumerate them."
            ),
        ),
        Family(
            key="F15",
            title="castxml header vectors keep the element type clang reports",
            invariant=(
                "A castxml vector node resolves to the spelling clang gives for the same "
                "declaration, so a header comparison sees a same-size element change."
            ),
            harness_tests=(),
            planned_reason=(
                "One class so far; revisit when a second header-backend node type is "
                "collapsed to an opaque spelling and the two share the clang join."
            ),
        ),
        Family(
            key="F14",
            title="DWARF type spelling keeps the attribute that makes a type",
            invariant=(
                "A DWARF type whose identity is an attribute rather than its "
                "shape (DW_AT_GNU_vector) keeps that attribute in its recorded "
                "spelling: a vector never collapses onto a plain array's T[] "
                "spelling, so a same-size vector-versus-array change is reported."
            ),
            harness_tests=(),
            planned_reason=(
                "One class so far; revisit when a second attribute-determined "
                "type (for example a sized or address-space-qualified type) "
                "collapses the same way and the two share one mechanism."
            ),
        ),
        Family(
            key="OTHER",
            title="CI, tooling, storage and trust-boundary surfaces",
            invariant=(
                "No shared generalized harness; each class keeps its own seed tests."
            ),
            harness_tests=(),
            planned_reason=(
                "Heterogeneous CI/shell/storage/report-rendering mechanisms; "
                "revisit when three or more classes share one mechanism."
            ),
        ),
    )
}


_F1 = (
    "classification.non_entity_finding_feeds_contract_coverage",
    "detector.selector_axis_unstamped",
    "detector.removal_from_header_enumeration",
    "evidence.unread_producer_read_as_confirmed_absence",
    "evidence.depth_projection_drops_lower_layer_detector_input",
    "evidence.container_presence_read_as_evidence_content",
    "evidence.silent_degradation_to_clean_verdict",
    "evidence.export_presence_as_declaration_presence",
    "evidence.richer_model_hides_weaker_observation",
    "evidence.operand_shape_silently_unresolved",
    "evidence.surface_membership_asymmetry",
    "evidence.export_fact_join_disagreement",
    "status.container_existence_taken_for_completed_work",
    "report.unobserved_population_counted_as_observed",
    "report.unestablished_result_reads_as_success",
    "storage.legacy_availability_collapse",
    "storage.cross_generation_spelling_drift",
    "storage.short_decode_mistaken_for_complete_decode",
    "classification.two_agreeing_sources_read_as_unknown",
    "classification.default_branch_asserts_more_than_inputs",
    "detector.diff_confirmation_precondition",
    "coverage.discovery_derived_completeness",
    "guard.absent_capability_vs_real_failure",
    "registry.kind_completeness",
    "evidence.optional_layer_prerequisite_for_a_stated_fact",
    # An empty `ElfSymbol.version` stood for both "base-bound" and "no version".
    "evidence.distinct_encoded_states_collapsed_to_one_value",
    # A location expression with no exact offset was read as a plausible value.
    "evidence.malformed_input_yields_value",
)
_F2 = (
    "report.cross_producer_disagreement_on_one_symbol",
    # The L2 header pass and the L5 pass derived different fact families
    # from the same AST.
    "evidence.producer_drops_facts_its_input_holds",
    # Member check and release reconciliation disagreed on one obligation.
    "evidence.narrowed_conclusion_not_carried_across_cardinality",
    # The CLI and `dumper.dump` hybrid routes disagreed on the parse-time
    # dependency skip.
    "extraction.recorded_scope_matches_parse_skip",
    # L4's default extractor was castxml on the typed path but clang via the
    # `dump` CLI's re-embed, so the same C tree's macros reached one route only.
    "extraction.default_extractor_lacks_the_promised_fact_family",
    "serialization.persisted_field_not_decoded",
    "cardinality.member_request_drops_scalar_field",
    "config.front_end_default_divergence",
    "config.propagation_completeness",
    "config.option_dropped_at_a_dispatch_branch",
    "config.sided_shared_input_dropped",
    "config.command_specific_discovery",
    "evidence.entry_point_skips_extraction_record",
    "evidence.stored_snapshot_rederivation",
    "report.finding_entry_builder_parity",
    "report.scalar_release_projection_drift",
    "report.refusal_reaches_only_the_primary_output",
    "cli_surface.capability_guard_diverged_from_pipeline",
    "cli_surface.copied_option_table_went_stale",
    "cli_surface.name_independent_dispatch_undone_downstream",
    "cli.default_format_unreachable_operand",
    "release.cartesian_product_contract",
    "gate.per_member_axis_fold",
    "gating.consumer_scope_enrichment",
    "scoping.declaration_kind_forwarded_unfiltered",
    "scoping.aggregate_view_starvation",
    "adapter.duck_typed_view_attribute_drift",
    "api.positional_slot_rebinding",
    "evidence.compare_dump_inline_routing_parity",
    # A release member and its scalar pair disagreed when a header existed on one
    # side only; H2's asymmetric-header cell pins the route parity.
    "evidence.per_side_operand_divergence",
)
_F3 = (
    "config.location_dependent_fingerprint",
    "extract.aggregate_layout_header_attribution",
    "identity.edge_matched_on_coarse_key",
    "identity.environment_taint",
    "identity.name_and_referent_compared_as_one",
    "identity.pe_x86_c_decoration_alias",
    "identity.platform_decorated_mangled_name",
    "identity.release_edge_keyed_on_side_summary",
    "identity.second_node_key_scheme",
    "identity.special_member_variant_family",
    "matching.dedup_key_soundness",
    "evidence.backfill_bare_name_match",
    "identity.type_index_bare_name_collision",
    "extraction.macho_mangled_identity_normalization",
    "extraction.macho_export_index_double_strip",
    "classification.one_spelling_of_a_path_treated_as_its_identity",
    "comparability.incidental_ordering_treated_as_contract",
    "tooling.platform_dependent_path_key",
)
_F4 = (
    "classification.name_shape_as_contract_membership",
    "classification.declaration_existence_as_export_obligation",
    "classification.demotion_applied_beyond_its_justification",
    "classification.source_only_change_scored_as_binary_break",
    "evidence.spelling_used_as_a_semantic_model",
    "evidence.spelling_matcher_same_offset_underreport",
    "policy.public_surface_reachability",
    "extraction.function_local_declaration_leaks_into_surface",
    "extraction.implicit_declaration_leaks_into_surface",
    "extraction.implicit_language_rule_read_off_one_explicit_key",
    "extraction.language_mode_export_evidence",
    "extraction.ast_wrapper_chain_traversal",
    "extraction.target_flag_dropped_from_header_parse",
    "guard.proxy_predicate_overshoots_justification",
    "evidence.linker_reserved_export_symbols",
)
_F5 = tuple(
    [
        "perf.a_whole_document_retained_for_a_narrow_projection",
        "perf.admission_commits_the_whole_probed_budget",
        "perf.bounded_cache_budget_omits_what_it_retains",
        "perf.cache_fast_path_bypasses_shared_coordination",
        "perf.derived_cache_must_match_what_recomputing_would_give",
        "cache.computed_output_keyed_without_code_identity",
        "perf.enumerable_value_space_allocated_per_occurrence",
        "perf.evidence_released_before_its_consumer_runs",
        "perf.fixed_layout_fast_path_field_decoding",
        "perf.narrowed_computation_must_equal_the_unnarrowed_answer",
        "perf.optimization_wired_to_one_of_several_equivalent_paths",
        "perf.per_symbol_dto_carries_a_per_instance_dict",
        "perf.pattern_compiled_before_the_filter_that_discards_it",
        "perf.pure_content_digest_recomputed_per_consumer",
        "perf.retention_decided_by_one_switch_not_by_consumers",
        "perf.reuse_key_normalizes_an_ordered_input",
        "perf.shared_projection_read_as_an_owned_copy",
        "perf.shared_resource_gate_keyed_on_a_per_caller_value",
        "caching.duck_typed_key_attribute_default",
        "perf.streaming_producer_joined_at_the_encoder",
        "cache.bookkeeping_describes_a_different_set_than_it_retains",
        "concurrency.compound_cache_operation_under_fan_out",
        "concurrency.gc_census_beside_live_thread",
        "concurrency.forked_child_unbounded_reap",
        "serialization.pure_projection_mutates_its_own_input",
        "serialization.whole_document_materialised_to_write_it",
        "cache.hit_identity_follows_the_requested_path",
    ]
)
_F8 = (
    "extraction.aggregate_layout_inverted_by_line",
    "extraction.emulated_compiler_builtin_absent_from_frontend",
    "extraction.linker_summary_flag_read_as_the_fact",
    "scoping.system_header_layout_unrecognized",
)
_F7 = (
    # Masked by suite-wide shared interpreter state: passed in a full run,
    # failed alone (test isolation), hence F7 rather than OTHER.
    "imports.first_import_order_cycle",
    "imports.side_effect_import_reorders_registration",
    "guard.differential_test_shares_state_with_itself",
    "invariant.blanket_assertion_over_widened_population",
    "test_double.narrower_than_the_real_signature",
    "test_fixture.host_artifact_assumed_capability",
    "test_harness.destructive_reset_of_a_caller_supplied_path",
    "test_harness.git_inherits_developer_signing_config",
    "test_harness.nested_session_prunes_outer_temp_root",
    "test_infra.autouse_allocator_cannot_recover",
    "test_infra.caller_owned_directory_fabricated",
    "tests.constructed_environment_drops_ambient_mitigation",
    "tests.dead_harness_reads_as_a_passing_control",
    "tests.fixture_fabricates_a_state_the_types_forbid",
    "tests.locale_dependent_repo_text_read",
    "tests.host_dependent_expectation",
    "tests.language_rule_oracle_restates_the_detector",
)
_F9 = (
    "serialization.interpreter_dependent_str_subclass_token",
    "concurrency.per_thread_state_in_inherited_context",
)
_F10 = ("concurrency.abandoned_child_outlives_failed_owner",)
_F11 = ("measurement.forked_tree_rss_overstates_memory",)
_F12 = (
    # The public-root probe read only `/I` from a CL-mode argv, so a `-I` or
    # `/external:I` include root was never probed while the replay read a
    # different operand set from the same driver spelling.
    "extraction.msvc_include_operand_spelling_not_probed",
)
_F13 = ("extraction.compile_entry_collapsed_to_first_source",)
_F14 = ("extraction.vector_type_collapsed_to_plain_array",)
_F15 = ("extraction.castxml_vector_element_not_modelled",)

#: id -> reason it belongs to no shared family.
OTHER_REASONS: dict[str, str] = {
    "guard.platform_convention_without_a_gate": "platform convention lint gate",
    "ci.inert_concurrency_group_key": "GitHub workflow configuration",
    "ci.instrumentation_without_a_consumer": "GitHub workflow configuration",
    "ci.path_filter_omits_own_build_infrastructure": "GitHub workflow configuration",
    "ci.shared_budget_over_heterogeneous_matrix": "GitHub workflow configuration",
    "ci.unrelated_apt_source_gates_the_job": "GitHub workflow configuration",
    "shell.empty_array_expansion_under_nounset": "shell-script semantics",
    "trust_boundary.shell_workflow_injection": "trust-boundary execution tests",
    "tooling.platform_dependent_record_separator": "developer tooling text I/O",
    "config.env_flag_value_domain": "environment-variable parsing",
    "config.input_granted_unrelated_authority": "trust-boundary authority",
    "config.textual_substitution_into_a_structured_document": "document templating",
    "cli_surface.derived_table_lost_in_its_own_encoding": "Action option-table encoding",
    "cli_surface.destination_collision_checked_only_by_exact_path": "output-path handling",
    "cli_surface.retired_spelling_in_remediation": "user-facing message text",
    "report.shared_display_budget_starvation": "report rendering budget",
    "report.untrusted_value_escapes_its_cell": "report rendering escaping",
    "storage.out_of_band_snapshot_reader_envelope_drift": "storage envelope readers",
    "storage.safety_limit_used_as_allocation_size": "storage resource limits",
    "storage.third_party_contract_at_scale": "third-party codec contracts",
    "serialization.str_enum_downcast_via_generic_rewrite": "serialization codec",
    "policy.disposition_conservation": "policy disposition audit (ADR-067)",
    "compare.order_independent_reorder_refused": "comparability gate header-order proof (ADR-082)",
    "package.ar_magic_is_not_deb": "package format sniffing (ar archives)",
    "templates.fn_type_arguments_counted_as_template_arguments": "template argument counting",
}

FAMILY_OF: dict[str, str] = {}
for _key, _ids in (
    ("F1", _F1),
    ("F2", _F2),
    ("F3", _F3),
    ("F4", _F4),
    ("F5", _F5),
    ("F7", _F7),
    ("F8", _F8),
    ("F9", _F9),
    ("F10", _F10),
    ("F11", _F11),
    ("F12", _F12),
    ("F13", _F13),
    ("F14", _F14),
    ("F15", _F15),
    ("OTHER", tuple(OTHER_REASONS)),
):
    for _id in _ids:
        if _id in FAMILY_OF:
            raise ValueError(f"bug class {_id!r} assigned to two families")
        FAMILY_OF[_id] = _key


def family_counts() -> dict[str, int]:
    """Number of registered bug classes per family key."""
    counts = dict.fromkeys(FAMILIES, 0)
    for key in FAMILY_OF.values():
        counts[key] += 1
    return counts
