# CLAUDE.md — `scripts/`

Maintenance and demo scripts. Not packaged; not part of the public API.
Each must run with Python 3.11+ and the package installed in dev mode
(`pip install -e ".[dev]"`).

## Inventory

One row per script: name, first sentence of its purpose, trigger. **Read the full row in [`docs/contribute/agent-guide/scripts-inventory.md`](../docs/contribute/agent-guide/scripts-inventory.md) before editing, wiring or relying on a script** — it carries the design history, flags and caveats each row used to inline here.

| Script | Purpose | Triggered by |
|---|---|---|
| `verify.py` | **The** verification orchestrator (CLAUDE.md "M0-3"). | Pixi (`pixi run check` = `--profile pr`), pre-commit (mypy + ai-readiness hooks), CI … |
| `run_isolated_module.py` | The isolated-module-lookup runner every Python-tool `-m` invocation in this directory shares (`verify.py`'s `_py()`, `check_ai_readiness.py`'s mypy-baseline check, `gen_repo_facts.py`'s … | imported/invoked as a subprocess entry point by the four scripts above. |
| `gen_repo_facts.py` | Single source of truth for volatile repository facts (CLAUDE.md "M1-4"). | `verify.py --only repo-facts` (CI `ai-readiness` job). |
| `check_ai_readiness.py` | AI-readiness gate (file size, CLAUDE.md coverage, test ratio, ChangeKind invariants, mypy baseline drift, import cycles, engine/CLI dependency-direction boundary, test-assertion density, ADR … | CI (`ai-readiness`) and `pre-commit`, both via `verify.py --only ai-readiness`. |
| `find_duplicate_tests.py` | Reports same-module `test_*` functions with byte-identical bodies (matching parameters and decorators too). | Manual. |
| `usecase_paths.py` | Which code each use case runs. | CI (`usecase-paths.yml`: PR base-vs-head diff; weekly full ranking and dead-code list); manual. |
| `usecase_adr_reach.py` | `usecase_paths.py`'s `adr-reach` and `ratchet` analyses. | imported (by `usecase_paths.py`); weekly `usecase-paths.yml` (`ratchet --strict` over a … |
| `production_references.py` | `usecase_paths.py dead`'s reference analysis, the rule of `docs/contribute/plans/dead-code-and-single-owner.md` made executable: an unreached function is **dead** when every reference to its name in … | imported by `usecase_paths.py dead`. |
| `usecase_flows.yaml` | Data for `usecase_paths.py record --source flows`: the catalog cases and the real CLI command lines (each tagged with a `docs/contribute/usecase-registry.yaml` id) run against them. | read by `usecase_paths.py`. |
| `check_architecture.py` | Focused ADR-061 gate for the stable responsibility-package graph and temporary no-growth debt ledger in `architecture/`: validates both schemas, new-file ceilings/names, frozen root prefix families … | `verify.py --profile pr` (`architecture` step). |
| `adr_status_sync.py` | The `adr-status-sync` gate, split out of `check_ai_readiness.py` (already past the 2000-line hard cap): an ADR's own `**Status:**` line and its row in `docs/contribute/adr/index.md` may not … | imported (by `check_ai_readiness.py`). |
| `engine_cli_boundary.py` | The `engine-cli-boundary` gate, split out of `check_ai_readiness.py` the same way `adr_status_sync.py` was (same 2000-line-cap reason): no engine-layer module (`scan_engine.py`, `service*.py` … | imported (by `check_ai_readiness.py`). |
| `fact_detector_misuse.py` | The `fact-detector-misuse` gate (ADR-063 Phase 0, `docs/contribute/plans/one-semantic-pipeline.md`), split out of `check_ai_readiness.py` the same way `fact_field_readers.py`/`engine_cli_boundary.py` … | imported (by `check_ai_readiness.py`). |
| `fact_detector_misuse_aliases.py` | The "does this expression/annotation/name resolve to a `Fact[T]` value" primitives `fact_detector_misuse.py` builds its top-level scan on. | imported (by `fact_detector_misuse.py`). |
| `fact_detector_misuse_scope.py` | The lexical-scope resolution primitives both `fact_detector_misuse.py` and `fact_detector_misuse_aliases.py` build their own alias resolution on. | imported (by `fact_detector_misuse.py`, `fact_detector_misuse_aliases.py`). |
| `fact_field_readers.py` | The `fact-field-readers` gate (ADR-063 Phase 0's "widened, non-glob AI-readiness check", `docs/contribute/plans/one-semantic-pipeline.md`), split out of `check_ai_readiness.py` the same way … | imported (by `check_ai_readiness.py`). |
| `fact_field_readers_scope.py` | Lexical-scope resolution primitives for `fact_field_readers.py`, split out once that module approached the 2000-line hard cap (mechanical extraction, not a redesign — mirrors … | imported (by `fact_field_readers.py`). |
| `fact_field_readers_scan.py` | The per-node matching half of `fact_field_readers.py`'s `unmigrated_fact_reader_sites`, split out to break up that function's single very complex body (radon CC 179): its … | imported (by `fact_field_readers.py`). |
| `fact_registry_completeness.py` | The `fact-registry-completeness` gate (ADR-063 D7/Phase 5, `docs/contribute/plans/one-semantic-pipeline.md`), mirroring `fact_field_readers.py`'s own extraction pattern. | imported (by `check_ai_readiness.py`). |
| `semantic_ir_cutover.py` | The `semantic-ir-cutover` gate (ADR-063 Phase 6B, `docs/contribute/plans/one-semantic-pipeline.md`), split out the same way `fact_field_readers.py`/`engine_cli_boundary.py` were. | imported (by `check_ai_readiness.py`). |
| `perf_antipatterns.py` | The `perf-antipatterns` gate (AST, `abicheck/` only), a sibling leaf module of `check_ai_readiness.py`. | imported (by `check_ai_readiness.py`); gated via `tests/test_perf_antipatterns.py`; manual. |
| `audit_repeated_calls.py` | Finds first-party functions called repeatedly **with the same arguments** inside one `compare()` (plain values by value, other objects by identity; generator resumptions are not counted as calls) -- the missed-memoization question a call count cannot answer. | manual; budgets gated via `tests/test_compare_cost_budgets.py`, the whole-package ratchet via … |
| `complexity_bench.py` | Empirical complexity exponents of hot paths: `compare()` by symbol count and by type count, add/remove, rename matching, policy classification, history by release count. | `verify.py --only complexity-bench` (PR profile; CI `ai-readiness` job); gated via … |
| `perf_report.py` | Optimization-backlog report (markdown): hot first-party functions by cumulative time over every synthetic workload (`--corpus N` adds a dumped real C++ library), same-argument repeats ranked by cost … | manual; weekly in `performance.yml` (step summary + artifact). |
| `mypy_override_targets.py` | The `mypy-override-targets` gate, split out of `check_ai_readiness.py` the same way `adr_status_sync.py`/`engine_cli_boundary.py` were (2000-line hard cap). | imported (by `check_ai_readiness.py`). |
| `no_inline_gate_computation.py` | The `no-inline-gate-computation` gate (ADR-063 Phase 7, WARN, `docs/contribute/plans/one-semantic-pipeline.md`), split out the same way `engine_cli_boundary.py`/`adr_status_sync.py` were. | imported (by `check_ai_readiness.py`). |
| `findings_report.py` | The shared `Findings` error/warning collector both gate scripts report through. | imported (by `check_ai_readiness.py`, `check_docs_contract.py`). |
| `check_fp_rate.py` | False-positive/false-negative gate for public-surface scoping (ADR-024 §7). | CI (`ai-readiness`). Mirrored in `tests/test_fp_rate_gate.py`. |
| `check_tier_accuracy.py` | Per-evidence-tier accuracy gate — *what each level buys*. | CI (`ai-readiness`, + step-summary artifact). |
| `measure_contract_shadow.py` | ADR-049 Phase 3's shadow contract-evaluator measurement **and** gate. | Manual / CI step summary. |
| `contract_platform_corpus.py` | ADR-049 Phase 6's platform/shape corpus for the measurement above: hand-built snapshot pairs carrying real ELF `.dynsym` / PE export-directory / Mach-O export-trie shapes, plus the stripped … | imported (by `measure_contract_shadow.py`). |
| `gen_skill_eval_pack.py` | Builds `skills-src/evaluation/agents/skills/skill-eval-pack.json`. | `verify.py --only skill-eval-pack` (CI `ai-readiness` job). |
| `skill_eval_surface.py` | The abicheck surface a published skill can invoke or read. | imported (by `check_skill_eval_freshness.py`). |
| `check_skill_eval_freshness.py` | G37 D6's freshness gate, and the reason "the evaluation must postdate the skill trees it grades" stopped being prose nobody enforced. | `verify.py --only skill-eval-freshness` (CI `ai-readiness` job). |
| `gen_harbor_tasks.py` | Generates `skills-src/evaluation/agents/skills/harbor/tasks/`. | `verify.py --only harbor-tasks` (CI `ai-readiness` job). |
| `check_usecase_docs_sync.py` | Drift gate between `docs/contribute/usecase-registry.yaml` (the machine-checked source of truth) and its hand-maintained human summaries. | CI (`ai-readiness`). Mirrored in `tests/test_usecase_docs_sync.py`. |
| `check_adr_surfaces.py` | ADR-076's traceability gate: `docs/contribute/adr/adr-surface-registry.yaml` gives every ADR a disposition (`surfaced`/`partial`/`gap`/`internal`), its `UC-*` use cases and the CLI/API/Action/report surfaces that reach it. | `verify.py --only adr-surfaces` (CI `ai-readiness` job); manual after touching an ADR, a cited … |
| `check_docs_contract.py` | `docs/AGENTS.md`'s ownership contract, made machine-checkable: `docs/_meta/topics.yaml` integrity (every referenced path exists, no two topics claim the same `canonical_page`), page front-matter … | CI (`ai-readiness`). Mirrored in `tests/test_docs_contract.py` (+ … |
| `pipeline_status_ledger.py` | The `pipeline-status-ledger` gate (ADR-063 "PR 0"'s machine-readable authority matrix), split out of `check_docs_contract.py` the same way `adr_status_sync.py`/`engine_cli_boundary.py` were split out … | imported (by `check_docs_contract.py`). |
| `retired_surfaces.py` | `check_docs_contract.py`'s retired-CLI-surface registry (`RETIRED_SURFACES`) and its WARN-only sweep, split out the same way `pipeline_status_ledger.py`/`adr_status_sync.py`/`engine_cli_boundary.py` … |  |
| `config_key_operands.py` | `check_docs_contract.py`'s `config-key-as-cli-operand` sweep, split out the same way `retired_surfaces.py` was (its caller sits against the `file-size` soft limit, and CLAUDE.md's rule for a file at … | imported (by `check_docs_contract.py`). |
| `check_docs_review_triggers.py` | Advisory-only "review trigger" for `docs/AGENTS.md`'s front-matter `depends_on` field: diffs a PR's changed files against every docs page's `depends_on` list and reports the overlap as an … | CI (`docs-review-triggers.yml`, PR-only). |
| `check_examples_validation_status_sync.py` | Keeps `examples/README.md`'s "Current Validation Status" table's hand-typed "Result" prose honest against the latest gcc/clang/runtime/release/stripped/build-source validator run data. | CI (`examples-validation.yml`, path-filtered). |
| `check_build_source_release_proof.py` | Fails closed unless the fixed ten-case build/source artifact has exactly the expected full ground-truth case IDs, one `PASS` row for each, and the matching selected-case count. | `verify.py --profile full --only build-source-release-proof` in CI after producing … |
| `check_fair_metadata.py` | Checks local, deterministic FAIR-facing metadata (`CITATION.cff`/`codemeta.json`/`.zenodo.json`) and the published JSON Schema contract for internal consistency. | `verify.py --only fair-metadata` (CI `fair-metadata` job). |
| `check_distribution_metadata.py` | Validates a built sdist/wheel in `dist/` against `pyproject.toml` (name/version/summary/requires-python/license/Project-URLs), confirms the sdist carries … | `verify.py --only distribution-build` (via `build_and_check_distribution.py`). |
| `build_and_check_distribution.py` | Builds the sdist/wheel (isolated `build` module invocation via `run_isolated_module.py`), `twine check`s the resolved artifact paths (no shell glob), then runs `check_distribution_metadata.py`. | `verify.py --only distribution-build` (CI `fair-metadata` job). |
| `publish_schemas.py` | Synchronizes the package's JSON Schema copies (source of truth) into the MkDocs-published `docs/reference/schemas/v1` tree at their stable, versioned `$id` URLs. | `verify.py --only schema-sync` (CI `fair-metadata` job). |
| `check_bugfix_test_contract.py` | Bug-fix test contract gate. | CI (`bugfix-test-contract.yml`, on PR). |
| `check_mutation_score.py` | Mutation-score gate, three lanes: `--diff-scoped` (absolute — any surviving mutant in a function the branch changed fails, no baseline needed; a diff that changed no production function and has no … | CI (`mutation.yml`: PR path-filter → diff-scoped (scoped to the touched module(s) unless … |
| `mutation_results.py` | Leaf parsing/attribution layer under `check_mutation_score.py`: reads `mutmut results`' per-mutant listing, maps a mangled mutant key (`pkg.mod.xǁClsǁmeth__mutmut_3`) back to its source module and … | Imported by `check_mutation_score.py`. |
| `gen_mutation_test_selection.py` | Generates (or `--check`s) `tests/mutation_test_selection.txt`, the pytest `@argfile` `[tool.mutmut].pytest_add_cli_args_test_selection` reads: the test files with at least one test that calls … | `mutation.yml` (`selection-check` job). |
| `mutation_reach_trace.py` | pytest plugin (`MUTATION_REACH_OUT=<dir>`) recording which tests execute `only_mutate` code: `sys.monitoring` `PY_START` events armed only on those modules' code objects (re-armed per test with … | Loaded by `gen_mutation_test_selection.py`. |
| `mutmut_stable_param_ids.py` | pytest plugin loaded only into the mutation lane's sessions (`[tool.mutmut].pytest_add_cli_args`: `-p scripts.mutmut_stable_param_ids`). | `[tool.mutmut]` (every mutmut pytest session). |
| `mutation_scope.py` | Run-cost primitives for the mutation lane, sibling leaf of `check_mutation_score.py` (which owns gating and sits near the 2000-line cap). | `mutation.yml` (shard matrix, resolve job, baseline merge). |
| `check_changelog_fragment.py` | Fails a PR that touches … | CI (`changelog-check.yml`, on `opened`/`synchronize`/`reopened`/`labeled`/`unlabeled`). |
| `gen_examples_docs.py` | Regenerates `docs/reference/examples/caseNN_*.md` from `examples/case*/README.md`, and the generated regions (headline, verdict distribution, case index) of `examples/README.md` from … | `verify.py --only examples-docs` (CI `lint-and-types` job; runs `--check` with the … |
| `gen_detector_spec.py` | Regenerates the formal detector specification matrix (`docs/reference/detector-spec.{md,json}`) by fusing per-`ChangeKind` category (`checker_policy`), default verdict/severity/doc-slug … | manual |
| `gen_action_reference.py` | Regenerates the exhaustive GitHub Action inputs/outputs reference (`docs/reference/github-action-inputs.md`) from `action.yml`'s `description`/`required`/`default` fields. | manual |
| `gen_cli_reference.py` | Regenerates the exhaustive CLI command/option reference (`docs/reference/cli-reference.md`) from the live Click command tree (`abicheck/cli.py` + registered sibling modules). | manual |
| `gen_python_api_reference.py` | Regenerates the exhaustive Python API reference (`docs/reference/python-api-reference.md`) from every name in `abicheck.service.__all__`. | manual |
| `gen_config_reference.py` | Regenerates the exhaustive `.abicheck.yml` key/type reference (`docs/reference/config-keys-reference.md`) from `abicheck.buildsource.build_config.BuildConfig`'s strict-schema registries … | manual |
| `gen_agent_skills.py` | Publishes `skills-src/` as self-contained Agent Skills (ADR-058 / G36 P0.3) into the three generated trees `.agents/skills/`, `.claude/skills/`, and `.gemini/skills/`. | `verify.py --only agent-skills-generated` (CI: the `ai-readiness` job's second gate step … |
| `install_dev_skill.py` | Thin CLI wrapper around `gen_agent_skills.py`'s `render_all`/`write_trees` for local dev use: materializes the generated skill tree(s) on disk (`.agents/skills/`, `.claude/skills/`, `.gemini/skills/` … | manual, after a `skills-src/` edit, before exercising an installed skill locally. |
| `action_cli_mirror.py` | The `action-cli-mirror` AI-readiness check (ADR-070 D4): resolves every `# cli-mirror: <path>::<symbol>` annotation in the Action shell layer, so a comment justifying a guard by naming a CLI symbol … | imported |
| `gen_action_cli_surface.py` | Generates `action/cli-surface.txt` — the CLI `click.Choice` sets `action/validate-inputs.sh` needs **before** abicheck is installed. | manual |
| `gen_learning_ladder.py` | Renders the learning-series hub's numbered step list and role-path table. | manual |
| `gen_platform_matrix.py` | Regenerates the host-OS × binary-format capability matrix. | manual |
| `gen_backend_capability_matrix.py` | Regenerates the L2 header-backend × ABI-fact capability matrix. | manual |
| `gen_fact_capability_matrix.py` | Regenerates the fact/capability registry doc (`docs/reference/fact-registry.md`, ADR-063 D7/Phase 5). | manual |
| `gen_stable_abi_data.py` | Regenerates the vendored CPython Stable-ABI membership set (`abicheck/stable_abi_data.py`) from a `Misc/stable_abi.toml` (local path or `--url`). | manual |
| `gen_changekind_stub.py` | Regenerates `abicheck/model/change_catalog/kinds.pyi`, the mypy-only PEP 484 stub for `ChangeKind` (ADR-061 D9 model-vs-policy split). | manual; drift enforced by `tests/test_changekind_stub.py` (calls `--check` directly … |
| `example_catalog.py` | Phase 3 of the examples/catalog split (retired plan record, see `docs/contribute/plans/index.md`; **complete**): the single … | imported by every consumer in the codebase that resolves an example case's path, reads … |
| `gen_catalog_taxonomy.py` | Phase 1 + Phase 2 of the examples/catalog split (retired plan record, see `docs/contribute/plans/index.md`): generates `catalog/taxonomy.json`. | manual; run after adding/reclassifying a case. |
| `catalog_classification.py` | The declarative per-case `entity`/`scenario_kind`/`ecosystem` manifest loader/validator for `gen_catalog_taxonomy.py`. | imported (by `gen_catalog_taxonomy.py`). |
| `catalog_subjects.py` | The declarative, hand-authored `subjects` manifest loader/validator for `gen_catalog_taxonomy.py` (examples/catalog split's "What is left" item 2 — retired plan record, see … | imported (by `gen_catalog_taxonomy.py`, `gen_examples_docs.py`). |
| `workflow_examples.py` | Phase 5 of the examples/catalog split: the `examples/workflows/<id>/workflow.yaml` schema, its loader, and `readme_drift()`. | imported (by `gen_catalog_coverage_report.py` … |
| `catalog_rule_registry.py` | The canonical compatibility-rule registry's loader and its join against the catalog taxonomy (`catalog/catalog_rules.yaml` -> slug/title/definition; `build_families()` derives each rule's canonical … | imported (by `gen_catalog_taxonomy.py`, `gen_catalog_coverage_report.py` … |
| `gen_catalog_coverage_report.py` | Phase 6 of the examples/catalog split (retired plan record, see `docs/contribute/plans/index.md`): generates `docs/contribute/catalog-coverage.md`, reporting the 197-case catalog along five … | manual; run after any `ground_truth.json` change. |
| `abi_taxonomy_coverage.py` | Phases 2/3 of `docs/contribute/plans/abi-api-knowledge-and-corpus.md`: the parser for Phase 1's frozen Markdown taxonomy (`docs/contribute/abi-api-failure-taxonomy.md` — 88 leaf mechanisms read back … | imported (by `check_ai_readiness.py`, `gen_abi_taxonomy_coverage.py`). |
| `gen_abi_taxonomy_coverage.py` | Generates `docs/contribute/abi-taxonomy-coverage.md`, the Phase 2/3 coverage matrix: every taxonomy leaf's `learn_pages`/`catalog_cases`/`detector` columns and its one Phase 3 status, headlined by … | manual; run after editing `docs/_meta/abi-taxonomy-coverage.json` or the Phase 1 taxonomy. |
| `gen_g20_fixtures.py` | Single source of truth for the committed G20 snapshot fixtures (`catalog/cases/case143–151`, ADR-035 / plan `g20-source-scan-example-catalog`). | manual |
| `fixture_sync.py` | The write-or-`--check` driver the committed-snapshot generators share: the walk over `{case: {filename: builder}}`, serialization, byte-comparison, drift reporting, exit code. | imported (by `gen_g20_fixtures.py`, `gen_reachability_examples.py`). |
| `gen_l3l4l5_examples.py` | Single source of truth for the L3/L4/L5 build/source-only example fixtures. | manual |
| `gen_reachability_examples.py` | Generates the committed snapshot fixtures for the reachability-aware-suppression example cases (192–193): the headline scenario end to end, and its deliberate counter-example. | manual |
| `benchmark_comparison.py` | Benchmarks abicheck vs ABICC / libabigail across the `examples/` catalog. | manual |
| `generate_benchmark_report.py` | Wraps `benchmark_comparison.py` into one reproducible report: JSON + Markdown envelope (git commit, ground-truth sha256, tool versions, case list, per-lane accuracy/FP/FN/unsupported-error-timeout … | manual |
| `evidence_tiers.py` | Single source of truth for the five-source / L0–L4 evidence model: `EVIDENCE_TIER_BY_KIND` + `KINDLESS_CASE_TIER` compute each case's `min_evidence` (stored in `catalog/ground_truth.json`). | imported |
| `learning_ladder.py` | The learning series' reading order as data (`docs/_meta/learning-ladder.yaml`: two sequences, steps with a `floor:`, members, branches, links, role paths) and the `learning-ladder` docs-contract … | imported |
| `learning_nav_order.py` | The `learning-nav-order` AI-readiness gate, split out of `check_ai_readiness.py` (past the 2000-line cap): each nav group under the ABI/API Compatibility and Concepts tabs must be non-decreasing in … | imported (by `check_ai_readiness.py`, `learning_ladder.py`) |
| `platform_capabilities.py` | Single source of truth for the host-OS × binary-format capability matrix (ADR-051 Stage 2): per binary format (ELF/PE/Mach-O), each host OS's symbol-diff/type-param-diff capability and required … | imported |
| `backend_capabilities.py` | Single source of truth for the L2 header-backend capability matrix (G31 Phase D): one row per field of the six declaration dataclasses `dumper_castxml.py`/`dumper_clang.py` build, plus … | imported |
| `evidence_benchmark.py` | ADR-033 Phase 7 performance & false-positive report: times the compiler-free inline collection path per collect mode and prints the FP-rate gate's D9 delta metrics. | manual / CI report |
| `dump_cli_surface.py` | Dumps the full `abicheck` Click command tree (commands/subcommands, options, arguments — names, flags, defaults, types, choices) to JSON. | CI (`cli-interface-check.yml`). |
| `diff_cli_surface.py` | Diffs two `dump_cli_surface.py` JSON outputs and reports added/removed commands and added/removed/changed options. | CI (`cli-interface-check.yml`). |
| `benchmark_scaling.py` | Synthetic scaling benchmark for the pipeline. | CI (`performance.yml`: weekly / `performance` label / dispatch / PR classified as … |
| `check_header_graph_perf.py` | G31 Phase D header-graph attach-cost perf gate: isolates and times `service._attach_header_graph`'s marginal cost (the always-on L2 semantic-graph build, G31 Phase A) across a synthetic header-size … | CI (`performance.yml`: `header-graph-perf` report-only trend job on weekly/dispatch, the … |
| `bench_release_memory.py` | Reproducible memory/time benchmark for a header-depth multi-library `compare`. | manual; before/after measurement for memory work |
| `bench_extraction_scope.py` | Phase 0 harness of `docs/contribute/plans/target-ownership-and-extraction-scope.md`. | manual; rerun by every phase of that plan |
| `bench_graph_materialization.py` | Evidence-entity-model Phase 5 (invariant I6) measurement: what the header graph and the public-surface graph cost over the plain snapshot. | manual |
| `export_orphan_breakdown.py` | Evidence-entity-model Phase 4 (invariant I4) measurement: for one stored snapshot, runs the `exports` edge query (`compare.edge_query.EdgeEvidence`) over every declaration and every observed export … | manual |
| `perf_cache_reset.py` | Cold-cache reset for `benchmark_scaling.py`'s memory passes (`clear_process_caches()`: every live `lru_cache` plus the demangle batch cache), split out purely to keep that file under the file-size … | imported (by `benchmark_scaling.py`). |
| `perf_baseline.py` | Baseline-regression comparison for `benchmark_scaling.py`, split out purely to keep that file under the file-size gate's line cap: `baseline_points_from_report()` (parses a scaling-benchmark JSON … | imported (by `benchmark_scaling.py`). |
| `check_l2_cli_perf.py` | **Full-CLI L2 perf harness** -- the third and outermost of this repo's three perf measurement levels (`benchmark_scaling.py` = synthetic/in-process/compiler-free; `check_header_graph_perf.py` = real … | CI (`performance.yml`: `l2-cli-perf` on perf-sensitive PRs; `l2-cli-extended` … |
| `check_l2_scaling_perf.py` | **Full-CLI L2 scaling gate** -- the growth-rate sibling of `check_l2_cli_perf.py`. | CI (`performance.yml`: `l2-cli-perf` job, after the PR-vs-base step). |
| `l2_cli_argv.py` | The argv the full-CLI L2 harness measures -- the third 2000-line-cap split out of `check_l2_cli_perf.py`, and the narrowest seam left (argv construction depends on the fixture shape and nothing else). | imported (by `check_l2_cli_perf.py`). |
| `l2_cli_validation.py` | What the full-CLI L2 harness *asserts* about a run's output, split out of `check_l2_cli_perf.py` once that file crossed the `file-size` gate's 2000-line hard cap (mechanical extraction, unchanged … | imported (by `check_l2_cli_perf.py`). |
| `l2_cli_gating.py` | How the same harness turns receipts into a pass or a failure -- the third piece of that split (what it asserts / what it measures / how it gates). | imported (by `check_l2_cli_perf.py`). |
| `l2_cli_model.py` | The innermost types the full-CLI L2 harness is built from -- `Step`, `Scenario`, and `EXTRACTION_EXPECTATIONS` -- extracted on the fourth crossing of the `file-size` gate's 2000-line hard cap. | imported (by `check_l2_cli_perf.py`). |
| `l2_cli_observation.py` | What the full-CLI L2 harness *concludes from an observed native-invocation count* -- the fifth 2000-line-cap split, and an inward seam like `l2_cli_model.py`: every function takes a `CommandRun` plus … | imported (by `check_l2_cli_perf.py`). |
| `l2_cli_fixture.py` | The generated real C++ fixture `check_l2_cli_perf.py` measures against -- a real `.so` with real public headers built by a real compiler, not a hand-built `AbiSnapshot` (a synthetic snapshot would … | imported (by `check_l2_cli_perf.py`). |
| `l2_real_profiles.py` | The pinned real-integration L2 profiles (oneDAL PR #3693, SVS released-v0.4.0-vs-PR-#387 and its separate PR-base smoke test, PVXS PR #216) as **declarative data**: each profile's compared operands … | imported (by the periodic lane). |
| `perf_receipt.py` | The shared, versioned performance *receipt* layer the perf harnesses emit, so the three measurement levels above stop reinventing the apparatus around them while staying separate as scenarios: run … | imported (by `check_l2_cli_perf.py`). |
| `perf_measurement.py` | Shared repeated-measurement statistics for the two perf-gate scripts above: `summarize_samples()` (median/min/max/p95/coefficient-of-variation over a list of timing samples — the median is the value … | imported (by `benchmark_scaling.py`, `perf_baseline.py`, `check_header_graph_perf.py`). |
| `classify_perf_paths.py` | The classifier-job pattern for `performance.yml`'s PR-triggered jobs (`scaling`/`regression`/`l2-cli-perf` (which also runs the header-graph PR gate), plus `header-graph-perf` on schedule/dispatch) … | CI (`performance.yml`'s `classify` job, gating its downstream jobs). |
| `demo_libz.py` | End-to-end demo on libz. | CI (`integration.yml` Linux leg, via `verify.py --profile full --only demo-libz`) |
| `summarize_test_durations.py` | Renders the per-test durations captured by the `tests/conftest.py` `ABICHECK_DURATIONS_JSON` hook into a Markdown table (GitHub run summary in CI, stdout locally). | CI (`unit-tests`, Linux/3.13) |
| `extract_bundle_manifest.py` | Extracts a manifest from multi-library bundles (cases 90–93). | manual |
| `restore_git_mtimes.py` | Restores each tracked file's mtime to its last-commit timestamp (one `git log` walk, not one subprocess per file). | CI (`examples-validation.yml`). |
| `setup_dev_env.sh` | Vendor-neutral dev-environment bootstrap: `pip install -e ".[dev,docs,dist]"` into an isolated venv (`DEV_VENV_DIR`, `dev_venv_pin.env` below — avoids clobbering dpkg-owned Python packages on … | `.claude/hooks/session-start.sh`. |
| `dev_venv_pin.env` | The single source of truth for where `setup_dev_env.sh` creates its isolated dev venv (`DEV_VENV_DIR`) and which interpreter it uses (`dev_venv_python()` — prefers `python3.13`, AGENTS.md's canonical … | sourced by `setup_dev_env.sh`, `.claude/hooks/session-start.sh`. |
| `castxml_pin.env` | The single source of truth for `setup_dev_env.sh`'s conda-forge `castxml` pin. | sourced by `setup_dev_env.sh`, `.claude/hooks/session-start.sh`. |

## Conventions

- **Pure stdlib** for anything that may run before `pip install` (e.g.
  `check_ai_readiness.py` — it's the first CI step).
- **`from __future__ import annotations`** at the top of every script.
- **No global side effects** at import time — gate behavior on
  `if __name__ == "__main__":`.
- **Exit codes**: 0 on success, 1 on any check/operational failure.
  Demo scripts may print to stdout but should not write outside the repo
  tree without an explicit flag.

## Adding a new script

1. Place it here; give it an executable shebang (`#!/usr/bin/env python3`).
2. Add a row to the inventory table above.
3. If it is a pass/fail gate, add a `Step` to `verify.py`'s `STEPS` catalog
   with the right `profiles` membership instead of wiring the raw command
   into CI/pre-commit directly — `verify.py` is what CI, pre-commit, and
   `pixi run check` all call through (CLAUDE.md "M0-3"). Reporting-only
   scripts (benchmarks, summaries) still wire directly into
   `.github/workflows/ci.yml`.
4. Document its arguments via `argparse` so `--help` is enough for an
   agent to use it.
