# Changelog

All notable changes to abicheck are documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html)

---

## [Unreleased]

### Added

#### Release Recommendation (semver + SONAME)
- New `abicheck/semver.py` derives a **release recommendation** from the
  policy-aware verdict + change set: a semantic-version bump
  (`major`/`minor`/`patch`/`none`) and a SONAME action
  (`bump_required`/`bump_performed`/`bump_missing`/`no_bump_needed`).
- Always emitted in `abicheck compare --format json` under the additive
  `release_recommendation` key (also in `--stat --format json` and leaf mode);
  opt-in for Markdown via the new **`--recommend`** flag (works in leaf mode
  too). Policy-aware (honours `--policy sdk_vendor`/`plugin_abi` and custom
  policy files).
- JSON schema bumped to **1.1** (additive): `release_recommendation` documented
  as an optional object in `abicheck/schemas/compare_report.schema.json`.
- New tests: `tests/test_semver_recommendation.py`,
  `tests/test_workflow_scenarios.py` (drop-in upgrade, additive minor,
  host↔plugin load contract, policy-scoped decision).

#### User-Scenario / Flow Catalog (end-to-end scanner validation)
- New internal **user-scenario catalog** under `tests/scenarios/*.yaml` (grouped
  by theme, merged by globbing so it scales past one file): defines real-world
  *user flows* (CI gate, public-surface compliance scan, SARIF for code
  scanning, release recommendation, suppression, offline snapshots, …) —
  distinct from `examples/` (change-type fixtures) and `plans/` (backlog).
- `tests/test_scenarios.py` drives each automated scenario through the abicheck
  **CLI end-to-end** (CliRunner on JSON snapshots) and asserts the documented
  outcome, validating abicheck as a *scanner tool*, not only a change detector.
  Every scenario's `validates:` is checked against the use-case registry.
- Captures the missed usage scenario from **issue #235** (public-header scoping
  must suppress private ABI breaks) as `SC-PUBLIC-SURFACE-SCOPE`, now an
  end-to-end regression guard.

#### Use-Case Coverage Evaluation + machine-checked registry
- New `docs/development/usecase-coverage-evaluation.md` maps abicheck against
  the full application/library ABI-API change use-case space and records the
  code/test/example follow-ups (gaps G1–G8).
- New `docs/development/usecase-registry.yaml` — the machine-checkable source of
  truth for every use case (`status`, `axis`, `evidence`, `gap`, `next_steps`),
  validated by `tests/test_usecase_registry.py`: coverage claims must cite
  evidence paths that exist, and unfinished items must carry a gap + plan. This
  makes the use cases first-class, extensible, and testable.
- Cross-platform honesty: `docs/reference/platforms.md` now states the
  validation reality (Linux = CI-validated baseline; macOS/Windows =
  parser-level/partial), guarded by `tests/test_platform_coverage_honesty.py`.

#### JUnit XML Output
- **`--format junit`** for `compare` and `compare-release` commands — produces
  JUnit XML reports for CI systems (GitLab CI, Jenkins, Azure DevOps) that
  display ABI check results as standard test results in their dashboards.
- Each exported symbol/type maps to a `<testcase>`; breaking changes become
  `<failure>` elements with severity type and source location.
- Supports `--show-only` filtering, suppression files, and policy overrides.
- New module: `abicheck/junit_report.py` (stdlib only, no external dependencies).

#### Binary Fingerprint Rename Detection (Exploratory)
- **Binary fingerprint rename detection** (exploratory, ADR-003 extension):
  new `binary_fingerprint.py` module with `compute_function_fingerprints()`,
  `match_renamed_functions()`, and `compute_section_summary()`.  Uses function
  code size and SHA-256 hash from ELF `.dynsym` + `.text` to detect likely
  renames when symbol names change but the underlying code is identical.
  New `FUNC_LIKELY_RENAMED` change kind (verdict: `COMPATIBLE_WITH_RISK`).
  Integrated as the `fingerprint_renames` detector — fires only in
  `elf_only_mode` (stripped binaries without debug info or headers).

#### Debian Symbols File Adapter
- **`abicheck debian-symbols generate`** — generate Debian symbols files (`dpkg-gensymbols`
  format) from shared library binaries. Supports C++ demangled `(c++)` form, ELF symbol
  versioning (`@Base` / `@VERSION_NODE`), and automatic SONAME-to-package-name derivation.
  Options: `--package`, `--version`, `--no-cpp`, `-o`.
