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
            harness_tests=("tests/test_family_f1_evidence_ablation.py",),
        ),
        Family(
            key="F2",
            title="Route parity",
            invariant=(
                "The same semantic request yields the same normalized report "
                "through every route (CLI, typed API, scalar vs release member, "
                "stored vs live operand, dry run vs execution)."
            ),
            harness_tests=("tests/test_family_f2_route_parity.py",),
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
    "evidence.unread_producer_read_as_confirmed_absence",
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
    "storage.short_decode_mistaken_for_complete_decode",
    "classification.two_agreeing_sources_read_as_unknown",
    "classification.default_branch_asserts_more_than_inputs",
    "detector.diff_confirmation_precondition",
    "coverage.discovery_derived_completeness",
    "guard.absent_capability_vs_real_failure",
    "registry.kind_completeness",
    "evidence.optional_layer_prerequisite_for_a_stated_fact",
)
_F2 = (
    "serialization.persisted_field_not_decoded",
    "cardinality.member_request_drops_scalar_field",
    "config.front_end_default_divergence",
    "config.propagation_completeness",
    "config.option_dropped_at_a_dispatch_branch",
    "config.sided_shared_input_dropped",
    "config.command_specific_discovery",
    "config.inferred_root_bypassed_by_a_second_entry_point",
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
)
_F3 = (
    "extract.aggregate_layout_header_attribution",
    "identity.environment_taint",
    "identity.name_and_referent_compared_as_one",
    "identity.pe_x86_c_decoration_alias",
    "identity.platform_decorated_mangled_name",
    "identity.release_edge_keyed_on_side_summary",
    "identity.second_node_key_scheme",
    "identity.special_member_variant_family",
    "matching.dedup_key_soundness",
    "evidence.backfill_bare_name_match",
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
    "evidence.spelling_used_as_a_semantic_model",
    "evidence.spelling_matcher_same_offset_underreport",
    "policy.public_surface_reachability",
    "extraction.function_local_declaration_leaks_into_surface",
    "extraction.implicit_declaration_leaks_into_surface",
    "extraction.implicit_language_rule_read_off_one_explicit_key",
    "extraction.language_mode_export_evidence",
    "extraction.ast_wrapper_chain_traversal",
    "guard.proxy_predicate_overshoots_justification",
    "config.rule_language_class_collapsed",
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
        "perf.pure_content_digest_recomputed_per_consumer",
        "perf.retention_decided_by_one_switch_not_by_consumers",
        "perf.reuse_key_normalizes_an_ordered_input",
        "perf.shared_projection_read_as_an_owned_copy",
        "perf.shared_resource_gate_keyed_on_a_per_caller_value",
        "perf.streaming_producer_joined_at_the_encoder",
        "cache.bookkeeping_describes_a_different_set_than_it_retains",
        "concurrency.compound_cache_operation_under_fan_out",
        "concurrency.gc_census_beside_live_thread",
        "concurrency.forked_child_unbounded_reap",
        "serialization.pure_projection_mutates_its_own_input",
        "serialization.whole_document_materialised_to_write_it",
    ]
)
_F8 = (
    "extraction.aggregate_layout_inverted_by_line",
    "extraction.emulated_compiler_builtin_absent_from_frontend",
    "extraction.linker_summary_flag_read_as_the_fact",
    "scoping.system_header_layout_unrecognized",
)
_F7 = (
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
)
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
