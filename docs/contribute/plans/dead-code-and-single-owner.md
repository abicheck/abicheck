# Dead code and single owners

Status: Stage A done (PR #1448); Stage B item 1 and Stage C done; Stage B
items 2-3 continue in [usecase-path-tracing](usecase-path-tracing.md);
Stage D's first pass done; Stage E's parameter pass added, its list not
yet decided.

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
| `_CastxmlParser` delegation shims (`dumper_castxml.py`, 20 incl. the ones the name matcher kept alive through a same-named method elsewhere, and the unread `_FUNCTION_TAGS`) | **Deleted.** Each forwarded to an `extract/headers/castxml/*` owner; the tests that reached through one now call the owner with `parser._ctx`. |
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

Next: decide each, module by module, the way Stages C and D did: remove the
parameter (and whatever it threads), keep a test seam, or keep a tunable
with the page or test that names it. Recompute the list rather than
editing a copy of it.