- **`abicheck debian-symbols validate`** — validate a Debian symbols file against a binary.
  Reports missing and new symbols. Respects `(optional)` tag semantics. Exit code `0` = match,
  `2` = mismatch.
- **`abicheck debian-symbols diff`** — diff two Debian symbols files showing added, removed,
  and version-changed symbols.
- Full Debian tag syntax support: `(c++)`, `(optional)`, `(arch=...)`, pipe-separated groups
  (`(c++|optional)`), and round-trip formatting preservation.
- New module: `abicheck.debian_symbols` with Python API for programmatic use
  (`generate_symbols_file`, `validate_symbols`, `diff_symbols_files`, `parse_symbols_file`).

#### ELF Symbol-Version Policy Checks
- **`symbol_version_node_removed`** (BREAKING) — detects when an entire version node
  (e.g., `LIBFOO_1.0`) is removed from the version script, listing affected symbols.
  Deduplicated with `symbol_version_defined_removed` (the more specific node-level
  change wins).
- **`symbol_moved_version_node`** (COMPATIBLE_WITH_RISK) — detects when a symbol
  migrates between version nodes (e.g., `LIBFOO_1.0` → `LIBFOO_2.0`).
- **`soname_bump_recommended`** (COMPATIBLE) — post-detector advisory emitted when
  binary-incompatible changes are detected but the SONAME is not bumped. This
  advisory can be escalated to BREAKING via `--policy-file` with
  `soname_bump_recommended: break`.
- **`soname_bump_unnecessary`** (COMPATIBLE) — advisory emitted when the SONAME is
  bumped but no binary-incompatible changes are detected.
- **`version_script_missing`** (COMPATIBLE) — advisory emitted when the new library
  exports symbols without a version script (`--version-script`).
- New `diff_versioning.py` module with version-node graph diffing, SONAME bump
  policy check (post-detector), and version-script presence detection.
- Cross-detector deduplication for `SYMBOL_VERSION_NODE_REMOVED` vs
  `SYMBOL_VERSION_DEFINED_REMOVED`.
- 35 new tests covering all version-policy scenarios and checker integration.

### Planned
- `--policy-file` schema validation improvements
- Version-stamped typedef suppression (libpng `png_libpng_version_X_Y_Z` pattern)

---

## [0.2.0] — 2026-03-21

### Added

#### Application Compatibility Checking ([ADR-005](docs/development/adr/005-application-compatibility.md)) (#157)
- **`abicheck appcompat`** — answer "Will my application break with the new library?" by
  intersecting the app's required symbols with the library diff. Only changes affecting symbols
  your binary actually uses are reported.
- Full mode (old lib + new lib + headers) and weak mode (`--check-against` a single library).
- `--list-required-symbols` to inspect which symbols your binary imports.
- `--show-irrelevant` to see filtered-out changes that do not affect your application.
- Works with ELF, PE, and Mach-O binaries.

#### Cross-Platform Support
- **Windows (PE/COFF)** and **macOS (Mach-O)** binary metadata analysis (exports, imports,
  dependencies) alongside existing Linux (ELF) support.
- **PDB parser**: Windows PE debug info extraction for type-level analysis.
- Windows MSVC/MinGW toolchain support matrix and smoke tests.
- macOS ARM64 regression tests and `install_name` coverage.

#### Configurable Severity Levels (#180)
- Four issue categories: `abi_breaking`, `potential_breaking`, `quality_issues`, `additions`,
  each assignable to `error`, `warning`, or `info`.
- **`--severity-preset`**: Built-in presets (`default`, `strict`, `info-only`) for quick
  configuration.
- Per-category overrides: `--severity-abi-breaking`, `--severity-potential-breaking`,
  `--severity-quality-issues`, `--severity-additions`.
- Severity controls report visualization (badges, section grouping) and exit codes.
- PolicyFile overrides supported. JSON output includes top-level `"severity"` object
  and per-change `"severity"` field.
- Replaces the removed `--fail-on-additions` flag.

#### Report Filtering & Deduplication ([ADR-004](docs/development/adr/004-report-filtering-and-deduplication.md))
- **Redundancy filtering**: Automatically collapses derived changes caused by root type changes
  (e.g. a struct size change that propagates to 30 `FUNC_PARAMS_CHANGED` entries). Root type
  changes are annotated with `caused_count` and `affected_symbols`. Use `--show-redundant` to
  disable filtering.
