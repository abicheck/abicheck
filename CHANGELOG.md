# Changelog

High-level product history for abicheck. This file explains the product-level
changes that matter when choosing or upgrading a release; it is intentionally
not a complete implementation ledger. Detailed history lives in Git commits,
pull requests, architecture records, and release-specific documentation.

> **Pre-1.0 stability notice:** abicheck's scanner CLI is under active
> development. Commands, options, output fields, and automation contracts may
> change between minor releases; pin the version and review the upgrade guide
> before updating CI or other production automation.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

Pending changes accumulate as fragments in [`changelog.d/`](changelog.d/).
They are consolidated into a concise release summary when a version ships.

<!-- scriv-insert-here -->

## [0.8.0] — 2026-11-07

### One option vocabulary (breaking)

- Every concept now has one spelling across the CLI, `.abicheck.yml`, the
  Action, the reusable workflows and the typed Python API (ADR-091), and the
  old spellings are removed with no transition window — a retired flag fails
  with exit `64`, a retired Action input fails before any analysis and names
  its replacement. Highlights: `--version` → `--label`, `--abi3` →
  `--abi3-floor`, `deps --old-root/--new-root/--sysroot` → `--root`,
  `acknowledgment:` → `acknowledgments:`, `source.method` → `sources.method`,
  and many Action inputs (`policy-file` → `policy`, `old-version` →
  `old-label`, `debug-info1/2` → `old-/new-debug-info`, …).
- New `--set PATH=VALUE` on `compare`, `dump` and `project capture-variants`
  (and `config_overrides` on the typed requests, `set:` on the Action) sets
  one `.abicheck.yml` key for one run. Options that now live only in the
  config (`--used-by-manifest`, `--required-symbol`, `--view`, and Action
  inputs such as `dso-only`, `frontend`, `compile-options`) moved behind it.
- `--crosscheck` is gone; cross-source checks are ordinary findings
  controlled with `policy.overrides`.

### Acknowledgments, audit and gating

- `compare --acknowledgments FILE` and the Action's `acknowledgments` input
  apply acknowledgment records for one run; an acknowledged break can stop
  gating, and accepted breaks stay visible on a passing run.
- The disposition audit lists every configured suppression, `reclassify:`
  and acknowledgment rule, including ones that matched nothing, as `used`,
  `unused` or `not_evaluated` (with the missing evidence), and
  `unused_rules: warn|block` can fail a run. Rules take an optional `id:`;
  an id-less rule gets a portable `rule-<12 hex>` id.
- A `compare` run is never unbounded: without `--budget` it now has a 2-hour
  deadline (exit `5`), and directory/package `compare` accepts `--budget`.
- `compare --require-same-generation` refuses a pair captured by different
  extractor generations.

### New analysis

- New findings: `PUBLIC_VIRTUAL_DISPATCH_SET_CHANGED`,
  `PUBLIC_CONSUMER_COMPILED_DEPENDENCY_CHANGED`, `TARGET_ISA_CHANGED`, and
  `public_api_internal_type_renamed`.
- Class- and function-template parameter defaults are recorded, and a
  header-level default change is an `API_BREAK` (ADR-085); the clang header
  backend models C++20 concepts and constraints.
- `compare --no-baseline libfoo.a -H include/` audits a static archive
  (ADR-086).
- `dump` captures a whole release as a `BundleFacts` baseline, with
  published JSON Schemas; stored `BundleFacts` comparisons render the full
  release document, JUnit and `oneline`.

### Reports and CI integration

- Directory/package `compare` reports show what each library removed and
  changed, list added/removed public API, count findings by entity and
  operation, render `sarif` and `review`, and cover PE/COFF and Mach-O in the
  dependency graph. Large scans cap the Coverage Warnings section.
- `--use-cases` attribution appears per finding in HTML and SARIF.
- The JSON report records which baseline the Action compared against;
  baseline-set archives can be pinned with `expected-archive-digest` and
  attested (`attest: true` on `actions/stage-baseline` and
  `publish-baseline.yml`). New `pr-comment-key` input for parallel jobs.
- Built-in `append_only_dispatch_tables` policy.

### Correctness and performance

- About 180 fixes, mostly false positives and hidden breaks: templates and
  specializations, vtables/typeinfo, ELF binding, header-only dependencies,
  binary-only runs no longer claiming source-level or reachability evidence,
  and failed or malformed extractor output (clang AST, DWARF) reported as
  `FAILED` instead of an empty surface.
- Faster snapshot encode/decode with lazy loading of stored declarations, a
  byte-bounded snapshot cache, less memory per release member, and fewer
  repeated header/`clang -M` passes.

### Upgrade notes

- Pin `abicheck/abicheck@v0.8.0` and run your existing command lines,
  `.abicheck.yml` and workflow inputs once: every retired spelling fails
  loudly. The full rename table is
  [Upgrading to 0.8](docs/start/upgrading-to-0.8.md).
