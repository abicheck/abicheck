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

## [0.6.0] — 2026-09-13

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

[Unreleased]: https://github.com/abicheck/abicheck/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/abicheck/abicheck/releases/tag/v0.6.0
[0.5.0]: https://github.com/abicheck/abicheck/releases/tag/v0.5.0
[0.4.0]: https://github.com/abicheck/abicheck/releases/tag/v0.4.0
[0.3.0]: https://github.com/abicheck/abicheck/releases/tag/v0.3.0
[0.2.0]: https://github.com/abicheck/abicheck/releases/tag/v0.2.0
[0.1.0]: https://github.com/abicheck/abicheck/releases/tag/v0.1.0
