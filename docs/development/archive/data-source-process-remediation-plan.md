# Data Source Process Remediation Plan

**Date:** 2026-06-11
**Status:** Draft plan
**Scope:** Update abicheck L0-L5 data-source model, documentation, runtime diagnostics, and real-world validation process.

## Verification Summary

The current documentation and process are obsolete.

Verified drift:

- `docs/development/adr/003-data-source-architecture.md` still describes a three-layer L0/L1/L2 architecture and a fixed "30 detectors" matrix.
- Runtime diagnostics previously exposed the old L0/L1/L2 language:
  - `abicheck/dwarf_snapshot.py::show_data_sources`
  - `abicheck/cli.py::_print_data_sources`
  - PR #351 now has the first runtime-diagnostic implementation slices:
    `dump --show-data-sources` reports L0-L5 and can load
    `--build-info <pack>` or `--sources <pack>` for L3/L4/L5 status, and
    compare now emits old/new L0-L5 evidence coverage side by side.
- The current codebase already has evidence and platform support beyond ADR-003:
  - canonical ordered `evidence_tier` plus raw `evidence_tiers`
  - ELF, PE, Mach-O metadata
  - DWARF, advanced DWARF, BTF, CTF, PDB
  - debug artifact resolution
  - BuildSourcePack L3/L4/L5 coverage rows, inline snapshot embedding, and content-addressed pack references
  - build-context capture from compile DB, CMake File API, Ninja, Bazel, Make,
    compiler records, and external extractor manifests
  - source ABI replay through clang, CastXML, or Android header-ABI dumps
  - source graph summaries with optional include/call/Kythe/CodeQL augmentations
  - public surface scoping
  - package, bundle, stack, and appcompat analysis paths
  - SYCL and heterogeneous stack support
- Existing ADRs partially cover the later work (`007`, `018`, `020`, `021`,
  `023`, `024`, `026`, `027`, `028`, `029`, `030`, `031`, `032`), but there is
  no current canonical process that ties the sources, gates, and validation
  matrix together.

## Target L0-L5 Model

Replace the old L0/L1/L2-only mental model with a canonical L0-L5 taxonomy:

- **L0: Binary identity and loader metadata**
  - ELF, PE, Mach-O, SONAME/install-name, exported symbols, imports/dependencies, version nodes, binding, visibility, relocation/loader facts.
- **L1: Debug and binary type metadata**
  - DWARF, advanced DWARF, BTF, CTF, PDB, dSYM/debuglink/build-id resolved artifacts.
- **L2: Declared public API surface**
  - headers, CastXML AST, public include roots, source-location filtering, constants, default parameters, templates, typedef spelling.
- **L3: Build, toolchain, packaging, and distribution context**
  - compile commands, CMake/Ninja/Bazel/Make facts, compiler flags, target triples,
    language standards, ABI macros, generated files, package metadata,
    debug/devel package pairing, bundle membership, and trusted external
    extractor outputs.
- **L4: Source ABI replay**
  - per-TU source/header replay under real build context; public declarations,
    types, macros, default arguments, inline/template/constexpr bodies, Android
    header-ABI dumps, and source-only API findings.
- **L5: Source/implementation graph**
  - compact target/source/header/compile-unit/build-option graph, optional
    include/call/Kythe/CodeQL augmentation, graph-to-graph diff, finding
    localization, and impact/reachability explanation.

This taxonomy should become the single vocabulary used by ADRs, CLI diagnostics, JSON/SARIF/HTML reports, validation reports, and release-process gates.
Consumer/appcompat, bundle, stack, baseline, suppression, and policy inputs are
impact/report contexts that can consume L0-L5 findings; they are not a separate
canonical evidence layer.

## Gaps To Address

1. **Documentation drift**
   - ADR-003 is no longer the source of truth for available sources or detector coverage.
   - Later ADRs describe pieces of the current architecture but do not define one canonical end-to-end process.
   - User-facing limitations and verdict docs do not clearly explain when evidence is incomplete, asymmetric, or degraded.

2. **Runtime diagnostics drift**
   - `--show-data-sources` now has the first L0-L5 implementation in PR #351.
   - Compare diagnostics now print old/new L0-L5 evidence coverage side by side
     and mark asymmetric rows.
   - Remaining diagnostics still need richer debug artifact provenance, package
     pairing, appcompat context, and source/package-specific detail.
   - Detector-count language is brittle because detector coverage now depends on platform, evidence, surface, policy, and mode.