- Report schema is **7.0** (the `exit` block drops the crosscheck keys), and
  `rule_id` for id-less rules changed format; update report consumers.
- Python API (pre-1.0): typed names follow ADR-091 (`old_label`/`new_label`,
  `policy` takes a name or path, `contract` is one field;
  `contract_evaluation=False` is refused). More root modules moved to their
  owning packages (`abicheck.suppression` → `abicheck.policy.suppression.rules`,
  `abicheck.package` → `abicheck.extract.packages`, `abicheck.reporter*` →
  `abicheck.report`).
- abicheck now requires Click 8.2 or newer.

## [0.7.0] — 2026-10-13

### Compatibility contract and gating

- Contract evaluation is now on by default for `compare` (CLI, typed API and
  the Action). With no `--contract`, the domain follows the evidence:
  `public` when header evidence covers every side, otherwise `exports`, then
  `all`. Findings outside the selected contract no longer gate, and the
  contract-coverage exit (`1`) can apply without `--contract`.
  `--contract all` restores the 0.6 behaviour exactly.
- `--contract` is now `compare`'s one contract mechanism:
  `--scope-public-headers`/`--no-scope-public-headers` and `--post-manifest`
  are removed (the POST manifest moves to `contract.overlays.post_manifest`
  in `.abicheck.yml`).
- Every compare report states the versioning-policy verdict when the policy
  declares a `versioning:` block, without changing the verdict or exit code.
- Many false positives and hidden breaks fixed across contract placement,
  symbol-versioned aliases, macro-spelled calling conventions, unresolved
  pointees, same-name records in different namespaces, enum sentinels,
  `public_not_exported`, linker-reserved symbols and AArch64 targets.

### Releases, reports, and CI integration

- Directory/package `compare` gained a release HTML report with a per-member
  table, comparison scope and a dependency graph (release JSON schema 1.9),
  and runs each member through the same compare primitive, configuration and
  exit fold as a single-library comparison.
- HTML reports gained consumer-impact (`--use-cases`) sections, and
  `project history -o html` renders a release timeline. HTML, Markdown and
  PR comments now agree with JSON on added/removed/changed counts.
- New report-only Actions for fork pull requests (`actions/report`,
  `actions/verify-source-run`) publish an already-produced report without
  running contributor code in a privileged job.
- `publish-baseline.yml` can publish a pre-captured baseline-set, including
  one captured by a different, verified producer run; per-profile compile
  context is honoured when capturing baselines.
- `actions/baseline` supports declarative component selection, and
  `.abicheck.yml` can capture multiple variants.

### Performance and execution

- `ABICHECK_MAX_THREADS` caps abicheck's total thread count across all
  nested pools; `ABICHECK_REFERENCE_MODE=1` disables every cache and
  parallel path for differential testing and debugging.
- Faster header and source analysis: the header graph parses alongside the
  primary dump, `--depth source` dumps each translation unit once for all
  graph families, and ELF, DWARF, clang-AST and type-spelling hot paths were
  reworked. Warm `compare` runs hit the whole-snapshot cache again, and disk
  caches no longer serve output from a different abicheck build.
- Timed-out external tools get a graceful SIGTERM and all extraction child
  processes run under one supervisor.

### Internal architecture

- Continued the ADR-061/063 migration: finding disposition, identity, report
  views, rendering, the native dump, header backends and release workflows
  moved to their responsibility owners, and the declaration fields of
  `AbiSnapshot` now live in its semantic IR.

### Upgrade notes

- Pin `abicheck/abicheck@v0.7.0` and re-check gates: the evidence-adaptive
  contract default can change which findings gate and may surface exit `1`
  for incomplete contract evidence. Use `--contract all` to keep the 0.6
  reading while migrating.
- `abicheck compat` (the ABICC drop-in, including ABICC XML descriptors and
  Perl `ABI.dump` inputs) is removed; use `compare`. The CLI now has five root
  commands.
- Python API (pre-1.0): read snapshot declarations as
  `snapshot.declarations.<kind>`; the flat re-export modules
  (`abicheck.severity`, `abicheck.exit_decision`, `abicheck.reclassify`,
  `abicheck.appcompat`, `abicheck.finding_identity`, `abicheck.post_processing`,
  and others) and undocumented API with no caller are gone. Import from the
  owning packages (see `docs/use/python-api.md`).
- `ChangeKind`s `frame_register_changed` and
  `bundle_variant_coverage_regressed` are removed; a policy file naming them
  no longer loads.

## [0.6.0] — 2026-09-13

See [Upgrading from 0.5 to 0.6](docs/start/upgrading-to-0.6.md) for the
migration.

### Comparison, release, and assurance

