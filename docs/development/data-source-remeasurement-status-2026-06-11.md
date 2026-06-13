# Data Source Remeasurement Status - 2026-06-11

**Task:** `remeasure-all`
**Scope:** First local proof pass for the L0-L5 data-source/process remediation work.

## Environment

- Repo: `/home/openclaw/.openclaw/workspace-abicheck/repo`
- Command prefix needed locally: `PYTHONPATH=.`
- Available tools:
  - `castxml`: `/home/openclaw/.local/bin/castxml`
  - `gcc`: `/usr/bin/gcc`
  - `g++`: `/usr/bin/g++`
  - `cmake`: `/usr/bin/cmake`
  - `pytest`: `9.0.2`

## Full Synthetic Example Remeasurement

Command:

```bash
PYTHONPATH=. python tests/validate_examples.py --json
```

Result:

- Exit code: `1`
- Total cases: `129`
- PASS: `104`
- XFAIL: `5`
- SKIP: `7`
- FAIL: `10`
- ERROR: `3`

Unexpected FAIL cases:

- `case01_symbol_removal`: expected `BREAKING`, got `NO_CHANGE`
- `case02_param_type_change`: expected `BREAKING`, got `NO_CHANGE`
- `case03_compat_addition`: expected `COMPATIBLE`, got `NO_CHANGE`
- `case102_frozen_runtime_signature_changed`: expected `BREAKING`, got `NO_CHANGE`
- `case10_return_type`: expected `BREAKING`, got `NO_CHANGE`
- `case12_function_removed`: expected `BREAKING`, got `NO_CHANGE`
- `case33_pointer_level`: expected `BREAKING`, got `NO_CHANGE`
- `case46_pointer_chain_type_change`: expected `BREAKING`, got `NO_CHANGE`
- `case59_func_became_inline`: expected `BREAKING`, got `NO_CHANGE`
- `case66_language_linkage_changed`: expected `BREAKING`, got `NO_CHANGE`

Unexpected ERROR cases:

- `case126_sycl_device_impl_ptr`: `dump v1 failed`; CastXML failed while processing `v1.h`
- `case80_pimpl_shared_to_unique`: `dump v1 failed`; CastXML failed while processing `v1.h`
- `case89_inline_accessor_renamed_pimpl_member`: `dump v1 failed`; CastXML failed while processing `v1.h`

Known expected non-pass buckets:

- XFAIL: 5 known gaps from `examples/ground_truth.json`
- SKIP: 7 platform/tooling/layout skips, including bundle cases delegated to bundle tests and BTF fixture delegated to kernel workflow tests

## Real-World Matrix Status

Command planned:

```bash
ABICHECK_VALIDATION_LIBS=validation/libs/ex python validation/scripts/run_matrix.py
```

Status:

- Blocked locally: `validation/libs/ex` is not present.
- This matches `validation/README.md`: real binaries are intentionally not committed and must be fetched/extracted from `validation/data/manifest.json`.

Current checked-in real-world inventory:

- Manifest pairs: 11
- Current result records: 33 shared-library comparisons
- Current evidence modes in `validation/data/results.json`:
  - `sym->sym`: 25
  - `dwarf->sym`: 5
  - `dwarf->dwarf`: 3

## Dirty Worktree Context

Pre-existing modified files not touched by this task:

- `abicheck/elf_symbol_filter.py`
- `tests/test_elf_symbol_filters.py`
- `validation/data/results.json`

`validation/data/results.json` was intentionally restored to the `main` version
in PR #351 after review. The refreshed local real-world output had not been
paired with a regenerated `validation/REPORT.md`, so keeping it in the PR would
make the checked-in report and result artifact contradict each other.

Files added/updated by this task:

- `docs/development/data-source-process-remediation-plan.md`
- `docs/development/data-source-remeasurement-status-2026-06-11.md`

## Next Required Steps

1. Investigate the 10 `NO_CHANGE` false negatives from full example validation.
2. Investigate the 2 CastXML errors in pimpl-related examples.
3. Run component suites listed in `data-source-process-remediation-plan.md`.
4. Fetch/extract the real-world conda packages from `validation/data/manifest.json` and rerun `validation/scripts/run_matrix.py`.
5. Extend report/validation outputs beyond current `sym/dwarf` labels to explicit L0-L5 coverage.
   First runtime diagnostic slices are now implemented for `--show-data-sources`
   with L3/L4/L5 build-source pack reporting, plus compare-side old/new L0-L5
   coverage summaries.
6. Run the newly enabled artifact-variant matrix across all examples:
   `python tests/validate_examples.py --artifact-variant all --json`.
   The runner now supports debug+headers, release+headers, stripped+headers,
   and build/source-evidence variants; the full all-example matrix still needs
   to be measured and triaged.

## Example Artifact Variant Enablement

Implemented in PR #351:

- `debug-headers`: existing default; builds Debug/`-g` where applicable and
  passes discovered public headers.
- `release-headers`: builds without debug flags / with Release CMake profile
  and still passes headers.
- `stripped-headers`: builds with debug info, strips debug sections, and still
  passes headers.