3. **Report/schema semantics**
   - JSON already exposes `evidence_tier` and `evidence_tiers`, and compare
     reports can carry L0-L5 coverage rows, but the process does not fully
     define how those map to user-visible confidence and warnings.
   - Reports need explicit coverage warnings for missing headers, missing debug artifacts, stripped binaries, mismatched package/debug/devel inputs, and asymmetric evidence.
   - Mixed rich-to-poor comparisons must be marked as lower-confidence/manual-review where appropriate instead of producing hard false positives.

4. **Validation gaps**
   - Existing tests cover many unit paths, but the remeasurement process is not organized by evidence mode.
   - Real-world validation should measure accuracy by L0-only, L1-only, L0+L1,
     L0+L2, L0+L1+L2, L3 build/package context, L4 source ABI replay, L5 source
     graph, and consumer/appcompat impact context.
   - Known false-positive families need regression gates, especially rich-to-poor evidence transitions.

## Work Plan

### 1. Create the canonical source/process spec

Deliverables:

- New or replacement architecture document defining L0-L5.
- Cross-reference from ADR-003 to the new canonical document, or supersede ADR-003 with a newer ADR.
- Update ADR index and relevant ADR backreferences.

Required content:

- Source taxonomy and responsibilities.
- Source precedence and merge rules.
- Provenance fields and confidence semantics.
- Platform matrix for ELF, PE/PDB, Mach-O/dSYM, BTF, CTF.
- User-facing explanations for incomplete evidence and manual-review results.

Gate:

- `rg "L0/L1/L2|30 detectors|24/30|6/30" docs validation README.md mkdocs.yml abicheck tests` shows no stale canonical claims except historical notes explicitly marked as historical.

### 2. Update runtime data-source diagnostics

Deliverables:

- Replace old `--show-data-sources` output with L0-L5 output.
- Include old/new comparison evidence summary where a compare command has both sides.
- Replace fixed detector counts with capability/coverage categories.

Suggested output sections:

- L0 binary metadata: present/missing, format, exported-symbol count, version info.
- L1 debug/type metadata: present/missing, resolved artifact path, debug format, counts.
- L2 declared API surface: headers/include roots, source filtering status, CastXML status.
- L3 build/package context: compile DB, build-system adapters, compiler/build
  facts, package identity, and devel/debug package match.
- L4 source ABI replay: extractor backend, replay scope, declarations/types/
  macros/default arguments/inline bodies collected, and partial replay failures.
- L5 source graph: graph summary, include/call/Kythe/CodeQL augmentation status,
  graph-diff availability, and localization support.
- Impact context: appcompat/bundle/stack/baseline/policy inputs that can consume
  or scope L0-L5 findings.
- Coverage warnings: missing/asymmetric/degraded evidence.

Gate:

- CLI tests assert L0-L5 output for no-debug, DWARF, headers, evidence-pack
  present/absent, package/debug, and appcompat-impact cases.

### 3. Normalize report evidence semantics

Deliverables:

- Define exact mapping between raw `evidence_tiers`, canonical `evidence_tier`,
  and L0-L5 coverage.
- Add or update report fields only if current fields cannot express the model safely.
- Ensure Markdown, JSON, SARIF, HTML, stack, bundle, and appcompat reports use consistent terminology.

Rules to encode:

- `evidence_tier` remains the ordered confidence scalar for analysis depth.
- L0-L5 coverage is a capability/provenance vector, not a single ordered scalar.
- Missing source layers must become explicit `coverage_warnings`.
- Asymmetric evidence must lower confidence or require manual review for source-dependent findings.

Gate:

- Schema tests and reporter tests cover full evidence, symbols-only, debug-only, header-only, and asymmetric comparisons.

### 4. Fix asymmetric rich-to-poor behavior

Deliverables:

- Audit detectors that can misclassify missing evidence as removed API/type data.
- Add degradation logic for old-rich/new-poor, old-poor/new-rich, and source-family mismatch cases.
- Suppress or downgrade source-dependent removals when the new side simply lacks the evidence needed to observe them.

