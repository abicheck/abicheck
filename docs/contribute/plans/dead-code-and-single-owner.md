# Dead code and single owners

Status: Stage A done (PR #1448); Stage B item 1 and Stage C done; Stage B
items 2-3 continue in [usecase-path-tracing](usecase-path-tracing.md);
Stage D's first pass done; Stage E's parameter list decided (31 parameters
left, each kept or recorded as a known gap; five groups were dropped wiring
and are now wired, and one removal exposed a notice-wording bug, fixed), its
documented-API list decided too.

## Why

Line coverage is high, but much of it came from tests calling functions no
command, workflow or documented API calls. And several building blocks had
more than one implementation, so two paths through the product answered the
same question differently. Both were found by running the real CLI scenarios
(not the test suite) under `coverage run` over the 169 catalog library pairs,
then cross-checking every unexecuted definition for production references to
a fixpoint, and by comparing the competing implementations directly.

## Rules for this work

- **Dead** means no caller in `abicheck/`, `scripts/`, `action/`, `actions/`,
  `.github/` or `pyproject.toml`, and not documented as Python API in
  `docs/use`, `docs/reference` or `docs/learn`. A test-only caller does not
  make code live.
- Documented API (including API an accepted ADR or plan names), test hooks and explicitly planned primitives are kept until
  a decision says otherwise.
- A test that used a removed function as an independent oracle keeps it as
  test code; it is never just deleted.
- Each duplicate is collapsed onto one owner, with a test that states the
  shared contract over a generated input space and fails on the old code.

## Done in PR #1448

Removed (about 10k lines): leftovers of the deleted `merge`, `collect` and
`scan --artifact-set` commands, the appcompat renderers, the stderr
annotation path, and about 40 smaller unreferenced functions.

Fixed while removing:

- Markdown dropped every finding of a flooded non-gating kind.
- `compat` could not read back its own `.dump`.

Single owners:

| Question | Owner | Former divergence |
|---|---|---|
| `-std=` spelling, edition order | `model/language_standard.py` | first vs last flag; `c++98` ranked above `c++20` |
| Internal namespace names | `model/symbol_ownership.py` | export accounting missed `__detail`/`_impl` |
| Integer specifier order | `model/int_spelling.py` | `long unsigned int` vs `unsigned long` across backends |
| New-library exports in appcompat | `model/export_index.py` | private table reader |
| Stored-pack integrity | `pack_io.verify_integrity` via `pack_load` | defined, never called |
| Verdict to legacy exit code | `policy.severity.legacy_exit_code` | `--used-by` kept its own map |

## Stage A — behavioural duplicates (done)

| # | Question | Owner | Bug it closed |
|---|---|---|---|
| 6 | `-std=` on castxml's command line | `extract/castxml_compiler_emulation.castxml_parser_arguments` | MSVC `/std:` was read by castxml as a file name; L2 and L4 parses failed |
| 7 | Is a finding in a frozen namespace | `post_processing.match_frozen_namespace` + `policy/frozen_namespace.py` | demotion looked at the root type only, so a finding escalation would tag was demoted first |
| 8 | Is an entity standard-library | `model/source_graph_query.is_stdlib_owned_name` | `mylib::Wrapper<std::string>` read as stdlib |
| 9 | Override parameter identity | `model/signature_normalization.canonicalize_function_signature_param_type` | vtable keys missed array adjustment and callback cv |

## Stage B — make the measurement reproducible

1. **Done.** `scripts/usecase_paths.py` records which functions the
   automated scenarios and the real-binary catalog flows execute
   ([usecase-path-tracing](usecase-path-tracing.md)), and its `dead`
   subcommand (`scripts/production_references.py`) applies this plan's rule
   to a recording: every unreached function whose production references
   all lie inside other dead functions, to a greatest fixpoint, with
   documented and ADR/plan-named API listed apart and kept as roots. The
   weekly `usecase-paths.yml` run publishes the list, so it is recomputed,
   never hand-made. `tests/test_production_references.py` checks the
   fixpoint against breadth-first reachability over generated call graphs.
2. Windows and macOS recordings, and 3. wider sources (the composite
   Action, PR-comment rendering, the hybrid frontend, `post_manifest`,
   `debian_symbols`, a multi-library release) are the same work as
   usecase-path-tracing's "Widen the sources" and "Windows and macOS"
   steps, tracked there. Until they land, PE, Mach-O and PDB readers and
   those flows read as unreached on Linux for want of a recording, not
   because they are dead.

## Stage C — decisions (done)

Each item was wired into a real path, deleted, moved to `tests/`, or kept
with the ADR or page that names it:

| Item | Decision |
|---|---|
| `product_baseline.py` (~1,900 lines + ~3,800 of tests) | **Deleted.** It reimplemented directory `compare` with diverging semantics (no suppression, no ADR-065 scope record, every missing library a removal) and carried ADR-065 D3's open silent-fallback item. A whole product is compared with directory/package `compare`; `--bundle-facts-out` stores its baseline. ADR-065's D3 item is closed by the deletion. |
| `bundle_side_input.compare_bundle_sides` and its `LiveBundleInput`/`StoredBundleFactsInput`/`resolve_bundle_side` layer | **Deleted.** The CLI has one driver per operand shape instead (`compare_release_against_bundle_facts`, `workflows/bundle_stored_pair_compare.py`). |
| `bundle_multibuild.py`, incl. `coverage_regression_findings` | **Deleted, with `ChangeKind.BUNDLE_VARIANT_COVERAGE_REGRESSED` (410 → 409 kinds).** G38 had made the CLI half's deferral permanent and storage-format-v2 A1.6's `compare/variant_pairing.py` is the pairing that runs, report-only by design; the kind could never be emitted. G38 Phase 3 carries the amendment. A gate on an unmatched required variant belongs to ADR-065's completeness axis, not a `ChangeKind`. |
| `buildsource/archive_graph.defining_members` | **Wired.** `localize_symbol` (`graph explain`, documented Python API) reports `defined_in_archive_members` through it — the "`cache_dispatch.o` in `libinternal_dispatch.a`" answer its reference page promised. Moved to `model/source_graph_query.py`, since `compare` may not import `extract`. |
| `accepted_main_cache_key` / `accepted_main_cache_restore_prefix` | **Wired.** `update-main-baseline.yml`'s "Compute cache key" step calls them instead of restating the key in bash, which had drifted: `baseline-generation: 03` keyed `-g03` while the manifest recorded 3, and `abc` built a key before `actions/baseline` rejected it. `parse_baseline_generation` applies `actions/baseline`'s acceptance rule. |
| `cross_front_end_differences`, `unstatable_selectors` | **Moved to `tests/_cross_front_end.py`.** They compare two resolved configs; no run holds two. |
| `source_smoke.run_source_smoke` | **Moved to `tests/source_smoke.py`.** Its only runtime caller is the examples harness; ADR-060 calls it a fixture oracle. |
| `sycl_context.decode_and_select_frontend_context`, `decode_frontend_contexts`, `select_frontend_context` | **Moved to `tests/_sycl_context_oracle.py`**, the independent non-streaming oracle the production `..._from_path` decoder is checked against. |
| `storage/identity.group_by_entity` | **Deleted** (named nowhere; its tests now state the same contract on `OccurrenceSet`). |
| `impact/use_cases.build_use_case_graph`/`join_use_case_graph` | **Deleted.** No tracked item consumes the joined graph: `compare --use-cases` answers over the plain library graph (`explain_use_case_impact`). Their entrypoint-resolution tests now assert on the shared resolution owner. ADR-057's status carries the amendment; the `USE_CASE_*` edge kinds stay registered as reserved. |
| `post_manifest.diff_manifests` | **Kept:** documented release-script API in `docs/use/post-python.md`. |
| `storage/availability.for_entity`/`missing_families` | **Kept:** the inert ADR-062 Phase 0 primitive (D3) that one-semantic-pipeline Phase 8 wires; deleting `for_entity` would leave `override()` writing data nothing reads. |
| `contract_replay.replay_original_decisions` | **Kept:** ADR-049 D6's "replay original decision" procedure, which ADR-067 builds on. |
| `workflows.input_resolution.load_env_matrix` | **Kept:** ADR-068's documented migration path for the retired `env_matrix_path`. |

The removed Python names are registered in `scripts/retired_surfaces.py`, so
a page that still presents one as live is flagged.

## Stage D — the recomputed list (first pass done)

`python scripts/usecase_paths.py record --source scenarios --source flows
--build-catalog DIR` followed by `usecase_paths.py dead` listed **127** dead
functions (no production reference, undocumented), 26 named only in user
docs and 76 named only by an ADR or plan. Worked module by module, the same
way as Stage C. Recomputed after the pass, the same recording lists **0**
dead functions; the decided-but-kept ones below are now named by this plan,
which is what moves them out of the dead list.

A function with no production reference is dead whatever the platform: a
missing Windows or macOS recording only explains why a function is
*unreached*, never why nothing calls it. So the `pdb_parser.py` accessors
were decided here like everything else rather than waiting for a Windows
recording.

### Deleted, wired or moved

| Item | Decision |
|---|---|
| `_CastxmlParser` delegation shims (`dumper_castxml.py`, 17 incl. the ones the name matcher kept alive through a same-named method elsewhere, and the unread `_FUNCTION_TAGS`) | **Deleted.** Each forwarded to an `extract/headers/castxml/*` owner; the tests that reached through one now call the owner with `parser._ctx`. Three more, `_root`/`_pub_header_segs`/`_pub_dir_segs`, were deleted by hand past the tool's list and **restored**: `extract/header_ast_fields.py` reads them with `getattr(parser, ...)`, and without `_root` every castxml parse in a live `compare` shared one SemanticIR (the integration lane caught it). The tool had kept them live through those string names; a hand override of a live verdict needs the reference read first. The shared normalization now declines to share for a parser without a root, and `tests/test_header_ast_parser_interface.py` checks both parsers carry every attribute read off them by name. |
| `reporter_markdown.py`'s `_append_*`/`_build_*` section wrappers (12) and their `reporter.py` re-exports | **Deleted.** The renderer goes through `compute_*` + `report/render_markdown.render_*`; the tests now call that pair. |
| The CFI pass in `dwarf_advanced.py` (frame registers, callee-saved fallback), `parse_advanced_dwarf` (both copies), `ChangeKind.FRAME_REGISTER_CHANGED`, `AdvancedDwarfMetadata.frame_registers`/`callee_saved_regs` | **Deleted** (409 → 408 kinds). The unified DWARF parse never called the pass, and it asked pyelftools for `get_EH_CFI_entries`/`get_CFI_entries`, which do not exist, so it returned nothing even when called; its tests used mocks with those names. Measured before deciding: a corrected pass wired into production changed one of the 169 built catalog cases, a false `frame_register_changed` turning `case15_noexcept_change` from `COMPATIBLE_WITH_RISK` into `BREAKING`, and still missed case64's GCC `ms_abi` change (the callee-saved set at that optimisation level carries no `rdi`/`rsi` spill, and the heuristic counted the return-address column as a saved register). `test_dwarf_unified.py`'s advanced-half "unified equals separate" tests compared `parse_dwarf` with a shim of itself; they now compare against `tests/_dwarf_advanced_oracle.py`, a separate ELF open. |
| `service_compare_evidence.explicit_source_extractor` and `L4_SOURCE_EXTRACTORS` | **Deleted** (scan's resolver). Its test module claimed `compare`/`dump` never pass an explicit `--ast-frontend` to L4 replay; they do, through `effective_frontend` in `workflows/artifact/embed_side.py`. The exhaustive frontend x env oracle now checks that live resolver (`tests/test_l4_frontend_propagation.py`), and the `config.propagation_completeness` manifest entry says so. |
| About thirty single accessors and wrappers: `BinarySummary.has_text`/`text_size`, `HeaderCompileContextResolution.matched_unit_count`, `BundleSnapshot.library_names`, `_collect_additions`, `_ClangAstParser._specialization_record_index`, `_castxml_available`, `DebugArtifact.has_dsym`/`has_pdb`/`has_split_dwarf`, `_candidate_type_names`, `BundleVariantsConfig.required_names`, `ChangeKindRegistry.kinds_for_entity`/`templated_kinds`, `conflicts_to_dicts`, `is_unresolved_node_id`, `ScopeAcquisitionRecord.members_in`, `AbiSnapshot.func_by_mangled`, `surface_facts.is_unknown`, `is_local_name_symbol`, `policy_registry_markdown`, `coverage_diagnostic_from_summary`, `PostProcessingPipeline.step_names`, `PipelineContext.baseline_present`, `package_declares_full_dependency_scope`, `variant_and_artifact_ids`, `ChangeInventorySplit.has_hygiene`, `_charge_document_bytes`, `ExpectedTargets.from_manifest_file`, `ResolvedArtifactPlan.add_cleanup`, `BundleCompareRequest.any_stored`, `SnapshotRetention.any_full`, `ordinal_only_pe_exports`, `execution_cache.caching_enabled`/`cache_kinds` | **Deleted.** Tests that used one now state the same expectation on the data it read. |
| `merge_unproduced`, `override_suppression`/`override_suppressed_change`, `no_baseline_json_report`/`no_baseline_markdown_report`, `fold_audit_gate_exit`, `classify_change_object`, `is_pe`/`is_macho`, `PatternFactsResult.should_escalate`, `ProfileSpec.runner_label`, `EntityResolver.canonical_id_for`, `attribute_failing_headers`/`cross_header_conflicts`, `encode_native_identity_aliases`, `TypeDatabase.all_procedures`/`all_mfunctions` | **Deleted.** `package_component_inventory` is ADR-065's real producer of unproduced members; the override pair served the deleted `scan --against`; the report wrappers bypassed the CLI's own `render_no_baseline` (tests read that now, and the audit-gate property tests drive the real fold, `no_baseline_exit_code`); production sniffs formats through `binary_utils.classify_magic` and rejects an MZ-only file with a clear PE parse error; the alias encoder's writer is gone and the surviving decoder reads older packages only. |
| `classify_aapcs64_aggregate`/`_is_short_vector` | **Deleted, gap recorded.** An unwired AAPCS64 HFA/HVA classifier whose wiring plan (G1) closed without wiring it, while three docs cited it as modeled coverage. The docs are corrected and `docs/contribute/known-gaps.md` records what a real detector needs. |
| `conservation_holds`, `unclassified_release_contribution_fields`, `applied_pack_fields`, `count_visible_options`, `graph_table_to_legacy_dict`, `type_string_references_name`, `load_snapshot_document` | **Moved to `tests/`** (`_disposition_invariants.py`, `_graph_table_oracle.py`, `_type_token_oracle.py`, `_snapshot_document_reader.py`, or the one test that asserts it). Each was a test oracle or helper. The type-token oracle now states production's ASCII-only boundary rule, with non-ASCII cases in its agreement sweep. |

### Single owners found while deciding

Several "dead" functions turned out to be the one correct implementation of
something production computed a second way. Each was wired or the copy
removed, with a test over generated inputs that fails on the old code:

| Question | Owner now | Former divergence |
|---|---|---|
| Discover a directory's comparable inputs for `compare --dry-run` | `workflows.release_inputs.collect_release_inputs` | the preview repeated the discovery inline and answered an empty plan for a side with no supported input, where the real run refuses it |
| Raw gate inputs → the effective-config digest's `EffectiveGate` | `effective_config_digest.effective_config_fields_from_raw` (called by `add_effective_config_digest`) | the report block rebuilt the gate inline; the function's copy, used by ~90 tests, recorded an empty scheme literally |
| Which definition a PDB forward reference names | `pdb_parser._link_forward_refs`, read through `resolve_struct`/`resolve_enum` | last definition won and structs and enums shared one map, while `pdb_metadata` takes the first complete definition as the layout; enum forward refs never followed |
| Is a fact a completed read | `model.evidence_merge.is_completed_read` | the merge functions and `diff_cxx_rules` re-spelled the status check |
| Relabel recorded findings `SUPPRESSED` after a ledger closed | `DispositionLedger.with_suppressed`, resolving through `indices_for` | matched by object, so an alias of a recorded observation kept its old disposition (the deleted `override_suppression` resolved aliases) |
| Format a snapshot's JSON text | `storage.json_stream.iter_json_indented` (`join_json_indented` for the one-shot zstd write) | the default zstd write used `json.dumps`, a second formatting path |

### Kept

| Item | Why |
|---|---|
| `buildsource/compiler_record.extract_compiler_record` and its helpers | Documented Python API: `docs/use/build-evidence-setup.md` names it as the replacement for the removed `--read-compiler-record` flag (the page now names the function, not only the module, so the tool can see it). |
| `header_include_memo.clear_include_memo`, `spelling_match_cache.clear_caches`, `cache_header_scan.reset_header_scan_statistics`, `path_aliases.clear_path_alias_caches`, `execution_cache.clear_memoized`/`reset_cache_stats`, `type_spelling.strip_ptr_cache_clear` | Test hooks: cache resets for test isolation and cold-cache benchmarks. |
| `type_spelling.strip_ptr_cache_entries`/`strip_ptr_cache_stats`, `_PatternRegistry.is_held`/`pattern_for`/`reference_counts`, `_ProbeGate.in_flight`, `MemoryAdmission.estimate_gib`, `lazy_graph.is_graph_decoded`, `SurfaceAcquisitionLedger.acquisitions_by_key`/`total_acquisitions`/`total_reuses` | Test hooks: the observation points the retention, single-flight, admission, lazy-decode and acquire-once tests assert through. |
| `policy/name_heuristics.severity_raising_heuristics` | The H4 catalogue is the test oracle design-hardening-from-defect-families.md assigns it (runtime registration lives in `model/`). |
| `extract/wheel_tags.parse_wheel_architecture_claim` | The planned entry point for G27's wheel-tag auto-derivation, with the three floor parsers g27-wheel-deployment-verification.md names. |
| `extract/dwarf_subtree_index.subtree_ends` | Test hook: the oracle probe the subtree-index tests compare against pyelftools' own terminators. |
| `storage/fact_availability.FactAvailability.establishes_absence`, `storage/identity.OccurrenceSet.is_ambiguous`/`occurrences_of` | The inert ADR-062 Phase 0 primitives (D3/D4) one-semantic-pipeline Phase 8 wires, kept for the same reason as Stage C's `for_entity`. |

Recompute the list rather than editing a copy of it. A cache-reset or
`reset_for_testing` hook that exists for test isolation is a test hook and
is kept.

## Stage E — parameters no production call passes

Stage D left one item open: keywords `scan`'s deletion left on
`workflows/artifact/execute._resolve_side_snapshot_impl`. The function stays
live, so a function-level pass cannot see them. `usecase_paths.py dead` now
runs a second pass, `production_references.dead_parameters`: a parameter
with a default that no production call passes, so every production call
runs the default. It needs no recording, and it matches calls by name and
errs towards "passed" the way the function pass errs towards "live" (`**kw`
passes everything, `*args` every positional slot, a function whose name has
any non-call production use is reported as not checkable). Calls inside
functions the function pass reports dead do not count, tests never count,
and `import f as g` is followed within its file.
`tests/test_production_references_parameters.py` checks it against
generated packages whose oracle is what each generated call binds.

First run on this branch: **146** parameters on **102** functions; 24 more
on 17 functions the user docs name (public API, listed apart, as for
functions); 185 functions not checkable. It finds the seven parameters
Stage D named on `_resolve_side_snapshot_impl` plus an eighth,
`build_config_locally_trusted`, and six on its wrapper
`resolve_side_snapshot`. The rest is mixed: parameters left by deleted
callers, test seams (`now`, `runner`, `env`), and tunables every caller
leaves at the default (`indent`, `timeout`, `limit`).

Decide each, module by module, the way Stages C and D did: remove the
parameter (and whatever it threads), keep a test seam, or keep a tunable
with the page or test that names it. Recompute the list rather than
editing a copy of it.

### Decided

| Item | Decision |
|---|---|
| `_resolve_side_snapshot_impl`: `build_config_locally_trusted`, `baseline_reuse_hint`, `seed_lang_explicit`, `defer_cleanup`, `source_extractor`, `expand_public_header_roots`, `l4_public_headers`/`l4_public_header_dirs`; its wrapper `resolve_side_snapshot`: `symbols_only`, `debug_presence_only` | **Removed**, with what they threaded: the impl's `symbols_only`/`debug_presence_only` (only the wrapper passed them), `embed_side_build_source`'s `defer_cleanup`/`source_extractor`/`expand_public_header_roots`/`l4_public_*` (its one caller is the impl), the trust gate's and the L2 seed's `build_config_locally_trusted`, and the pair-reuse decision behind `baseline_reuse_hint` (`BaselineReuseContext`, `resolve_baseline_compile_context`, `SideResolution.baseline_compile_context`, and `tests/test_baseline_reuse_context.py`). Every one existed for `scan`'s candidate resolution; no production call passed a non-default value, so `compare` and `dump` run exactly as before. Recomputed afterwards: 132 parameters on 100 functions. The wrapper's other four (`enable_debuginfod`, `debuginfod_url`, `dwarf_only`, `debug_format`) were first removed too and then restored: they were unpassed because `--no-baseline` dropped the `debug:` config, not because nothing needed them (next row). |
| `workflows/no_baseline_compare`: `resolve_no_baseline_candidate`'s `header_backend`, `frontend_context`, `notify`; `run_no_baseline_compare`'s `force_public_symbols`; `policy/outcome.run_outcome_dict_for_scan`'s `lifecycle` | `header_backend`/`frontend_context` and `lifecycle` **removed**: the config supplies the first two through `compile` (`compile.frontend` outranks the bare backend), and no caller gives a scan report a lifecycle (`run_outcome_for_scan_fields`'s went with it). `notify` and `force_public_symbols` were **wired**: they were unpassed because `--no-baseline` dropped what feeds them. Checking that found the class, not one instance: of the resolved config's fields, the audit read 6; `compile.lang`, the `debug:` block and `source.method` (all CLI flags demoted to config) had no effect on it at all. `frontends/cli/compare_config_settings.config_run_settings` is now the one reading both `compare` shapes use, and `tests/test_no_baseline_config_settings.py` requires every field to be read by the audit or declared not applicable (severity: ADR-068's audit-gate ruling; release fan-out fields: no directory operand) or not yet wired (`show_redundant`, shrink-only), plus 37 generated configs checked against the written document. Recomputed afterwards: 127 parameters. |
| Report renderers: `report_document` on `to_markdown`, `to_review_digest`, `to_sarif`/`to_sarif_str`, `to_junit_xml`, `generate_html_report` and the document builders below them; `compute_full_change_rows`' `evidence_status_override`; `write_html_report`'s `report_kind`/`demangle`; `to_junit_xml_multi`'s `report_mode`; `add_effective_config_digest`'s `on_incomplete_scope`/`fail_on_removed_library`; `indent`/`encoding`/`max_nodes`/`max_member_bytes` tunables | `report_document` **removed**: it was the pre-envelope form of sharing one document, and every production render passes `envelope` (`service_render` passed both, once); `report.envelope.resolved_document` now takes the envelope alone, and the tests that supplied a document supply an envelope. `evidence_status_override` **removed**: its caller was the appcompat HTML renderer Stage A deleted. `report_kind` was **wired**: `compat check -old-style` wrote every report as "Binary compatibility report"/`kind:binary`, including the source-only ones `-source` and `-src-report-path` produce; `tests/test_compat_report_kind.py` checks all 16 combinations of `-source`/`-binary`/`-bin-report-path`/`-src-report-path` against ABICC's flag table (10 fail on the previous code, the 6 binary-only ones pass on both). `demangle`, `report_mode` (the release fan-out rejects `--view root-cause` before rendering) and the per-comparison digest's release-only gate axes (only the release summary's gate reads them) **removed**; so are `to_json`/`to_sarif_str`/`stack_to_json`/`render_mapping_as_json`'s `indent`, `render_xml_document`'s `encoding`, `render_element_as_xml`'s `indent`, `max_nodes` and `max_member_bytes`, each now its constant. Two tests that only exercised a removed knob (`to_sarif_str(indent=4)`, `write_html_report(demangle=False)`) went with it. **Kept** as test seams: `load_member_report`'s `max_bytes` (the size refusal without a 32 MiB file), `render_xml_document`'s `indent` (round-trips unindented XML), `render_comment`'s `timestamp`; and as tunables the tests drive, `render_surface_changes_*`'s `limit`. Recomputed afterwards: 113 parameters on 86 functions. |
| `workflows/history.run_history_request`'s `versioning_policy` | **Wired**, a bug: `project history --policy DOC` passed the path on as a profile name, which `compare()` reads as `strict_abi`, so the document's `overrides:` never reached the pairwise comparisons and its `versioning:` block never reached the deprecation-window check (`deprecation_compliance` was always empty from the CLI). The command now takes `compare`'s own `--policy` (`cli_options.policy_option`), `run_history_request` takes the document and derives its stated versioning policy (`policy/versioning_policy.stated_versioning_policy_of`, which `semver.stated_versioning_policy` now delegates to), and the deprecation-window evaluation moved to `workflows/history_deprecation.py`. `tests/test_cli_project_history_policy.py` (14 of 16 fail on the previous code). |
| `diff_cpp_patterns.detect_inline_body_renamed_member`'s `namespaces` | **Wired**, a bug: the post-processing step never passed the policy's `internal_namespaces`, so the detector used its own three-name list -- no finding for a configured `priv::`, nor for `__detail::`/`_impl::`, which the shared default and every sibling step treat as internal. `tests/test_inline_body_internal_namespaces.py` (13 of 28 fail on the previous code; oracle: the documented convention and agreement with `internal_type_leaks_via_public_api`). |
| `preprocessor_facts.collect_preprocessor_facts`' `clang_bin`; `dumper_clang.resolve_source_frontend_clang_bin`'s `fallback` | **Wired**, a bug: the S2 pre-scan always ran a bare `clang++` while L4 replay of the same compile units ran `.abicheck.yml`'s `compile.compiler`/prefix; the `fallback="clang++"` existed for the pre-scan caller `scan` took with it. `embed_side_build_source` now records the pre-scan's compiler beside L4's (`AbiSnapshot.live_preprocessor_clang_bin`, runtime-only, never serialized). `tests/test_preprocessor_scan_compiler.py` (21 of 22 fail on the previous code). |
| `comparability.compute_extraction_contract`'s `target_triple`/`pointer_width`/`endianness` and `depfile_resolved_paths`/`generated_driver_path` | **Recorded as known gaps**, not wired here: both are real (two dumps differing only in `-m32`, or in the dependency headers they read, fingerprint identically), but wiring either changes what ADR-050's gate hashes and needs an unrecorded-side carve-out for every stored baseline. See `known-gaps.md`, "The comparability contract never records the target platform" and "The L2 header parse never captures a dependency file". |
| model/compare/extract: `spelling_matches`/`finditer_allow_nested` `start`/`end`; `reconciled_public_function_maps` `key`; `_export_table_declarations` `ids`; `record_compatibility_decisions` `kind_sets`; castxml `_resolve_cv_restrict`/`_type_alignment_bits` `depth`; `retry_excluding_error_headers` `max_attempts`; `debug_info_surface_facts`/`export_table_surface_facts` `producer`/`headers_parsed`; `build_consumer_graph` `symbols`; `internal_leak` `_path_has_indirection`/`_path_is_value_propagating` `snap`; `entity_id_for_type`/`entity_id_for_enum` `anonymous_ordinal`; `macro_definition_tokens`/`tokens_with_defines`/`MacroDefinition.token` `style`; `with_record_layout`/`layout_fact` `producer`; `split_top_level` `sep`; `check_pack_fields_applied` `loaded`; `check_resolved_config_applies_packs` `gate_supported`/`gate_reason`; `apply_pattern_verdicts` `enabled`; `compute_bindings` `metadata`/`preload`; `_truncate_message` `max_length`; `resolve_linker_script_chain` `max_hops`; `_safe_urlopen` `timeout` | **Removed**; each limit became the named constant it always was. `anonymous_ordinal` was checked first, since its docstring said two anonymous sibling records would otherwise share one `EntityId`: castxml, clang JSON and DWARF each skip a record/enum with neither a name nor an owning typedef's name, so no unnamed declaration reaches the identity function. Its docstring now says so, and that a producer emitting one needs a discriminator first. `gate_supported`/`gate_reason` existed for `scan`. |
| workflow/policy/storage: `aggregate`/`aggregate_reports_dir` `on_missing_required`/`on_unexpected_target` (and `resolve_gate_policy`'s explicit tier); `_proc_tree_pids` `root`; `reconcile_release_public_surface` `has_baseline`; `acquire_release_surface` `version`; `ResolvedExecutionContext.from_plan` `evaluation_config`/`assurance`; `build_bundle_snapshot_from_metadata` `root`; the three diagnostics' `mitigation`; `resolve_release_exit_decision` `not_comparable_code`/`removed_required_library_code`; `compute_gate_decision` `legacy_exit_code` (and its legacy branch); `enforce_ast_cache_budget` `min_age_seconds`; `validate_bundle_archive_artifact_type` `expected`; `record_digest` `digest`; `canonical_json` `drop_capture_metadata`; `check_json_container_budget` `max_nesting_depth`; `_write_json_chunked` `depth`; `write_snapshot_text_stream` `decoded_size`; `check_reader_compatibility` `supported_package_format`/`supported_comparison_contract`; `_raw_scalar_lookup` `_visiting`; `check_single_env`/`resolve_dependencies` `max_file_size`; compat `_do_echo` `err`, `_resolve_headers_from_list` `skip_rules` | **Removed.** The aggregate report schema still accepts `effective_policy.source: "explicit"` so an older report validates; it is no longer emitted. `max_file_size` was the removed MCP `abi_deps` tool's bound. Removing `mitigation` exposed a wording bug: the coverage notice advised `contract.unresolved=warn` in the message that opens "Accepted by contract.unresolved=warn", and the scope notice advised `scope.on_incomplete: block` to a run already under `block`. Both now offer only a change that changes something; `tests/test_notice_advice_matches_setting.py` drives `compare` over {default, accepting, gating} x {compatible, breaking} (4 of 12 fail on the previous code). `check_reader_compatibility`'s `reader_extractor_generation`/`reader_resolver_generation` are **recorded as a known gap**: no reader passes one, no generation constant exists, and nothing reads `semantics_differ`, so ADR-062 D2's drift half is unbuilt. |
| buildsource: `build_check_id` `environment_id`; `build_header_only_graph` `ast_root`; `BuildSourcePack.from_embedded_dict` `root`; `find_pattern_facts`, `resolve_expected_source_inputs` and `resolve_source_inputs` `changed_paths` (with `path_changed`); `to_aggregate_manifest` `head_sha`; `parse_android_dump` `tu_id`; `assemble_source_tu` `diagnostics`; `resolve_source_extractor` `fallback`/`preference`; `compute_tu_cache_key` `schema_version`; `run_source_replay` `library`/`forced_public`/`target_id`, then `link_source_abi` `forced_public` and `select_compile_units`/`public_header_roots_for`/`_extract_one` `target_id`; `run_layout_tool` `timeout`; `dump_source_only` (and `_write_snapshot_output`, the CLI `embed_build_source`) `build_query`/`build_compile_db`/`build_targets`; the evidence adapters' `quiet`; `resolve_dump_collect_context` `inputs_pack`; `attach_build_context_for_parsed_headers` `compile_db`; `parse_sycl_metadata` `extra_plugin_paths` | **Removed.** `forced_public` is applied by the contract pipeline's overlay, which never needed L4's copy. The changed-path narrowing of the pre-scan's inputs had only `scan` as a caller; `SourceInputDisposition.EXCLUDED` stays in the vocabulary with no producer. `target` replay scope is now every unit attached to a build target, which is what every production call already got. `build_mode_from_signals`' `raw_producer`/`raw_comment`/`dwarf_language` are **kept and recorded as a known gap**: removing them would leave `detect_compiler_family`/`detect_cxx_standard` reachable only from tests, and no dump path sets `AbiSnapshot.build_mode` at all. |
| Kept, with the reason | Test seams: `run_inferred_build_query` `timeout`/`which`, `run_cc_wrapper` `runner`/`env`/`emit`, `meminfo_available_gib` `path`, `enforce_ast_cache_budget` `max_bytes`/`now`, `SuppressionList.check_expired_strict` `today`, `compact_json_stream`/`decode_and_select_frontend_context_from_path` `chunk_size`, `capture_variants` `dump`, `run_ast_passes` `passes`, plus the seams the report-renderer row keeps. `depfile_args_from_argv` `trusted_root`: the opt-in that lets a raw `@file` token expand only under a verified root; neither call site has one, so such a token is dropped, as its docstring says. `ingest_inputs_pack` `attribution`/`expected_target_id`: the ADR-053 D3 filter no caller performs yet, which is why the project workflow's resolver rejects an inferred pack (`tests/test_reusable_workflows_project_evidence.py`). Recomputed after these rows: 31 parameters on 19 functions, every one kept above or recorded as a known gap. |
| Documented-API list (parameters on functions the docs name): `dry_run_estimate.estimate_scan`'s `mode`/`source_method`/`depth`/`seeded`/`max_tus`/`compile_db`; `cli_buildsource.embed_build_source`'s `quiet`; `check_requested_depth_satisfied`'s `build_source`; `process_resources.python_parallelism`'s `diagnostics`; `inline_graph_fold.fold_archive_graph`'s `search_roots` | **Removed**, and the first was a bug: the `compare --dry-run` cost preview, `estimate_scan`'s one caller, re-derived what the run resolves. It picked its own compile DB, ignoring `.abicheck.yml`'s `build.compile_db` and falling back to the source tree after a `--build-info` directory with no DB (where the run collects nothing), and it never saw `--since`/`--changed-path`, so it stated and priced "target" scope for a run that replays the changed units. `buildsource.inline.plan_compile_db` is now the one statement of the compile-DB order (`_resolve_compile_db` executes what it names), `frontends/cli/compare_enrichment.resolve_compare_changed_seed` the one seed resolution, and `estimate_scan` takes the run's level, collect mode and seed. `tests/test_compare_dry_run_compile_db.py` checks the preview against the run's own collector over {no DB, conventional DB, config-named DB, both} x {no config, in-tree, `--config`, stale `--config`} x {no/with/without-DB `--build-info`} (29 of 48 fail on the previous code) and the stated scope over {binary, headers, build, source} x four seeds (3 of 16). `tests/scan_estimate_helpers.py`, an orphan once `scan`'s cost-model tests went, is deleted. `python_parallelism` now ignores an unparsable `ABICHECK_MEMBER_JOBS` silently, as every other sizing variable in the module does. **Kept:** `resolve_compatibility_evaluation_config`'s `api_spellings` (its reference page states why), `collect_inline_pack`'s cache dirs and `run_dump_request`'s `notify` (documented library capabilities), `read_legacy_snapshot_document`'s `artifact_id` (the selector for a multi-artifact package), `ObjectStore.put`'s `algorithm`, `save_snapshot`'s `compression`, `snapshot_to_json`'s `indent`, and the detector thresholds `min_removed`/`min_overloads`/`top_n`. |

## Stage F — recomputed after #1477

The same recording on `main` after #1477 (CLI cleanup Phase 9b–9d) listed
**8** dead functions and **2** new dead parameters, each left by that change
or by Stage E's own removals. Recomputed after the rows below: **0** dead
functions, and the parameter list is Stage E's 31 plus `run_no_baseline_set`'s
`audit`, kept.

| Item | Decision |
|---|---|
| `evidence_depth_levels.resolve_level`/`resolve_source_method`/`mode_preset`, with `ScanMode`, `_MODE_PRESET` and `UNPINNED_DEPTH` | **Deleted.** They were `scan`'s `--mode`/`--source-method` precedence resolver; only tests called them. Following them found a single-owner item: "explicit `--depth` → collect mode" had three copies (`cli_dump_depth.resolve_dump_depth`, `service_compare_evidence._resolve_depth_collect_mode`, `workflows/plan._depth_implied_collect_mode`), two of them saying they were "duplicated to stay a leaf", though `model/` is the leaf all three already imported. They agreed; `evidence_depth_levels.collect_mode_for_depth` is now the one answer, and `tests/test_evidence_depth_levels.py` checks all three callers against a table written from ADR-033 D2/ADR-043 D3 over every depth in every letter case. |
| `dry_run_estimate.expand_public_header_inputs`, `_compile_db_in` | **Deleted.** The first served `embed_side_build_source`'s `expand_public_header_roots`, which Stage E removed because only `scan` set it (L4's mirror detection samples a directory root itself, `clang_public_roots._public_root_samples`); the second was the cost preview's own compile-DB lookup, replaced by `buildsource.inline.plan_compile_db` in Stage E. |
| `cli_compare_options._cli_flag`, `no_baseline_compare.candidate_is_stored_snapshot`, `release_package.dso_only_filter_pair` | **Deleted.** Callers removed by #1477's directory `--no-baseline` and the release-matrix rewrite; nothing else asked. |
| `no_baseline_set.resolve_no_baseline_set_plan`'s `make_temp_dir` | **Removed**: no caller, test or production, passed one. |
| `no_baseline_set.run_no_baseline_set`'s `audit` | **Kept**, test seam: the per-member failure-isolation tests inject an audit that raises for one member. |


## Named only by an ADR or plan — rollout map

`usecase_paths.py dead` keeps a function with no production caller out of the
dead list when an ADR or plan names it. On the Stage F recording that is
**95** functions: 27 are the test hooks and inert primitives this plan already
decided above, and the other **68** are mapped here. Each one is in exactly
one of four groups, so a feature that is still rolling out is not counted as
dead, and is not mistaken for shipped either. Recompute with the same command;
a function that gains a production caller leaves this list by itself.

### Rolling out — planned, not yet wired into production

Each row is real remaining work: the owning document plans a production
consumer that does not exist yet.

| Item | Owner | Remaining work to reach production |
|---|---|---|
| `SemanticIR.occurrences_for`, `SemanticIRIndex.occurrences_for` | ADR-063 / [one-semantic-pipeline](one-semantic-pipeline.md) Phase 6 "PR 2" | Consumer cutover: `diff_symbols.py`/`diff_types.py` match through `SemanticIRIndex` instead of `AbiSnapshot.functions`/`variables`/`types`. |
| `EvidenceView.available_depths` | ADR-063 / one-semantic-pipeline (`ResolvedExecutionContext`) | The depth floor (`enforce_requested_depth`) should read the resolved `EvidenceView` rather than recompute. |
| `GateOptions.effective_gate`, `workflows.gate.effective_gate_for_resolved_compare_config` | ADR-061; [duplication-and-convergence-assessment](duplication-and-convergence-assessment.md) P0 `EffectiveGate` | The release fan-out and native `compare` still gate from their own severity fields; both should read one `EffectiveGate`. |
| `storage.import_baseline_set.import_baseline_set`/`export_baseline_set`, with `dto.baseline_set_metadata_from_dto`/`_to_dto` | ADR-062 (Proposed); [storage-format-v2](storage-format-v2.md); G40 | A baseline publish/load path in `compare` or `project` that goes through the BundleFacts→ProjectSnapshot adapter (streaming variant is a known gap). |
| `storage.entity_ids.elf_symbol_occurrence` | ADR-062 Phase 0 (storage-format-v2 A0.2/A0.3) | A storage-v2 ELF symbol-occurrence producer (later ADR-062 phases). |
| `binary_fingerprint.compute_function_fingerprints` | ADR-003 | `diff_symbols_renames.py`'s ELF-only rename path describes fingerprinting when a binary path is available; the call was never made. |
| `wheel_tags.parse_manylinux_glibc_floor`/`parse_musllinux_floor`/`parse_macos_deployment_target_floor` | [g27-wheel-deployment-verification](g27-wheel-deployment-verification.md) | Auto-derive `runtime_floors` from a compared wheel's own platform tag; today every floor needs an explicit `--env-matrix`. |
| `wheel_tags.parse_numpy_requirement_from_metadata`, `parse_wheel_numpy_requirement` | [g26-numpy-capi-envelope](g26-numpy-capi-envelope.md) | G26's "declared" side: `diff_numpy_capi` should read the wheel METADATA requirement through these. |
| `graph_backends.ingest_codeql_extends_results` | ADR-041 (partially phased), ADR-044 | L5 CodeQL collection calls it beside `ingest_codeql_call_results` when an extends-query result exists. |
| `acknowledgment_gate.fold_additions_review_exit` (live, but always `0`) | ADR-067 D6 | No front end passes `acknowledgments` to `checker.compare`, so the additions-review axis never fires. Wiring it needs an input (config key or flag), the axis inside `ExitDecision` (an `exit` block field and reason; today the CLI folds it after the decision, so the report's `exit.code` and the typed API would disagree with the process exit once it can fire), and the report schema bump that goes with that. |

Two rows first listed here were not wiring gaps. `legacy_record_ir` was a
wrapper: `compare/record_layout.py` already reads through
`legacy_record_occurrences`, and the plan named the wrapper, so the wrapper
is deleted and one-semantic-pipeline now names the real function.
`surface_facts.binary_export_match` is the reader of the export-match tier
producers stamp (ADR-063), used by the tests that check that tier; whether
weaker tiers should stop counting as exported is the separate policy
question ADR-063 leaves open, so it is kept as that question's inert reader.

`snapshot_digest_cache.digest_scope` was listed here as rolling out; it is
not. Both front ends open the same scope through `run_scoped_digest_cache`,
and the measurement and the H5 cell that now covers it are recorded in
design-hardening Phase 4, which this closes.

### Library API group — decided

The Python API is `abicheck.service.__all__`
(`docs/reference/python-api-reference.md` is generated from it). None of
these was in it, so each was decided rather than documented by default:

| Item | Decision |
|---|---|
| `TypeMetadataSource` and its accessors on `BtfMetadata`/`CtfMetadata`/`DwarfMetadata` (`get_struct_layout`, `get_enum_info`, `has_data`, and `get_function_proto`/`get_typedef`) | **Deleted.** Its docstring said detectors accept the protocol; none did, and nothing used it as an annotation. BTF and CTF reach the checker as `DwarfMetadata` through `to_dwarf_metadata()`. `tests/test_type_metadata.py`, which checked only protocol conformance, is deleted; the BTF/CTF parser tests now read the parsed tables directly. ADR-007 carries a dated amendment. |
| `wheel_tags.parse_manylinux_glibc_floor`/`parse_musllinux_floor`/`parse_macos_deployment_target_floor` | **Rolling out, not API:** G27's still-planned auto-derivation of `runtime_floors` from a compared wheel's own tag (today every floor comes from `--env-matrix`). Listed with `parse_wheel_architecture_claim`, which Stage D kept for the same entry point. |
| `inputs_emit.write_inputs_pack` | **Moved to `tests/_inputs_pack_writer.py`.** No producer writes a pack in one call; the `abicheck-cc` wrapper and the Clang plugin write incrementally through `init_inputs_pack`/`append_source_facts`, which the helper still uses, so its packs exercise the real format. |
| `project_snapshot_store.read_project_manifest` | **Moved to `tests/_project_manifest_reader.py`.** Every production reader goes through the lazy primitives (ADR-062 D8); the eager convenience was assembled from them and only tests loaded a whole manifest. |
| `EntityResolver.v1_id_for` | **Deleted.** The mapping it read is live (`resolve` detects conflicts with it); the tests now state the representative through that behaviour: a third node with the same identity conflicts with the first, not the second. |
| `snapshot_io.read_snapshot_storage_info` | **Kept, test hook:** the observation point the compression tests read a written envelope through, over the same `_classify_with_skippable_fallback` the read path uses. |
| `contract_replay.replay_original_decisions`, `workflows.input_resolution.load_env_matrix` | **Kept**, as Stage C already decided (ADR-049 D6's replay procedure; ADR-068's migration path for `env_matrix_path`). |

### Test hooks and oracles

Kept for the tests that assert through them; not rollout work.
`DecisionComparison.is_sound`, `contract_graph_encoding.resolve_graph_node`
(the slow reference resolver), `coverage_ledger.suppression_reaches_coverage_failures`
and `SuppressionList.is_suppressed` (the unsuppressibility proof), all under
[public-contract-default](public-contract-default.md);
`scope_segments.flat_names`, `legacy_function_ir` (one-semantic-pipeline);
`evidence_merge.presence_in`, `execution_cache.cache_stats`,
`model.name_heuristics.heuristic_callables`,
`policy.name_heuristics.name_heuristic_registry`/`registry_problems`
(design-hardening); `DetectorRegistry.detector_names` (G31);
`probe_harness._snapshot_object_file` (duplication-and-convergence, recorded
exception).

### Stale references — decided in Stage G

The documents naming these described a superseded or deleted path, so the
name kept dead code alive. Each was checked for a dropped wiring first; see
Stage G below for the decisions.

## Stage G — the stale references

| Item | Decision |
|---|---|
| `contract_coverage_exit.fold_coverage_exit`, `analysis_assurance.fold_analysis_assurance_exit` | **Deleted, superseded.** `policy.exit_decision.resolve_exit_decision` is the one fold: it takes `coverage_exit_floor` and `analysis_assurance_exit_contribution` and applies the `max`, so the wrappers were a second statement of that fold with no caller. Their tests now assert through `resolve_exit_decision`. (`acknowledgment_gate.fold_additions_review_exit` is still folded *outside* `ExitDecision`, in `frontends/cli/runtime.py`, after the decision is made, so `ExitDecision.reasons` cannot name the additions-review axis. It is live and is recorded here as the next single-owner item.) |
| `workflows.plan.scan_bazel_scoping_failure` | **Deleted.** Its callers went with `scan`; `compare`/`dump` carry the same guard as `_check_bazel_target_scoping`, with the same headers-or-collection rule. |
| `evidence_depth_levels.parse_user_depth` and its `symbols` alias | **Deleted.** It served `ScanRequest`; `collect_mode_for_depth` rejects `symbols`, as the three copies it replaced did. |
| `Suppression.selector_matches` | **Deleted.** Its named consumer, `ReclassifyRule`, matches through its own `SelectorSet`. |
| `ExportSet.destinations` | **Deleted** (no reader). |
| `cli_helpers_compare._build_match_map`, `frontends/cli/release_variant_operand.py` (`_resolve_release_package_side`) | **Deleted.** Both were Click-translating wrappers whose callers moved to the engine (`binary_utils.build_match_map`, `workflows.release_inputs.resolve_release_package_side`) with translation at `frontends.cli.release_compare_request`; five modules imported them without calling them. Their tests now assert the typed error from the engine function. |
| `storage.atomic_file.atomic_copy` | **Deleted.** It existed to stream the clang AST cache write; that write now streams through `storage/json_compact.py` and `json_chunked_write.py`, so the memory property it protected still holds. |
| `binary_fingerprint.compute_section_summary` with `BinarySummary`, `SectionSummary`, `_ABI_SECTIONS`, `_extract_section_summary` | **Deleted.** ADR-003 listed a section-hash triage that no detector or command consumed. |
| `fact_provenance.is_castxml_backed_fact`/`both_castxml_backed_fact` | **Deleted.** G31 Phase C replaced the castxml-only gate with `both_known_backed_fact`/`fact_producer`; the hybrid-merge tests now read provenance through `fact_producer`. |
| `evidence_depth._l5_payload_empty` | **Deleted.** It was a wrapper over `resolve_l5_source_graph`, and the callers already use that resolver directly. `layer_payload_empty`'s own L5 case reads `pack.source_graph` directly; its one caller (`cli_buildsource`) has a pack and no snapshot, so the resolver's snapshot fallback does not apply. |

The names are registered in `scripts/retired_surfaces.py`.