- Reworked the product around one evidence-driven comparison flow: each result
  can carry its comparison context, provenance, compatibility decision, and
  assurance state instead of relying on a best-effort verdict alone.
- Made no-baseline, not-comparable, incomplete-analysis, and policy-blocked
  outcomes first-class. CI and release tooling can distinguish “no regression
  found” from “a comparison could not establish a trustworthy result.”
- Added release-oriented views that join findings, impact, disposition,
  coverage, and remediation evidence. This makes a release decision reviewable
  rather than a raw list of ABI deltas.
- Strengthened reconciliation between human-readable reports and machine
  outputs, including Markdown, HTML, JSON, SARIF, JUnit, and GitHub Action
  summaries.
- Added explicit gates for completeness, evidence quality, policy selection,
  and performance budgets so automation can fail safely when its input does not
  meet the requested assurance level.

### Snapshots, bundles, and reproducibility

- Introduced durable project snapshots and a versioned bundle/storage model for
  transporting a comparison subject, its selected libraries, metadata, and
  analysis evidence as a coherent artifact.
- Expanded bundle and package workflows: stored members, archive contents,
  library inventories, compression, extraction, and provenance are validated
  before downstream analysis consumes them.
- Improved baseline and release selection so directory, package, archive,
  binary, and previously stored snapshot inputs follow a predictable path to a
  comparable analysis request.
- Made cached and persisted results safer to reuse by tightening identity,
  digest, configuration, and ownership checks around stored artifacts.

### Build and source understanding

- Expanded compile-context resolution across project configuration, compile
  databases, manifests, command-line overrides, include roots, language mode,
  compiler identity, and response files.
- Added richer source/build/header graph modelling. Public-header scope,
  transitive includes, selected translation units, and source-only analysis are
  now represented as evidence rather than inferred loosely from a binary.
- Improved source replay and artifact-set workflows so the scanner can explain
  what it discovered, what it analysed, and why a requested source/build input
  was accepted, filtered, or rejected.
- Improved toolchain and platform handling across ELF, Mach-O, PE, DWARF, PDB,
  Clang, CastXML, GCC-family compilers, conda-forge environments, and
  package-derived inputs.

### ABI model and usability

- Continued the semantic surface migration: entities, types, functions,
  variables, constants, fields, and relationships are carried through a more
  explicit, typed fact model with clearer ownership and confidence boundaries.
- Broadened ABI coverage for templates, typedefs, opaque types, layout facts,
  vtables, virtual methods, qualifiers, references, pointers, and source-level
  identities while preserving evidence for uncertain or unavailable facts.
- Consolidated scanner and comparison CLI surfaces, tightened option
  validation, and improved dry-run output so users can inspect resolved work
  before launching an expensive analysis.
- Hardened diagnostics, documentation, test coverage, and cross-platform edge
  cases found through real project runs, CI, migration work, and review.

### Upgrade notes

- Treat 0.6.0 as a deliberate pre-1.0 CLI upgrade: pin `abicheck/abicheck@v0.6.0`
  in GitHub Actions and review command options, exit handling, JSON consumers,
  and policy files before moving production automation.
- Prefer the new evidence and assurance fields when they are available; do not
  collapse non-comparable or incomplete outcomes into compatibility success.

## [0.5.0] — 2026-07-16

### CLI and workflow consolidation

- Simplified the pre-1.0 public CLI into a smaller command surface centred on
  `dump`, `compare`, `scan`, `deps`, and `compat`, reducing overlap between
  older entry points and bringing common configuration under shared rules.
- Unified comparison options, analysis depth, dry runs, profile selection, and
  configuration validation across the main workflows. Equivalent requests now
  resolve through the same core options rather than command-specific defaults.
- Clarified the separation between collecting an ABI snapshot, comparing two
  subjects, scanning a project, and checking dependencies. This gives CI
  integrations more predictable inputs, output shapes, and failure modes.

### Reports and compatibility decisions

- Made application- and symbol-scoped compatibility results more consistent
  across text, JSON, HTML, SARIF, JUnit, and GitHub Action reporting.
- Added clearer status and filtering behaviour for findings, severity, scope,
  and changed-path views, helping consumers focus on actionable API/ABI risk.
- Improved semantic-version and SONAME-oriented release guidance so policy can
  turn compatibility evidence into a visible recommendation rather than an
  undocumented convention.

### Inputs, packages, and robustness

- Improved package, directory, snapshot, and release comparison flows,
  including build-information collection, input validation, and reusable
  baseline selection.
- Extended source and header handling for compile databases, include scope,
  compiler options, and project configuration while continuing to support
  binary-only workflows.
- Fixed correctness and usability gaps found through post-merge reviews and
  real CI use, particularly around option routing, diagnostics, output parity,
  and platform/toolchain edge cases.