- `build-source`: builds with headers, collects L3/L4/L5 evidence through
  `collect --compile-db --source-abi --source-abi-extractor castxml
  --source-graph summary`, and compares with old/new build-source packs.

Smoke proof:

```bash
PYTHONPATH=. python tests/validate_examples.py case04 --artifact-variant all --json
```

Result: `PASS=4`, one pass for each variant.

## Runtime Data-Source Diagnostic Implementation

Implemented in PR #351:

- `abicheck dump --show-data-sources` now reports L0-L5 availability instead
  of only L0/L1/L2.
- `abicheck dump --show-data-sources --build-info <pack>` loads the build-source pack
  and reports L3 build context, L4 source ABI, and L5 source graph status.
- Historical fixed detector-count claims were removed from live diagnostic
  output; the CLI now describes the active evidence mode and missing evidence
  boundaries.
- `abicheck compare` now prints old/new L0-L5 evidence coverage side by side
  when build/source coverage is in play. Asymmetric rows are visibly marked in
  stderr while the existing JSON `layer_coverage` field remains target/new-side
  focused for backward compatibility.

Targeted proof:

```bash
PYTHONPATH=. pytest -q tests/test_dwarf_snapshot.py -k show_data_sources
PYTHONPATH=. pytest -q tests/test_dwarf_coverage_gaps.py -k show_data_sources
ruff check abicheck/dwarf_snapshot.py abicheck/cli.py tests/test_dwarf_snapshot.py
```

Result: all passed.

Additional implementation proof after compare-side summary:

```bash
PYTHONPATH=. pytest -q tests/test_build_source_cli.py::test_compare_with_evidence_emits_coverage_and_findings tests/test_build_source_cli.py::test_compare_asymmetric_old_only_reports_target_not_collected tests/test_layer_coverage.py::test_compare_cli_reports_coverage_asymmetry tests/test_dwarf_snapshot.py::TestShowDataSources tests/test_dwarf_snapshot.py::TestPrintDataSourcesDirect tests/test_validate_examples_unit.py
PYTHONPATH=. python tests/validate_examples.py case04 --artifact-variant all --json
ruff check --no-cache abicheck tests
mypy abicheck
python scripts/check_ai_readiness.py
```

Result: targeted pytest `29 passed`; case04 artifact variants `PASS=4`; ruff
clean; mypy clean; AI-readiness `0` errors and `13` warnings.

Additional PR-review fix proof:

```bash
PYTHONPATH=. python tests/validate_examples.py case04 --artifact-variant all --json
PYTHONPATH=. python tests/validate_examples.py case103 --artifact-variant build-source --json
PYTHONPATH=. python tests/validate_examples.py case104 --artifact-variant build-source --json
PYTHONPATH=. python tests/validate_examples.py case104 --artifact-variant release-headers --json
```

Result: case04 `PASS=4`; case103 build-source `PASS=1` with expected
`COMPATIBLE_WITH_RISK` from L3 toolchain-flag evidence; case104 build-source
`PASS=1`; case104 release-headers `PASS=1`.

## Remeasurement Artifact Readiness

Implemented in PR #351:

- `tests/validate_examples.py --json` now emits schema `validate_examples.v2`.
- Top-level metadata records runner, command, platform, selected cases,
  `examples/ground_truth.json` corpus size, and selected artifact variants.
- Each result records component, case id, platform, mode, source layers,
  evidence asymmetry, runtime seconds, expected verdict, actual verdict, status,
  and whether manual review is acceptable.
- `validation/scripts/run_matrix.py` now emits real-world matrix records with
  schema `run_matrix.v2`.
- Real-world records include component, case id, platform, logical library,
  mode, old/new source layers, evidence asymmetry, runtime, expected verdict,
  actual verdict, normalized compatibility-axis verdicts, comparison status,
  exit code, stderr, summary counts, release recommendation, and optional layer
  coverage.
- Real-world run metadata is written to `validation/data/results.meta.json`
  with runner, command, platform, manifest pair count, comparison count, and
  observed evidence modes and comparison-status counts.
- `validation/scripts/run_component_suites.py` now emits component-suite
  records with schema `component_suites.v1`.
- Component-suite records include suite/case id, platform, supported platforms,
  source layers, pytest command, runtime, status, pytest summary counts, and
  explicit blocked reasons for missing files or optional dependencies.
- `validation/scripts/summarize_remeasurement.py` now consumes
  `validate_examples.v2`, `component_suites.v1`, and `run_matrix.v2` artifacts
  and emits `remeasurement_summary.v1`.
- The combined summary records total records, blocking failures,
  status/verdict/mode/source-layer counts, real-world expectation mismatches,
  real-world run errors, and component-suite blocked reasons.
- Real-world summaries no longer count expected non-zero compare exit codes as
  blocking failures; expected `BREAKING` / `API_BREAK` outcomes are scored by
  expected-vs-actual verdict status instead.

Smoke proof:

