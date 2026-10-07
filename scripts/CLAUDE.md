# CLAUDE.md — `scripts/`

Maintenance and demo scripts. Not packaged; not part of the public API.
Each must run with Python 3.11+ and the package installed in dev mode
(`pip install -e ".[dev]"`).

## Inventory

One line per script. The full purpose, design notes and trigger for each
row live in [`docs/contribute/agent-guide/scripts-inventory.md`](../docs/contribute/agent-guide/scripts-inventory.md):
read a script's full row before changing the script or its CI wiring, and
keep both tables in step when you add or remove a script.

| Script | Purpose |
|--------|---------|
| `verify.py` | The verification orchestrator (CLAUDE.md "M0-3"). |
| `run_isolated_module.py` | The isolated-module-lookup runner every Python-tool `-m` invocation in this directory shares (`verify.py`'s `_py()`, `check_ai_readiness.py`'s mypy-baseline … |
| `gen_repo_facts.py` | Single source of truth for volatile repository facts (CLAUDE.md "M1-4") — project/latest-release version, example-catalog size, fast-lane test count, … |
| `check_ai_readiness.py` | AI-readiness gate (file size, CLAUDE.md coverage, test ratio, ChangeKind invariants, mypy baseline drift, import cycles, engine/CLI dependency-direction … |
| `find_duplicate_tests.py` | Reports same-module `test_*` functions with byte-identical bodies (matching parameters and decorators too). |
| `usecase_paths.py` | Which code each use case runs. |
| `production_references.py` | `usecase_paths.py dead`'s reference analysis, the rule of `docs/contribute/plans/dead-code-and-single-owner.md` made executable: |
| `usecase_flows.yaml` | Data for `usecase_paths.py record --source flows`: |
| `check_architecture.py` | Focused ADR-061 gate for the stable responsibility-package graph and temporary no-growth debt ledger in `architecture/`: |
| `adr_status_sync.py` | The `adr-status-sync` gate, split out of `check_ai_readiness.py` (already past the 2000-line hard cap): |
| `engine_cli_boundary.py` | The `engine-cli-boundary` gate, split out of `check_ai_readiness.py` the same way `adr_status_sync.py` was (same 2000-line-cap reason): |
| `fact_detector_misuse.py` | The `fact-detector-misuse` gate (ADR-063 Phase 0, `docs/contribute/plans/one-semantic-pipeline.md`), split out of `check_ai_readiness.py` the same way … |
| `fact_detector_misuse_aliases.py` | The "does this expression/annotation/name resolve to a `Fact[T]` value" primitives `fact_detector_misuse.py` builds its top-level scan on — … |
| `fact_detector_misuse_scope.py` | The lexical-scope resolution primitives both `fact_detector_misuse.py` and `fact_detector_misuse_aliases.py` build their own alias resolution on — … |
| `fact_field_readers.py` | The `fact-field-readers` gate (ADR-063 Phase 0's "widened, non-glob AI-readiness check", `docs/contribute/plans/one-semantic-pipeline.md`), split out of … |
| `fact_field_readers_scope.py` | Lexical-scope resolution primitives for `fact_field_readers.py`, split out once that module approached the 2000-line hard cap (mechanical extraction, not a … |
| `fact_field_readers_scan.py` | The per-node matching half of `fact_field_readers.py`'s `unmigrated_fact_reader_sites`, split out to break up that function's single very complex body … |
| `fact_registry_completeness.py` | The `fact-registry-completeness` gate (ADR-063 D7/Phase 5, `docs/contribute/plans/one-semantic-pipeline.md`), mirroring `fact_field_readers.py`'s own … |
| `semantic_ir_cutover.py` | The `semantic-ir-cutover` gate (ADR-063 Phase 6B, `docs/contribute/plans/one-semantic-pipeline.md`), split out the same way … |
| `perf_antipatterns.py` | The `perf-antipatterns` gate (AST, `abicheck/` only), a sibling leaf module of `check_ai_readiness.py`. |
| `audit_repeated_calls.py` | Finds first-party functions called repeatedly with the same arguments inside one `compare()` (plain values by value, other objects by identity; |
| `perf_report.py` | Optimization-backlog report (markdown): |
| `mypy_override_targets.py` | The `mypy-override-targets` gate, split out of `check_ai_readiness.py` the same way `adr_status_sync.py`/`engine_cli_boundary.py` were (2000-line hard cap). |
| `no_inline_gate_computation.py` | The `no-inline-gate-computation` gate (ADR-063 Phase 7, WARN, `docs/contribute/plans/one-semantic-pipeline.md`), split out the same way … |
| `findings_report.py` | The shared `Findings` error/warning collector both gate scripts report through — grouping by check name, the `ERROR:`/`WARN:` rendering, and the `<label>: |
| `check_fp_rate.py` | False-positive/false-negative gate for public-surface scoping (ADR-024 §7). |
| `check_tier_accuracy.py` | Per-evidence-tier accuracy gate — *what each level buys*. |
| `measure_contract_shadow.py` | ADR-049 Phase 3's shadow contract-evaluator measurement and gate. |
| `contract_platform_corpus.py` | ADR-049 Phase 6's platform/shape corpus for the measurement above: |
| `gen_skill_eval_pack.py` | Builds `skills-src/evaluation/agents/skills/skill-eval-pack.json` — the one artifact the skill evaluation runs against and the one interface … |
| `skill_eval_surface.py` | The abicheck surface a published skill can invoke or read — the CLI reference, the detector spec, the compare-report schema — and its digest (G37 D6). |
| `check_skill_eval_freshness.py` | G37 D6's freshness gate, and the reason "the evaluation must postdate the skill trees it grades" stopped being prose nobody enforced. |
| `gen_harbor_tasks.py` | Generates `skills-src/evaluation/agents/skills/harbor/tasks/` — one [Harbor](https://www.harborframework.com) task directory per `status: |
| `check_usecase_docs_sync.py` | Drift gate between `docs/contribute/usecase-registry.yaml` (the machine-checked source of truth) and its hand-maintained human summaries — the "Gaps that … |
| `check_docs_contract.py` | `docs/AGENTS.md`'s ownership contract, made machine-checkable: |
| `pipeline_status_ledger.py` | The `pipeline-status-ledger` gate (ADR-063 "PR 0"'s machine-readable authority matrix), split out of `check_docs_contract.py` the same way … |
| `retired_surfaces.py` | `check_docs_contract.py`'s retired-CLI-surface registry (`RETIRED_SURFACES`) and its WARN-only sweep, split out the same way … |
| `config_key_operands.py` | `check_docs_contract.py`'s `config-key-as-cli-operand` sweep, split out the same way `retired_surfaces.py` was (its caller sits against the `file-size` soft … |
| `check_docs_review_triggers.py` | Advisory-only "review trigger" for `docs/AGENTS.md`'s front-matter `depends_on` field: |
| `check_examples_validation_status_sync.py` | Keeps `examples/README.md`'s "Current Validation Status" table's hand-typed "Result" prose honest against the latest … |
| `check_build_source_release_proof.py` | Fails closed unless the fixed ten-case build/source artifact has exactly the expected full ground-truth case IDs, one `PASS` row for each, and the matching … |
| `check_fair_metadata.py` | Checks local, deterministic FAIR-facing metadata (`CITATION.cff`/`codemeta.json`/`.zenodo.json`) and the published JSON Schema contract for internal … |
| `check_distribution_metadata.py` | Validates a built sdist/wheel in `dist/` against `pyproject.toml` (name/version/summary/requires-python/license/Project-URLs), confirms the sdist carries … |
| `build_and_check_distribution.py` | Builds the sdist/wheel (isolated `build` module invocation via `run_isolated_module.py`), `twine check`s the resolved artifact paths (no shell glob), then … |
| `publish_schemas.py` | Synchronizes the package's JSON Schema copies (source of truth) into the MkDocs-published `docs/reference/schemas/v1` tree at their stable, versioned `$id` … |
| `check_bugfix_test_contract.py` | Bug-fix test contract gate. |
| `check_mutation_score.py` | Mutation-score gate, three lanes: |
| `mutation_results.py` | Leaf parsing/attribution layer under `check_mutation_score.py`: |
| `gen_mutation_test_selection.py` | Generates (or `--check`s) `tests/mutation_test_selection.txt`, the pytest `@argfile` `[tool.mutmut].pytest_add_cli_args_test_selection` reads: |
| `mutation_reach_trace.py` | pytest plugin (`MUTATION_REACH_OUT=<dir>`) recording which tests execute `only_mutate` code: |
| `mutmut_stable_param_ids.py` | pytest plugin loaded only into the mutation lane's sessions (`[tool.mutmut].pytest_add_cli_args`: |
| `mutation_scope.py` | Run-cost primitives for the mutation lane, sibling leaf of `check_mutation_score.py` (which owns gating and sits near the 2000-line cap). |
| `check_changelog_fragment.py` | Fails a PR that touches `abicheck//*.py` (including deletions and moves out of `abicheck/`, via `--no-renames`) without an added/modified `.md` fragment in … |
| `gen_examples_docs.py` | Regenerates `docs/reference/examples/caseNN_*.md` from `examples/case*/README.md`, and the generated regions (headline, verdict distribution, case index) of … |
| `gen_detector_spec.py` | Regenerates the formal detector specification matrix (`docs/reference/detector-spec.{md,json}`) by fusing per-`ChangeKind` category (`checker_policy`), … |
| `gen_action_reference.py` | Regenerates the exhaustive GitHub Action inputs/outputs reference (`docs/reference/github-action-inputs.md`) from `action.yml`'s … |
| `gen_cli_reference.py` | Regenerates the exhaustive CLI command/option reference (`docs/reference/cli-reference.md`) from the live Click command tree (`abicheck/cli.py` + registered … |
| `gen_python_api_reference.py` | Regenerates the exhaustive Python API reference (`docs/reference/python-api-reference.md`) from every name in `abicheck.service.__all__` — full function … |
| `gen_config_reference.py` | Regenerates the exhaustive `.abicheck.yml` key/type reference (`docs/reference/config-keys-reference.md`) from … |
| `gen_agent_skills.py` | Publishes `skills-src/` as self-contained Agent Skills (ADR-058 / G36 P0.3) into the three generated trees `.agents/skills/`, `.claude/skills/`, and … |
| `install_dev_skill.py` | Thin CLI wrapper around `gen_agent_skills.py`'s `render_all`/`write_trees` for local dev use: |
| `action_cli_mirror.py` | The `action-cli-mirror` AI-readiness check (ADR-070 D4): |
| `gen_action_cli_surface.py` | Generates `action/cli-surface.txt` — the CLI `click.Choice` sets `action/validate-inputs.sh` needs before abicheck is installed. |
| `gen_learning_ladder.py` | Renders the learning-series hub's numbered step list and role-path table — `docs/learn/abi-api-handling.md`'s `<!-- BEGIN/END GENERATED: |
| `gen_platform_matrix.py` | Regenerates the host-OS × binary-format capability matrix — `docs/reference/platforms.md`'s "Quick Reference: |
| `gen_backend_capability_matrix.py` | Regenerates the L2 header-backend × ABI-fact capability matrix — `docs/reference/header-backend-capabilities.md`'s fact tables, spliced between `<!-- … |
| `gen_fact_capability_matrix.py` | Regenerates the fact/capability registry doc (`docs/reference/fact-registry.md`, ADR-063 D7/Phase 5) — a fully-generated page (mirroring … |
| `gen_stable_abi_data.py` | Regenerates the vendored CPython Stable-ABI membership set (`abicheck/stable_abi_data.py`) from a `Misc/stable_abi.toml` (local path or `--url`). |
| `gen_changekind_stub.py` | Regenerates `abicheck/model/change_catalog/kinds.pyi`, the mypy-only PEP 484 stub for `ChangeKind` (ADR-061 D9 model-vs-policy split). |
| `example_catalog.py` | Phase 3 of the examples/catalog split (retired plan record, see `docs/contribute/plans/index.md`; |
| `gen_catalog_taxonomy.py` | Phase 1 + Phase 2 of the examples/catalog split (retired plan record, see `docs/contribute/plans/index.md`): |
| `catalog_classification.py` | The declarative per-case `entity`/`scenario_kind`/`ecosystem` manifest loader/validator for `gen_catalog_taxonomy.py` — … |
| `catalog_subjects.py` | The declarative, hand-authored `subjects` manifest loader/validator for `gen_catalog_taxonomy.py` (examples/catalog split's "What is left" item 2 — retired … |
| `workflow_examples.py` | Phase 5 of the examples/catalog split: |
| `catalog_rule_registry.py` | The canonical compatibility-rule registry's loader and its join against the catalog taxonomy (`catalog/catalog_rules.yaml` -> slug/title/definition; |
| `gen_catalog_coverage_report.py` | Phase 6 of the examples/catalog split (retired plan record, see `docs/contribute/plans/index.md`): |
| `abi_taxonomy_coverage.py` | Phases 2/3 of `docs/contribute/plans/abi-api-knowledge-and-corpus.md`: |
| `gen_abi_taxonomy_coverage.py` | Generates `docs/contribute/abi-taxonomy-coverage.md`, the Phase 2/3 coverage matrix: |
| `gen_g20_fixtures.py` | Single source of truth for the committed G20 snapshot fixtures (`catalog/cases/case143–151`, ADR-035 / plan `g20-source-scan-example-catalog`). |
| `fixture_sync.py` | The write-or-`--check` driver the committed-snapshot generators share: |
| `gen_l3l4l5_examples.py` | Single source of truth for the L3/L4/L5 build/source-only example fixtures — ABI/API failures only build context, source-replay, or the derived source graph … |
| `gen_reachability_examples.py` | Generates the committed snapshot fixtures for the reachability-aware-suppression example cases (192–193): |
| `benchmark_comparison.py` | Benchmarks abicheck vs ABICC / libabigail across the `examples/` catalog. |
| `generate_benchmark_report.py` | Wraps `benchmark_comparison.py` into one reproducible report: |
| `evidence_tiers.py` | Single source of truth for the five-source / L0–L4 evidence model: |
| `learning_ladder.py` | The learning series' reading order as data (`docs/_meta/learning-ladder.yaml`: |
| `learning_nav_order.py` | The `learning-nav-order` AI-readiness gate, split out of `check_ai_readiness.py` (past the 2000-line cap): |
| `platform_capabilities.py` | Single source of truth for the host-OS × binary-format capability matrix (ADR-051 Stage 2): |
| `backend_capabilities.py` | Single source of truth for the L2 header-backend capability matrix (G31 Phase D): |
| `evidence_benchmark.py` | ADR-033 Phase 7 performance & false-positive report: |
| `dump_cli_surface.py` | Dumps the full `abicheck` Click command tree (commands/subcommands, options, arguments — names, flags, defaults, types, choices) to JSON. |
| `diff_cli_surface.py` | Diffs two `dump_cli_surface.py` JSON outputs and reports added/removed commands and added/removed/changed options. |
| `benchmark_scaling.py` | Synthetic scaling benchmark for the pipeline — sweeps sizes and times `compare()`, suppression audit, severity, serialization, and the HTML/SARIF/JUnit … |
| `check_header_graph_perf.py` | G31 Phase D header-graph attach-cost perf gate: |
| `bench_release_memory.py` | Reproducible memory/time benchmark for a header-depth multi-library `compare`. |
| `bench_extraction_scope.py` | Phase 0 harness of `docs/contribute/plans/target-ownership-and-extraction-scope.md`. |
| `bench_graph_materialization.py` | Evidence-entity-model Phase 5 (invariant I6) measurement: |
| `export_orphan_breakdown.py` | Evidence-entity-model Phase 4 (invariant I4) measurement: |
| `perf_cache_reset.py` | Cold-cache reset for `benchmark_scaling.py`'s memory passes (`clear_process_caches()`: |
| `perf_baseline.py` | Baseline-regression comparison for `benchmark_scaling.py`, split out purely to keep that file under the file-size gate's line cap: |
| `check_l2_cli_perf.py` | Full-CLI L2 perf harness -- the third and outermost of this repo's three perf measurement levels (`benchmark_scaling.py` = synthetic/in-process/compiler-free; |
| `check_l2_scaling_perf.py` | Full-CLI L2 scaling gate -- the growth-rate sibling of `check_l2_cli_perf.py`. |
| `l2_cli_argv.py` | The argv the full-CLI L2 harness measures -- the third 2000-line-cap split out of `check_l2_cli_perf.py`, and the narrowest seam left (argv construction … |
| `l2_cli_validation.py` | What the full-CLI L2 harness *asserts* about a run's output, split out of `check_l2_cli_perf.py` once that file crossed the `file-size` gate's 2000-line … |
| `l2_cli_gating.py` | How the same harness turns receipts into a pass or a failure -- the third piece of that split (what it asserts / what it measures / how it gates). |
| `l2_cli_model.py` | The innermost types the full-CLI L2 harness is built from -- `Step`, `Scenario`, and `EXTRACTION_EXPECTATIONS` -- extracted on the fourth crossing of the … |
| `l2_cli_observation.py` | What the full-CLI L2 harness *concludes from an observed native-invocation count* -- the fifth 2000-line-cap split, and an inward seam like `l2_cli_model.py`: |
| `l2_cli_fixture.py` | The generated real C++ fixture `check_l2_cli_perf.py` measures against -- a real `.so` with real public headers built by a real compiler, not a hand-built … |
| `l2_real_profiles.py` | The pinned real-integration L2 profiles (oneDAL PR #3693, SVS released-v0.4.0-vs-PR-#387 and its separate PR-base smoke test, PVXS PR #216) as declarative data: |
| `perf_receipt.py` | The shared, versioned performance *receipt* layer the perf harnesses emit, so the three measurement levels above stop reinventing the apparatus around them … |
| `perf_measurement.py` | Shared repeated-measurement statistics for the two perf-gate scripts above: |
| `classify_perf_paths.py` | The classifier-job pattern for `performance.yml`'s PR-triggered jobs (`scaling`/`regression`/`l2-cli-perf` (which also runs the header-graph PR gate), plus … |
| `demo_libz.py` | End-to-end demo on libz. |
| `summarize_test_durations.py` | Renders the per-test durations captured by the `tests/conftest.py` `ABICHECK_DURATIONS_JSON` hook into a Markdown table (GitHub run summary in CI, stdout … |
| `extract_bundle_manifest.py` | Extracts a manifest from multi-library bundles (cases 90–93). |
| `restore_git_mtimes.py` | Restores each tracked file's mtime to its last-commit timestamp (one `git log` walk, not one subprocess per file). |
| `setup_dev_env.sh` | Vendor-neutral dev-environment bootstrap: |
| `dev_venv_pin.env` | The single source of truth for where `setup_dev_env.sh` creates its isolated dev venv (`DEV_VENV_DIR`) and which interpreter it uses (`dev_venv_python()` — … |
| `castxml_pin.env` | The single source of truth for `setup_dev_env.sh`'s conda-forge `castxml` pin — `CASTXML_BUILD` (the direct matchspec) *and* `CASTXML_LOCKED_SPECS` (its … |

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
2. Add a row to the inventory table above and the full row to
   `docs/contribute/agent-guide/scripts-inventory.md`.
3. If it is a pass/fail gate, add a `Step` to `verify.py`'s `STEPS` catalog
   with the right `profiles` membership instead of wiring the raw command
   into CI/pre-commit directly — `verify.py` is what CI, pre-commit, and
   `pixi run check` all call through (CLAUDE.md "M0-3"). Reporting-only
   scripts (benchmarks, summaries) still wire directly into
   `.github/workflows/ci.yml`.
4. Document its arguments via `argparse` so `--help` is enough for an
   agent to use it.