Initial high-risk areas:

- type removals from missing DWARF/header data
- function signature changes when one side is symbol-only
- enum/typedef/constant changes when L2 is missing
- source-only API changes when L4 is missing
- graph-derived localization/impact changes when L5 is missing

Gate:

- Known false-positive cases stop producing phantom removal avalanches.
- Regression corpus includes TBB 2021.9 -> 2022.0 and libxml2 2.9.7 -> 2.9.9, plus oneDAL/oneTBB source-built runs with matched public headers and toolchain.

### 5. Expand validation and remeasurement matrix

Deliverables:

- Validation runner that records mode, platform, source layers, evidence asymmetry, component, case id, verdict, runtime, and known expected outcome.
- Published remeasurement report with per-mode false positives, false negatives, verdict accuracy, runtime, and residual risks.
- Full example-suite remeasurement across every case in `examples/ground_truth.json` before and after source/process changes.
- Full component-suite remeasurement for source-specific implementation areas:
  - ELF/symbol/versioning/surface
  - DWARF/advanced DWARF/debug resolver
  - BTF/CTF/PDB/PE/Mach-O
  - headers/CastXML/public-surface scoping
  - build context and package extraction
  - bundle, stack, and appcompat
  - report/schema/SARIF/HTML/JUnit output
  - policy, baseline, suppression, and confidence/evidence logic

Required mode matrix:

- L0-only
- L1-only
- L0+L1
- L0+L2
- L0+L1+L2
- L3 package/build-context enriched
- L4 source-ABI replay enriched
- L5 source-graph enriched
- consumer/appcompat/bundle/stack impact-context enriched
- asymmetric rich-to-poor and poor-to-rich evidence
- cross-mode snapshot comparisons:
  - L0 snapshot vs L0+L1 snapshot
  - L0 snapshot vs L0+L2 snapshot
  - L0+L1 snapshot vs L0+L1+L2 snapshot
  - L0+L1+L2 snapshot vs L0+L1+L2+L3+L4+L5 snapshot
  - old-rich/new-poor and old-poor/new-rich for every source-dependent detector family

Required platform matrix:

- ELF/Linux
- PE/PDB/Windows
- Mach-O/dSYM/macOS
- kernel/BTF
- CTF sample
- stripped and split-debug packages

Required example artifact variants:

- **Stripped binary with headers**: build the same example library, strip DWARF/type
  debug sections, still pass the public headers, and assert that L0+L2 findings
  remain active while L1-dependent findings degrade with explicit warnings.
- **Regular release binary with headers**: build without debug info but without
  post-build stripping, pass public headers, and assert behavior matches the
  intended L0+L2 profile rather than accidentally relying on compiler-emitted
  debug metadata.
- **Debug binary with headers**: current `tests/validate_examples.py` mostly
  exercises this profile because it builds examples with `-g`/`Debug` and passes
  discovered `v1`/`v2` headers to `abicheck dump`.
- **Build information and source access**: collect L3 build context, L4 source
  ABI replay, and L5 source graph evidence for selected examples. These must be
  measured as separate variants because they exercise `collect`,
  build-system adapters, source ABI replay, and source graph paths rather than
  the current synthetic example dump/compare harness.

Runner support:

- `tests/validate_examples.py --artifact-variant debug-headers` keeps the
  previous default behavior.
- `--artifact-variant release-headers` builds without debug flags and still
  passes headers.
- `--artifact-variant stripped-headers` builds debug artifacts, strips debug
  info, and still passes headers.
- `--artifact-variant build-source` collects L3/L4/L5 evidence with
  `collect --compile-db --source-abi --source-abi-extractor castxml
  --source-graph summary`, then compares with old/new build-source packs.
- `--artifact-variant all` runs all four variants for every selected example.
- JSON output uses schema `validate_examples.v2` and records platform, command,
  selected variants, ground-truth corpus size, per-case runtime, component,
  mode, source layers, evidence asymmetry, expected verdict, actual verdict,
  and manual-review allowance. This makes full synthetic remeasurement artifacts
  comparable across runs.

Build/source capability coverage to add after the current `build-source` smoke:

- L3: `--compile-db`, `--build-dir --cmake`, `--build-dir --ninja`,
  `--ninja-compdb`, `--bazel-cquery`, `--bazel-aquery`, `--make-dry-run`,
  `--read-compiler-record`, and `--extractor-manifest` with
  `--collection-mode permissive|strict|audit`.
- L4: `--source-abi-extractor clang|castxml|android`,
  `--source-abi-scope headers-only|changed|target|full`,
  `--source-abi-cache`, `--changed-path`, and partial-coverage degradation when
  a replay backend is unavailable or some translation units fail.
- L5: `--source-graph summary`, `--include-graph`, `--call-graph`,
  `--kythe-entries`, and `--codeql-results`, with graph-diff and localization
  evidence kept separate from artifact-proven ABI breaks.

Gate:

- Remeasurement report is generated from checked-in validation metadata.
  - `validation/scripts/summarize_remeasurement.py` consumes
    `validate_examples.v2`, `component_suites.v1`, and `run_matrix.v2`
    artifacts and writes schema `remeasurement_summary.v1`.
  - The summary records section counts, total records, blocking failures,
    status/verdict/mode/source-layer counts, and component-suite blockers.
- Each measured case records expected verdict, actual verdict, evidence coverage, and whether manual review is acceptable.
- `python tests/validate_examples.py --json` runs all non-skipped example cases from `examples/ground_truth.json`.
- `pytest` component suites for every source family above are run or explicitly marked blocked with missing platform/tooling reason.
  - `validation/scripts/run_component_suites.py` writes schema
    `component_suites.v1` records for each source-family suite.
  - Each component-suite record includes component, suite/case id, platform,
    supported platforms, source layers, pytest command, runtime, status,
    pass/fail/error/skip/warning counts, and blocked reasons.
- `validation/scripts/run_matrix.py` remeasures all package pairs in `validation/data/manifest.json` once binaries are available through `ABICHECK_VALIDATION_LIBS`.
  - `validation/data/results.json` keeps the per-comparison records and now uses
    schema `run_matrix.v2`.
  - `validation/data/results.meta.json` records run-level metadata: runner,
    command, platform, manifest pair count, comparison count, and observed
    evidence modes and comparison-status counts.
  - Each real-world record includes component, case id, platform, mode,
    L0-L5/source-layer coverage, old/new source layers, evidence asymmetry,
    runtime, expected verdict, actual verdict, normalized compatibility-axis
    verdicts, comparison status, exit code, and stderr.
  - Real-world release gating is based on expectation mismatch or missing
    verdict, not raw exit code, because expected `BREAKING` / `API_BREAK`
    verdicts legitimately return non-zero from `abicheck compare`.
- Current validation inventory is treated as the starting corpus, not the final corpus:
  - 129 synthetic cases in `examples/ground_truth.json`
  - 11 curated real-world package pairs in `validation/data/manifest.json`
  - 33 current real-world shared-library comparisons in `validation/data/results.json`
  - current observed real-world evidence modes: `sym->sym`, `dwarf->sym`, `dwarf->dwarf`
- Remeasurement must fail the release gate if any previously passing example, component suite, or real-world known-FP regression changes verdict without an accepted explanation.

### 6. Update user docs and process docs

Deliverables:

- Update limitations, verdicts, architecture, and relevant user-guide pages.
- Add "how to get better evidence" guidance:
  - install debug symbols
  - provide public headers
  - provide `compile_commands.json` or build-system metadata
  - collect source ABI replay with clang, CastXML, or Android header-ABI dumps
  - collect source graph summaries for impact/localization
  - provide package/bundle/appcompat context for consumer-impact triage
  - avoid comparing rich evidence to stripped artifacts without warnings
- Add troubleshooting for `--show-data-sources`.

Gate:

- User-facing docs explain what abicheck can and cannot prove in each evidence mode.

### 7. Add CI and release gates

Deliverables:

- CI check for stale source taxonomy phrases.
- Unit tests for L0-L5 diagnostics and report coverage fields.
- Real-world validation job or manual release checklist entry.

Release gate:

- No release with changed detector/source behavior unless:
  - docs are updated,
  - `--show-data-sources` remains accurate,
  - validation matrix is remeasured or explicitly waived with reason,
  - known FP/FN corpus does not regress.

## Suggested Implementation Order