- **`--show-only`**: Comma-separated filter tokens to limit displayed changes by severity
  (`breaking`, `api-break`, `risk`, `compatible`), element (`functions`, `variables`, `types`,
  `enums`, `elf`), or action (`added`, `removed`, `changed`). AND across dimensions, OR within.
  Does not affect verdict or exit codes. Invalid tokens produce a clean CLI error.
- **`--stat`**: One-line summary mode for CI gates. With `--format json`, emits only the summary
  object (no changes array).
- **`--report-mode leaf`**: Root-type-grouped output that lists affected interfaces under each
  root type change, instead of listing every change individually.
- **`--show-impact`**: Appends an impact summary table showing root changes and how many
  interfaces each affects, with separate columns for direct and derived counts.
- All filtering features work across Markdown, JSON, SARIF, and HTML output formats.
  ABICC-compatible XML includes redundancy annotations but does not support `--show-only`.
- Redundancy annotations in SARIF (`caused_by_type`/`caused_count` in result properties,
  `redundant_count` in run properties) and XML (`<redundant_changes>`, `<caused_by>`,
  `<caused_count>` elements in both binary and source sections).

#### Package Extraction Layer (#161)
- Extract and compare shared libraries directly from **RPM, Deb, tar, conda, and wheel**
  packages without manual unpacking.
- Full-stack ABI checking with dependency resolution across package contents.

#### DWARF-only Snapshot Builder ([ADR-003](docs/development/adr/003-data-source-architecture.md))
- Headerless ELF analysis: build ABI snapshots from DWARF debug info alone, without
  requiring public headers or castxml.

#### Full-Stack Dependency Validation (#153)
- `abicheck deps` — show dependency tree and symbol binding status for a binary.
- `abicheck stack-check` — compare a binary's full dependency stack across two sysroots.
- `--follow-deps` flag for `compare` to include dependency info.
- Symbol origin tracking (`SYMBOL_LEAKED_FROM_DEPENDENCY_CHANGED`).

#### Suppression Enhancements (#146)
- `label` field for human-readable suppression rule names.
- `source_location` field for file-scoped suppression rules.
- `expires` field for time-limited suppression rules with automatic expiry.
- **Suppression audit mode**: detect stale rules matching nothing, high-risk suppressions
  masking BREAKING changes, and near-expiry rules.

#### Detection Improvements
- **ELF visibility tracking**: New `SYMBOL_ELF_VISIBILITY_CHANGED` ChangeKind for
  DEFAULT/PROTECTED/HIDDEN/INTERNAL transitions.
- **`FUNC_REMOVED_FROM_BINARY`**: New BREAKING-severity ChangeKind for mixed-mode (headers +
  ELF) when a function is removed from the binary but header still declares it.
- **Global variable ELF-only tracking**: Collect STT_OBJECT and STT_TLS symbols as Variable
  entries in ELF-only fallback, enabling VAR_REMOVED/VAR_ADDED detection without headers.
- **DWARF reserved field detection**: DWARF struct layout diff detects reserved field
  activations (matching AST-level detection). Patterns include `reserved`, `mbz`, `fill`,
  `filler`, requiring matching offset AND type.
- **DWARF opaque struct handling**: Opaque types (forward-declared, accessed only via pointer)
  no longer trigger BREAKING for internal field changes. Correctly deserialized from JSON
  snapshots.
- **Type name canonicalization**: `canonicalize_type_name()` normalizes "struct Foo" vs "Foo",
  "const int" vs "int const", and whitespace differences, reducing false positives in
  field/return/variable/parameter type comparisons.
- **Cross-detector deduplication**: Centralized dedup collapses overlapping FUNC_REMOVED/
  FUNC_ADDED/VAR_REMOVED/VAR_ADDED reports from function, PE, and Mach-O detectors.
- **Confidence/evidence tracking**: `Confidence` enum (high/medium/low) on `DiffResult` based
  on available data sources (ELF, DWARF, header, PE, Mach-O). Human-readable
  `coverage_warnings` for disabled detectors.
- **Import-time ChangeKind completeness assertion**: Every `ChangeKind` member must appear in
  exactly one of BREAKING/COMPATIBLE/API_BREAK/RISK sets, enforced at import time.