```bash
PYTHONPATH=. python tests/validate_examples.py case04 --artifact-variant all --json
PYTHONPATH=. pytest -q tests/test_validate_examples_unit.py
PYTHONPATH=. pytest -q tests/test_validation_run_matrix.py
PYTHONPATH=. pytest -q tests/test_validation_component_suites.py
PYTHONPATH=. pytest -q tests/test_validation_remeasurement_summary.py
PYTHONPATH=. python validation/scripts/run_component_suites.py --suite report-policy --dry-run --output /tmp/component_suites.json
```

Result: case04 `PASS=4`; validate-example unit tests `20 passed`;
run-matrix unit tests `5 passed`; component-suite unit tests `4 passed`;
remeasurement-summary unit tests `4 passed`; component-suite dry-run wrote one
planned suite record.

## Plan Refresh For Current Build/Source Capabilities

Updated in PR #351 after the first implementation slice:

- The remediation plan now uses the current L0-L5 BuildSourcePack model instead
  of the older L0-L4 draft.
- L3 is explicitly build/toolchain/package context: compile DB, CMake, Ninja,
  Bazel, Make, compiler-record recovery, and external extractor manifests.
- L4 is explicitly source ABI replay: clang, CastXML, or Android header-ABI
  dumps, replay scopes, cache, changed-path filtering, and partial-coverage
  degradation.
- L5 is explicitly source graph: summary graph plus optional include/call,
  Kythe, and CodeQL augmentation for graph diff and localization.
- Consumer/appcompat/bundle/stack/policy inputs are recorded as impact/report
  context that consumes L0-L5 findings, not as a separate canonical evidence
  layer.

## Component Suite Status

Initial component-suite command:

```bash
PYTHONPATH=. pytest -q tests/test_elf_metadata_unit.py tests/test_elf_parse_integration.py tests/test_elf_symbol_filters.py tests/test_elf_version_policy.py tests/test_surface.py tests/test_surface_scope_parity.py tests/test_confidence_evidence.py tests/test_stripped_degradation.py tests/test_dwarf_snapshot.py tests/test_dwarf_metadata_coverage.py tests/test_dwarf_unified.py tests/test_debug_resolver.py tests/test_btf_metadata.py tests/test_btf_integration.py tests/test_ctf_metadata.py tests/test_pdb_metadata.py tests/test_pdb_parser.py tests/test_pe_metadata_unit.py tests/test_macho_metadata_unit.py tests/test_build_context.py tests/test_package.py tests/test_package_extractor_matrix.py tests/test_bundle.py tests/test_stack_checker.py tests/test_appcompat.py tests/test_appcompat_examples.py tests/test_report_schema.py tests/test_reporter.py tests/test_sarif.py tests/test_junit_report.py tests/test_policy_changekind_matrix.py tests/test_policy_file.py tests/test_baseline.py tests/test_suppression_matrix.py
```

Result:

- Exit code: `2`
- Blocked during test collection: `tests/test_pe_metadata_unit.py`
- Blocker: missing Python dependency `pefile`

Second component-suite command excluded `tests/test_pe_metadata_unit.py` but kept the rest of the local/Linux component list.

Result:

- Exit code: `1`
- Passed: `1641`
- Skipped: `5`
- Failed: `10`
- Warnings: `12`

Likely real failures:

- `tests/test_surface_scope_parity.py::test_internal_type_change_scoped_out_by_both`
  - expected non-breaking scoped result, got `BREAKING`
- `tests/test_surface_scope_parity.py::test_public_type_change_breaking_for_both`
  - expected breaking public type change, got `NO_CHANGE`

Tooling/dependency failures from missing `pefile`:

- `tests/test_macho_metadata_unit.py::TestCliIntegration::test_dump_native_binary_pe`
- `tests/test_macho_metadata_unit.py::TestCliIntegration::test_dump_native_binary_pe_empty_exports_raises`
- `tests/test_appcompat.py::TestParsePeAppRequirements::test_named_imports`
- `tests/test_appcompat.py::TestParsePeAppRequirements::test_ordinal_only_imports`
- `tests/test_appcompat.py::TestParsePeAppRequirements::test_filter_by_dll_name`
- `tests/test_appcompat.py::TestParsePeAppRequirements::test_pe_parse_error`
- `tests/test_appcompat.py::TestParsePeAppRequirements::test_no_import_directory`
- `tests/test_appcompat.py::TestGetNewLibExports::test_pe_exports`

Known false-positive / real-world scan gate:

```bash
PYTHONPATH=. pytest -q tests/test_real_world_false_positives.py tests/test_realworld_scan.py tests/test_fp_rate_gate.py
```

Result:

- Exit code: `1`
- Passed: `67`
- Failed: `2`

Failures:

- `tests/test_realworld_scan.py::TestRealWorldCompatibleRelease::test_compatible_release`
  - expected `FUNC_ADDED` for `compress_reset`; observed only `ENUM_MEMBER_ADDED`
- `tests/test_realworld_scan.py::TestRealWorldBreakingRelease::test_breaking_release`
  - expected `FUNC_REMOVED` for `compress_bound`; observed type layout changes but no function removal