### Upgrade notes

- Existing automation should migrate to the consolidated commands and verify
  any legacy aliases, option spellings, and output parsers before updating.

## [0.4.0] — 2026-07-01

### Analysis architecture

- Consolidated the CLI and moved key workflows behind typed service boundaries,
  separating user-facing command parsing from the model, extraction, and
  comparison work that supports it.
- Added structural source-graph comparison and explanation tooling, allowing
  users to inspect how headers, sources, and discovered components contribute
  to an analysis rather than treating discovery as opaque.
- Introduced a clearer analysis-depth model for binary, header, build, and
  source evidence. Requests can express the amount and kind of evidence they
  need instead of silently receiving a partial approximation.

### Scale and configuration

- Improved scalable source analysis through better work planning, cache-aware
  reuse, bounded discovery, and more explicit resource handling for larger
  projects.
- Tightened compile-context controls around include paths, compiler options,
  selected inputs, and public-header roots so results better reflect the build
  that a consumer actually ships.
- Expanded diagnostics for incomplete or conflicting source/build information,
  making configuration problems easier to separate from ABI changes.

### Upgrade notes

- Projects that use source-aware scanning should review depth and compile-
  context settings after upgrading; these controls became more explicit.

## [0.3.0] — 2026-06-03

### Policy and release guidance

- Added policy-aware release recommendations, including semantic-version and
  SONAME guidance, so compatibility results can be interpreted in the context
  of a release contract.
- Expanded suppression and severity controls, enabling teams to distinguish
  accepted compatibility debt from findings that should block a release.
- Improved finding deduplication, filtering, and impact-oriented views to make
  large comparisons easier to triage.

### CI and ecosystem reporting

- Added JUnit output, richer SARIF and HTML reports, and end-to-end scenario
  coverage for CI systems that need both a readable summary and structured
  annotations.
- Improved GitHub Action integration and report publication patterns for
  automated compatibility checks in pull requests and release pipelines.
- Added package extraction and dependency-stack analysis for release artifacts,
  broadening the inputs that can participate in a compatibility decision.

### ABI coverage and portability

- Broadened ABI detection, symbol-version policy support, and cross-platform
  foundations beyond the original Linux-first workflows.
- Strengthened handling of headers, symbols, debug information, and suppressions
  so the checker can present a more complete explanation of a reported change.

## [0.2.0] — 2026-03-21

### Broader compatibility analysis

- Added application compatibility analysis and dependency-aware checking,
  allowing a project to reason about the libraries it consumes as well as a
  single producer library in isolation.
- Introduced configurable severity policies, report filtering, deduplication,
  and impact-focused views for teams that need to tune ABI enforcement to their
  release process.
- Added stronger confidence and evidence reporting, making it clearer when a
  finding is based on symbols, DWARF, headers, or a combination of sources.

### More platforms and inputs

- Added Windows PE/COFF and macOS Mach-O metadata support alongside Linux ELF,
  establishing the cross-platform direction of the project.
- Added package-input support, DWARF-only snapshots, and richer baseline
  storage so analysis can begin from more than one kind of build artifact.
- Improved suppression and presentation controls for noisy or intentionally
  incompatible changes in real integration environments.

### Upgrade notes

- Teams adopting multi-platform or package workflows should validate that the
  selected binary, debug, and header artifacts describe the same build.

## [0.1.0] — 2026-03-13

### Initial public release

- First public release of the Python-native ABI compatibility checker for C/C++
  shared libraries, built to make compatibility evidence accessible without
  requiring a single external comparison engine.
- Delivered multi-source analysis using public headers, ELF symbols, and DWARF
  debug information, with policy-aware compatibility verdicts for API, ABI,
  layout, symbol, and versioning changes.
- Provided snapshot creation and comparison, ABICC-compatible operation, and
  Markdown, JSON, SARIF, and HTML reports for local investigation and CI.
- Established a Linux-first foundation with a focus on reproducible inputs,
  explainable findings, and a path toward broader platform and package support.

### Early adoption notes

- Treat the initial command surface and report schemas as pre-1.0 interfaces;
  pin versions in automation and keep source/debug artifacts alongside the
  binaries being checked.

[Unreleased]: https://github.com/abicheck/abicheck/compare/v0.7.0...HEAD
[0.7.0]: https://github.com/abicheck/abicheck/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/abicheck/abicheck/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/abicheck/abicheck/releases/tag/v0.5.0
[0.4.0]: https://github.com/abicheck/abicheck/releases/tag/v0.4.0
[0.3.0]: https://github.com/abicheck/abicheck/releases/tag/v0.3.0
[0.2.0]: https://github.com/abicheck/abicheck/releases/tag/v0.2.0
[0.1.0]: https://github.com/abicheck/abicheck/releases/tag/v0.1.0