#### Additional Improvements
- **GitHub Action** (`napetrov/abicheck@v1`) for CI integration with per-mode verdict mapping,
  format validation, and severity-preset support.
- **MCP server** for AI agent integration.
- **`--strict-elf-only`** flag: injects PolicyFile override upgrading `FUNC_REMOVED_ELF_ONLY`
  to BREAKING.
- **`compare-release`** composable file-classifier pipeline to identify ABI-relevant files in
  directory scans, ignoring non-ABI files.
- 13 new ABI compatibility test cases (cases 42, 49–62).
- Sentinel enum detection by name pattern (`*_last`/`*_max`/`*_count`).
- `--allow-symbols-only` flag for ELF compare without headers.
- Cross-platform CMake build support for example cases.
- 11 new Architecture Decision Records (ADRs 003–013).
- Renovate bot for dependency updates, GitHub issue/PR templates, CODEOWNERS.

### Changed
- **`checker.py` split into focused modules** (#187): The monolithic checker (3,939 lines) was
  split into `diff_types.py`, `diff_platform.py`, `diff_filtering.py`, `diff_symbols.py`, and
  `checker_types.py`. New `service.py` provides shared orchestration (`resolve_input`,
  `run_dump`, `run_compare`, `render_output`).
- **Standardized error hierarchy**: `PolicyError`, `ReportError` and other domain-specific
  exceptions added to `errors.py`. 46 error sites migrated from generic `RuntimeError`/
  `ValueError`. All inherit from `AbicheckError` and their original builtin for backward compat.
- **Code consolidation** (−226 lines): Shared DWARF utilities (`dwarf_utils.py`), unified
  `binary_utils.py` with `detect_binary_format()`, deduplicated HTML report constants, and
  PDB/PE module cleanup.
- **`--fail-on-additions` removed**: Replaced by the configurable severity system
  (`--severity-additions`).
- **Documentation reorganized**: Complete structure overhaul with standardized file naming.

### Fixed
- **Appcompat DSO scoping**: Symbols from unrelated DSOs (e.g., libexpat symbols when checking
  libz) no longer falsely attributed to the target library (#184).
- **C++ header auto-detection**: `.h` headers default to C mode; auto-detect C++ from structural
  syntax (class/namespace/template). Fixes false mismatches when castxml used wrong language
  mode.
- **C++ DWARF function extraction**: Demangled export index via batch `c++filt` with three-tier
  `_is_exported` check. Fixes missed C++ function detection in DWARF path.
- **Enum change deduplication**: Same-kind symbol-based dedup pass for enum ChangeKinds, preferring
  entries with populated old_value/new_value.
- **Compiler internal type filtering**: Filter `__va_list_tag`, `__builtin_va_list`, etc. from
  DWARF path. Eliminates false positives from compiler internals.
- **PDB struct extraction**: Deferred canonical registration until fields successfully extracted,
  preventing empty layouts from blocking valid later duplicates.
- **Compat HTML ELF-layer miscounting**: ELF-layer changes (soname_changed, etc.) now correctly
  categorized instead of being counted as Interface Problems.
- **Namespace-qualified type names**: Fixed `split("::")[0]` truncating names like
  `ns::MyStruct` to `ns`. Replaced with `_root_type_name()`.
- **Enum rename one-to-one guard**: Prevents aliases from collapsing true removals.
- **DWARF placeholder object check**: Check `dwarf.has_dwarf` flag instead of just `is not None`.
- **`affected_pct` always 0.0%**: `old_symbol_count` now propagated through `_apply_warn_newsym`
  and `_limit_affected_changes`. Capped at 100%.
- **Enum symbol qualification**: Use member-qualified enum symbols (`Color::GREEN`) so AST/DWARF
  dedup works via exact description matching.
- **Human-readable function parameters**: Format params as `int, int*` instead of raw Python repr.
- **PolicyFile on DiffResult**: Store `PolicyFile` on `DiffResult` with `_effective_kind_sets()`
  so policy overrides correctly affect report section classification.
- **Safe output file writing**: `_safe_write_output()` helper with parent directory creation
  replacing bare `write_text()` calls.
- **ELF format validation**: Validate ELF format in `deps_cmd` and `stack_check_cmd` before
  processing, preventing cryptic errors on non-ELF inputs.
- **PIE executable detection**: Distinguish PIE executables from shared libraries via PT_INTERP
  segment check.
- **castxml timeout handling**: Catch `subprocess.TimeoutExpired` with user-friendly error.
  Diagnostic hint when castxml fails in C mode on C++ headers.
- **JSON `--show-only` metadata**: Add `filtered_summary`/`show_only_applied` to JSON output.
  Always include `old_file`/`new_file` keys (null when absent).
- **`--show-only` exit code**: No longer incorrectly affects exit codes (display-only).
- **Library removal verdict**: Elevate verdict to `COMPATIBLE_WITH_RISK` when libraries are
  removed from dependency list.
- **compare-release non-ABI file noise**: Directory scans now ignore scripts, configs, and
  documentation that caused spurious errors.
- **GitHub Action / CLI alignment**: Fixed 7 discrepancies in verdict/exit-code mapping,
  format validation, and severity-preset scoping.
- **DWARFExprOp, TOCTOU, PDB ODR fixes**: Fixed attribute access, file operation races,
  and One Definition Rule handling.
- `enum_last_member_value_changed` downgraded to risk severity in policy.
- ABICC compat: auto-forward `abicheck compat <flags>` to `compat check`.
- Test parity fixes for ABICC 2.3.

### Performance
- **Ancestor function cache**: Each ancestor type scanned at most once in
  `_enrich_affected_symbols`, eliminating quadratic behavior on large diffs.
- **Pre-compiled regex patterns**: Word-boundary patterns in `_filter_redundant`,
  `_is_pointer_only_type`, and `_has_public_pointer_factory` compiled once and cached.
- **ELF section scan optimization**: Capture `.gnu.version` and `.dynsym` sections during main
  `iter_sections()` loop instead of re-scanning.
- **Session-scoped CMake builds**: Integration tests share a single cmake configure pass
  (reduced ~29 passes to 1 on Windows).
- **Parallel test execution**: pytest-xdist support with `--dist worksteal` and filelock-based
  build directory sharing.

### Testing
- Test coverage improved from 86% to 93.4% through systematic review of 117 test files.
- Hypothesis-based property tests: identical snapshots must produce NO_CHANGE, single known
  mutations must detect the specific ChangeKind.
- Exhaustive policy × ChangeKind matrix test: every ChangeKind verified under all 3 policies.
- Ground truth v3 with `expected_kinds` and `expected_absent_kinds` for bidirectional validation.
- macOS ARM64 regression tests and `install_name` edge-case coverage.
- `func_deleted` edge-case regressions for ABICC #100 (`= delete` hardening).
- Fixed trivially-true tests, duplicate test bodies, wrong mocks, and weak assertions.
- Removed duplicate tests, added `slow` marker, parametrized repetitive assertions.

### Platform
- **Linux** (ELF/DWARF) — full support.
- **Windows** (PE/COFF/PDB) — binary metadata and header AST analysis.
- **macOS** (Mach-O/DWARF) — binary metadata and header AST analysis.

### Installation
- Published to **PyPI**: `pip install abicheck`
- Published to **conda-forge**: `conda install -c conda-forge abicheck`

---

## [0.1.0] — 2026-03-13

First public release of abicheck — a modern, Python-native ABI compatibility checker
for C/C++ shared libraries, designed as a drop-in replacement for
[abi-compliance-checker (ABICC)](https://lvc.github.io/abi-compliance-checker/) with
additional capabilities.

### Features

#### Core Analysis
- **Multi-tier detection**: castxml (header AST) + ELF symbol table + DWARF debug info
- **85 ChangeKinds** across BREAKING / API_BREAK / COMPATIBLE severity tiers
- **100% ABICC parity** for 55 documented ABI break scenarios; exceeds ABICC in 6 additional scenarios
- Works on **release builds** with headers + `.so` — no debug symbols required for core checks

#### ABI Break Detection
- Function/variable add/remove/type changes
- Struct/class size, alignment, field offset, vtable changes
- Enum member add/remove/rename/value changes
- Return type, parameter type/count/default changes
- noexcept, virtual, pure-virtual, static, const, volatile method changes
- Base class add/remove/reorder (multiple inheritance)
- Symbol binding/type/visibility changes
- ELF metadata: SONAME, DT_NEEDED, DT_RPATH, IFUNC, symbol versioning, TLS
- DWARF advanced: calling convention, frame register (CFA) drift, value ABI trait
  - CFA extraction uses modal heuristic (not max-PC) to avoid epilogue bias
  - `.dynsym` takes priority over `.symtab` (local symbols never shadow exported names)

#### Policy System
- **Built-in profiles**: `strict_abi` (default), `sdk_vendor`, `plugin_abi`
- **`--policy-file`**: YAML-based per-kind verdict overrides for project-specific rules
- `DiffResult.policy` field — all classification buckets (`breaking`, `source_breaks`, `compatible`) are policy-aware
- Single source of truth: `policy_kind_sets()` in `checker_policy.py`

#### CLI
- `abicheck dump` — create ABI snapshot JSON from `.so` + headers
- `abicheck compare` — diff two snapshots with `--policy`, `--policy-file`, `--format` (markdown/json/sarif/html), `--suppress`
- `abicheck compat` — ABICC drop-in CLI (accepts all ABICC flags)
- `abicheck compat-dump` — create snapshot from ABICC XML descriptor
- `abicheck --version` — print version

#### Reports
- Markdown, JSON, SARIF, HTML report formats
- Split reports: `--bin-report-path` / `--src-report-path` (binary vs source breaks)
- Suppression system: YAML rules with symbol/type/version/platform/scope filters
- RE2-based suppression engine (O(N) guaranteed, no ReDoS)

#### ABICC Compatibility
- Drop-in: all major ABICC flags accepted (`-strict`, `-source`, `-binary`, `-warn-newsym`, etc.)
- ABICC XML descriptor support via `abicheck compat`
- ABICC-compatible HTML report output (`-old-style`)
- Exit codes mirror ABICC (0/1/2)

#### Deployment Risk Verdict
- New verdict `COMPATIBLE_WITH_RISK`: binary-compatible changes that pose a deployment
  risk requiring manual verification of target environment constraints.
- `RISK_KINDS` classification set in `checker_policy.py` (currently: `SYMBOL_VERSION_REQUIRED_ADDED`).
- `DiffResult.risk` property to query risk-classified changes.
- `"risk"` severity level in YAML policy files (maps to `COMPATIBLE_WITH_RISK`).
- `SYMBOL_VERSION_REQUIRED_ADDED` moved from `BREAKING_KINDS` → `RISK_KINDS`:
  new GLIBC version requirements in `DT_VERNEED` now produce `COMPATIBLE_WITH_RISK`
  instead of `BREAKING` — existing compiled consumers are unaffected (already linked).
- `policy_kind_sets()` now returns a 4-tuple `(breaking, api_break, compatible, risk)`.
- `plugin_abi` policy treats `SYMBOL_VERSION_REQUIRED_ADDED` as `BREAKING`
  (host/plugin deployment-floor raise is an in-process load blocker).
- `_apply_warn_newsym` promotes `COMPATIBLE_WITH_RISK` → `BREAKING` when `-warn-newsym` is active.

#### SARIF exit code changes (migration note)
- `BREAKING`: exit code `1` → `4`
- `API_BREAK`: now emits `2` (was `0`)
- `COMPATIBLE_WITH_RISK`: emits `0` (binary-compatible; risk surfaced via `exitCodeDescription`)
- If your CI pipeline checks `exitCode == 1` on BREAKING, update to `exitCode == 4`.

### Platform
- **Linux only** (ELF/DWARF). Windows (PE) and macOS (Mach-O) are not yet supported.

### Installation
- **From source**: `pip install abicheck` or `pip install -e ".[dev]"` for development.
- `castxml` must be installed separately via system packages (`apt install castxml`)
  or conda-forge (`conda install -c conda-forge castxml`)

### Requirements
- Python ≥ 3.10
- `castxml` (mandatory — for header-based C/C++ AST parsing; included in conda-forge install)
- `g++` or `clang++` (accessible to castxml)
- See [Installation](docs/getting-started.md) for full setup instructions

### Known Limitations

- **Suppression system**: label/tag-based suppression, file-scoped suppression (by `source_location`),
  and suppression expiry dates are not yet implemented. Resolved in v0.2.0.

---

[0.1.0]: https://github.com/napetrov/abicheck/releases/tag/v0.1.0
[0.2.0]: https://github.com/napetrov/abicheck/releases/tag/v0.2.0
[Unreleased]: https://github.com/napetrov/abicheck/compare/v0.2.0...HEAD