1. Land canonical L0-L5 spec and mark ADR-003 historical/superseded.
2. Update `--show-data-sources` and CLI tests. First slices done in PR #351:
   L0-L5 dump diagnostics plus compare-side old/new evidence summaries.
   Remaining work is broader debug/source/package/appcompat context output.
3. Normalize report wording and schema semantics.
4. Add asymmetric evidence degradation tests and fixes.
5. Build validation matrix metadata and runner.
6. Remeasure real-world corpus and publish results.
7. Update user docs and CI/release gates.

## Required Remeasurement Commands

Run these as the minimum proof set for this workstream. A blocked command must be recorded with the missing tool, platform, binary corpus, or fixture reason.

Synthetic examples:

```bash
python tests/validate_examples.py --json
python tests/validate_examples.py --artifact-variant all --json
pytest -q tests/test_example_autodiscovery.py tests/test_validate_examples_unit.py
```

Source-family component suites:

```bash
python validation/scripts/run_component_suites.py --all --output validation/data/component_suites.json
pytest -q \
  tests/test_elf_metadata_unit.py \
  tests/test_elf_parse_integration.py \
  tests/test_elf_symbol_filters.py \
  tests/test_elf_version_policy.py \
  tests/test_surface.py \
  tests/test_surface_scope_parity.py \
  tests/test_confidence_evidence.py \
  tests/test_stripped_degradation.py \
  tests/test_dwarf_snapshot.py \
  tests/test_dwarf_metadata_coverage.py \
  tests/test_dwarf_unified.py \
  tests/test_debug_resolver.py \
  tests/test_btf_metadata.py \
  tests/test_btf_integration.py \
  tests/test_ctf_metadata.py \
  tests/test_pdb_metadata.py \
  tests/test_pdb_parser.py \
  tests/test_pe_metadata_unit.py \
  tests/test_macho_metadata_unit.py \
  tests/test_build_context.py \
  tests/test_package.py \
  tests/test_package_extractor_matrix.py \
  tests/test_bundle.py \
  tests/test_stack_checker.py \
  tests/test_appcompat.py \
  tests/test_appcompat_examples.py \
  tests/test_report_schema.py \
  tests/test_reporter.py \
  tests/test_sarif.py \
  tests/test_junit_report.py \
  tests/test_policy_changekind_matrix.py \
  tests/test_policy_file.py \
  tests/test_baseline.py \
  tests/test_suppression_matrix.py
```

Known false-positive and real-world gates:

```bash
pytest -q tests/test_real_world_false_positives.py tests/test_realworld_scan.py tests/test_fp_rate_gate.py
ABICHECK_VALIDATION_LIBS=validation/libs/ex python validation/scripts/run_matrix.py
python validation/scripts/summarize_remeasurement.py \
  --examples results/validate_examples.json \
  --components validation/data/component_suites.json \
  --real-world validation/data/results.json \
  --real-world-meta validation/data/results.meta.json \
  --output validation/data/remeasurement_summary.json \
  --fail-on-blocking
python tests/check_validate_results.py
python tests/summarize_validate_results.py
```

Cross-platform/platform-specific gates:

```bash
pytest -q tests/test_cross_platform_fixtures.py tests/test_cross_platform_integration.py
pytest -q tests/test_windows_toolchain_smoke.py tests/test_msvc_pdb_e2e.py
pytest -q tests/test_macos_arm64_abi.py
pytest -q tests/test_workflow_kernel_accel.py
```

Expected artifacts:

- `validation/data/results.json` regenerated from the current manifest.
- `validation/data/runs/*.json` regenerated for each measured shared-library comparison.
- `validation/data/component_suites.json` generated from component-suite runs.
- `validation/data/remeasurement_summary.json` generated from examples,
  component suites, and real-world matrix artifacts.

## Definition Of Done

- One canonical L0-L5 source model is linked from architecture docs, ADR index, user docs, and diagnostics.
- CLI diagnostics and reports no longer imply that L0/L1/L2 are the whole process.
- Mixed evidence comparisons are clearly warned, downgraded, or routed to manual review when confidence is insufficient.
- Remeasurement covers evidence modes, platforms, package/debug/devel flows,
  source replay, source graph, and appcompat/bundle impact context.
- Known false positives are tracked as regression gates.
- Release process requires data-source/process docs and validation evidence to stay in sync with implementation.
