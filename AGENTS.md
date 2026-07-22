# AGENTS.md — Canonical instructions for coding agents working on abicheck

This is the **canonical, vendor-neutral** repository contract (CLAUDE.md
"M1-1"). Every tool-specific instruction surface is a thin adapter that
points back here instead of maintaining its own copy:

| File | Role |
|------|------|
| `AGENTS.md` (this file) | Canonical instructions — the source of truth |
| `CLAUDE.md` | Claude Code bootstrap — imports this file via `@AGENTS.md` |
| `.github/copilot-instructions.md` | GitHub Copilot adapter — points here |
| `.cursor/rules/abicheck.mdc` | Cursor adapter — points here |

If you're editing repository-wide instructions, edit **this file**. Don't
hand-duplicate a command or invariant into an adapter — adapters exist so
each tool's convention is satisfied without a second copy to drift.
Sub-directory `CLAUDE.md` files (`abicheck/CLAUDE.md`, `tests/CLAUDE.md`,
etc.) are scoped, per-area context, not adapters to this file — they stay as
they are.

## What is abicheck?

ABI compatibility checker for C/C++ shared libraries. Pure Python (3.10+).
Detects 394 ABI/API change types across ELF, PE/COFF, and Mach-O binaries,
categorized into `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, and `RISK_KINDS` (see `ChangeKind`).
Drop-in replacement for abi-compliance-checker (ABICC).

**Two different Python version numbers matter here, don't conflate them:**
`pyproject.toml`'s `requires-python = ">=3.10"` is the *minimum supported*
version (what a user's environment needs to run abicheck) — CI tests 3.12,
3.13, and 3.14 across platforms to keep that floor honest. **3.13** is the
*canonical development/CI* version — `repo_facts.json`'s `canonical_python`,
the single Linux lane the 95% coverage floor runs on (see "Line-coverage
floor" below), and what the `ai-readiness` CI job (including its
`repo_facts.json` mypy-baseline recheck) pins to. The separate
`lint-and-types` job that gates `mypy abicheck/` cleanliness on every PR
runs on 3.14, matching the other non-canonical lanes. When in doubt about
which Python to develop against locally, use 3.13.

## Quick reference

```bash
# Install in dev mode (do this first if pytest/ruff/mypy are missing)
pip install -e ".[dev]"

# Run fast unit tests (THE go-to command — ~43s, ~5400 tests)
pytest tests/ -m "not integration and not libabigail and not abicc and not slow and not golden" -q

# Lint (must pass, CI enforces)
ruff check abicheck/ tests/

# Type check (CI runs this — see "Known mypy issues" below)
mypy abicheck/

# Format check
ruff format --check abicheck/ tests/
```

### M0-3: `scripts/verify.py` — the one verification contract

The four commands above are the everyday inner loop, but they are **not**
the definition of "ready for PR" — the canonical CI unit lane runs golden
tests and enforces a 95% coverage floor that the fast command above
deliberately skips. `scripts/verify.py` is the single executable
orchestrator every consumer (pixi, pre-commit, CI, this file) calls through,
so the local and CI definitions of done cannot silently diverge again:

```bash
python scripts/verify.py --profile fast   # the four commands above, bundled
python scripts/verify.py --profile pr     # exact CI-equivalent PR gate (incl. golden + coverage floor + ai-readiness)
python scripts/verify.py --profile full   # + external-tool/parity/performance lanes, skipped where the environment lacks the tool

python scripts/verify.py --profile pr --list          # show the steps a profile runs, without running them
python scripts/verify.py --profile pr --only lint,typecheck   # run a subset
python scripts/verify.py --profile pr --json receipt.json     # machine-readable pass/fail/skip receipt
```

**Before opening a PR, run `--profile pr` (or `pixi run check`, which calls
the identical command) — not just the fast command above.**
`tests/test_verify_profiles.py` asserts that `pixi run check`,
`.pre-commit-config.yaml`, and `.github/workflows/ci.yml` all route through
`scripts/verify.py`'s step catalog rather than keeping independent copies of
these commands; if you change a check, change it in `scripts/verify.py` and
let that test tell you what else needs updating.

**`pip install -e ".[dev]"` alone is not full `pr`-profile parity.** The
`docs-build` step needs `mkdocs` (`pip install -e ".[dev,docs]"`) and the
`distribution-build` step needs `build`/`twine` (`pip install -e ".[dev,dist]"`)
— neither is in bare `[dev]`, matching the CI `lint-and-types`/`fair-metadata`
jobs' separate installs. Run `pip install -e ".[dev,docs,dist]"` for full
parity. `verify.py` never silently claims success when a step like this is
skipped for a missing tool: a `pr`-profile run with any skip prints an
explicit `WARNING: this pr-profile run is INCOMPLETE` line and sets
`"complete": false` in the `--json` receipt — don't treat a skip-containing
run as equivalent to a clean CI pass.

[pixi](https://pixi.sh) is also supported (`pixi install && pixi run test`,
`pixi run check`) and additionally manages the `castxml`/compiler/`libabigail`/
`abi-compliance-checker` system tools for the `integration`/`libabigail`/`abicc`
marker lanes below — see `[tool.pixi.*]` in `pyproject.toml` and
`CONTRIBUTING.md`. Unlike bare `pip install -e ".[dev]"`, pixi's `default`
environment includes the `docs` and `dist` features too, so `pixi run check`
is complete out of the box. Prefer `pip install -e ".[dev]"` above when pixi
isn't available in your environment (add `,docs,dist` for full parity).

## Test markers — know which tests you can run

| Marker | What it needs | When to use |
|--------|--------------|-------------|
| *(default)* | Python only | Always run these — fast, no external deps |
| `integration` | castxml + gcc/g++ | Only if modifying DWARF/ELF parsing |
| `libabigail` | abidiff + gcc/g++ | Only for parity testing |
| `abicc` | abi-compliance-checker + gcc/g++ | Only for parity testing |
| `msvc` | MSVC `cl.exe` (Windows) | Only for the MSVC+PDB end-to-end lane |
| `slow` | varies | Hypothesis/perf benchmarks, skip in normal dev |
| `golden` | golden files | Snapshot tests, skip unless changing output format |

**Default fast command excludes all external-tool markers.** Use it.

## Architecture — module map

Entry points:
- `abicheck/cli.py` — Click CLI (large file, at the 2000-line hard cap; be careful with edits)
- `abicheck/compat/cli.py` — ABICC-compatible CLI wrapper
- `abicheck/mcp_server.py` — MCP server for AI agent integration
- `abicheck/__main__.py` — `python -m abicheck` entry

Core pipeline (in order of data flow):
1. **Parsing** — extract metadata from binaries
   - `elf_metadata.py`, `pe_metadata.py`, `macho_metadata.py` — platform-specific
   - `dwarf_metadata.py`, `dwarf_advanced.py`, `dwarf_unified.py` — DWARF debug info
   - `pdb_parser.py`, `pdb_metadata.py`, `pdb_utils.py` — Windows PDB
   - `btf_metadata.py`, `ctf_metadata.py` — Linux kernel debug formats
   - `sycl_metadata.py` — SYCL plugin interface
2. **Snapshot** — `dumper.py` creates `AbiSnapshot` (model in `model.py`)
   - `dumper_castxml.py` — castxml XML → model parser (default L2 header backend)
   - `dumper_clang.py` — `clang -ast-dump=json` → model parser (alternative L2
     backend for clang-only hosts; `--ast-frontend clang` /
     `ABICHECK_AST_FRONTEND=clang`). Both parsers expose the same `parse_*`
     surface behind `dumper._header_ast_parser`.
   - `dwarf_snapshot.py` — DWARF-specific snapshot logic
   - `snapshot_cache.py` — caching layer
3. **Diffing** — compare two snapshots
   - `diff_symbols.py` — function/variable/parameter changes
   - `diff_types.py` — struct/enum/union/typedef changes
   - `diff_platform.py` — ELF/PE/Mach-O specific changes
   - `diff_elf_layout.py` — binary-only (no-DWARF/L0) vtable & RTTI layout diff from `_ZTV`/`_ZTI` symbol sizes
   - `diff_filtering.py` — deduplication and redundancy removal
   - `diff_versioning.py` — symbol version checks
   - `diff_sycl.py` — SYCL-specific diffs
4. **Detection** — classify changes
   - `detectors.py` — individual detection rules
   - `detector_registry.py` — registry pattern for detectors
   - `checker.py` — main comparison orchestrator
   - `checker_types.py` — `DiffResult`, result types
   - `checker_policy.py` — verdict classification (ChangeKind enum lives here)
5. **Policy & Suppression**
   - `policy_file.py` — YAML policy profiles
   - `suppression.py` — suppression rules (YAML + ABICC formats)
   - `severity.py` — severity configuration
6. **Reporting** — output results
   - `reporter.py` — JSON/Markdown/text output
   - `html_report.py` — HTML reports
   - `sarif.py` — SARIF 2.1.0 output
   - `junit_report.py` — JUnit XML output
   - `report_summary.py`, `report_classifications.py` — report helpers
7. **Application compatibility** — `appcompat.py`, `appcompat_html.py`
8. **Utilities**
   - `binary_utils.py` — binary file helpers
   - `binary_fingerprint.py` — rename detection via fingerprinting
   - `demangle.py` — C++ name demangling
   - `classify.py` — symbol classification
   - `annotations.py` — annotation handling
   - `errors.py` — exception types
   - `serialization.py` — snapshot serialization
   - `package.py` — package/archive handling
   - `debian_symbols.py` — Debian symbols file adapter
   - `environment_matrix.py` — multi-env comparison
   - `binder.py` — symbol binding logic
   - `resolver.py` — symbol resolution
   - `type_metadata.py`, `dwarf_utils.py` — shared type helpers
   - `change_registry.py` — change kind registry
   - `service.py` — service layer (Python API)
   - `stack_checker.py`, `stack_report.py`, `stack_html.py` — stack analysis
9. **Build-source evidence (optional L3–L5 layers)** — `buildsource/` package
   (collect/merge/source-ABI replay/source graph; ADR-028…033). See
   `abicheck/buildsource/CLAUDE.md` for its module map.

Beyond the core package: `.github/AGENTS.md` (CI/workflow architecture),
`action/AGENTS.md` (the composite GitHub Action's shell-script layer), and
`contrib/abicheck-clang-plugin/AGENTS.md` (the optional Clang facts plugin)
cover the surrounding first-party trees this file doesn't detail.

## Key types

- `AbiSnapshot` (`model.py`) — serializable snapshot of a library's ABI surface
- `DiffResult` (`checker_types.py`) — single detected change with kind, severity, details
- `ChangeKind` (`checker_policy.py`) — enum of 394 change types; categorized into `BREAKING_KINDS`, `API_BREAK_KINDS`, `RISK_KINDS`, and `COMPATIBLE_KINDS` (further split into `ADDITION_KINDS` and `QUALITY_KINDS`)
- `Verdict` (`checker.py`) — overall comparison result (compatible/source_break/breaking)
- `LibraryMetadata` (`checker.py`) — parsed library info

## Adding a new ChangeKind

1. Add to `ChangeKind` enum in `checker_policy.py`.
2. Add ONE `ChangeKindMeta` entry (kind string, `default_verdict`, optional
   `impact`/`description_template`) to `abicheck/change_registry.py` or one
   of its sibling `change_registry_<topic>.py` files (`_castxml`,
   `_buildsource`, `_composition`, `_coverage`, `_numpy`, `_suppression` —
   split out only to stay under the file-size cap; declaring an entry in any
   of them is equivalent). **Do NOT hand-edit `BREAKING_KINDS`/
   `API_BREAK_KINDS`/`COMPATIBLE_KINDS`/`RISK_KINDS` in `checker_policy.py`
   directly** — those are `frozenset`s *derived* from the registry at import
   time (`_kinds_for(...)`); the registry entry's `default_verdict` is what
   actually places a kind into one of them, and the import-time completeness
   assertion checks the derived sets, not a set you'd edit by hand.
3. Implement detection in the appropriate diff module, registered via
   `@registry.detector("...")` (`detector_registry.py`) the way the
   neighboring detectors in that file are.
4. Add unit test.

## Conventions

- **Commits**: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`)
- **Branches**: `feat/<name>` or `fix/<name>`
- **Python**: 3.10+ syntax, type annotations, `from __future__ import annotations`
- **No line length limit** (ruff E501 ignored)
- **Tests**: use `assert` freely; parametrize when possible
- **Changelog**: if your change touches `abicheck/**/*.py`, add a fragment
  with `scriv create` — writes `changelog.d/<name>.md`; uncomment one
  `### <Category>` section and describe the change (see
  `changelog.d/README.md`). Do **not** hand-edit `CHANGELOG.md`'s
  `## [Unreleased]` section — CI (`changelog-check.yml`) rejects a PR that
  touches `abicheck/**/*.py` without a fragment, and every PR editing that
  shared section directly was the reason it kept conflicting.

## Known mypy issues

CI runs `mypy abicheck/` as a required gate. The baseline is currently **0 errors** — the previously-documented 26 errors were all `unused-ignore` / `no-any-return` / `misc` warnings on third-party calls (pyelftools, click). They are suppressed in `pyproject.toml` via per-module `disable_error_code` overrides, which keeps the file portable across mypy releases without churning the underlying `# type: ignore` comments.

**Your responsibility**: run `mypy abicheck/` after your changes and ensure it stays clean. If a new third-party suppression is needed, extend the existing `disable_error_code` override for that module rather than scattering ad-hoc `# type: ignore` comments. If you legitimately reduce a real error to zero, leave `MYPY_ERROR_BASELINE = 0` in `scripts/check_ai_readiness.py` — it now warns on drift in either direction.

## AI-readiness gate

`scripts/check_ai_readiness.py` runs in CI as a fast structural gate. It checks:

| Check | Severity | What it enforces |
|-------|----------|------------------|
| `file-size` | ERROR > 2000 lines, WARN > 1500 | Every first-party Python tree (`abicheck/`, `scripts/`, `tests/`, `eval/`, `validation/`, `action/`, the clang plugin's `tests/` — `FIRST_PARTY_PY_ROOTS`) stays legible. `LARGE_FILE_ALLOWLIST` downgrades a specific pre-existing violator to WARN with a reviewed reason — it is not a way to silently exempt a new file |
| `claude-md-coverage` | ERROR | `CLAUDE.md` exists in each original major sub-tree (`REQUIRED_CLAUDE_MD_DIRS`) |
| `agent-instructions-coverage` | ERROR | `AGENTS.md` or `CLAUDE.md` exists in `.github/`, `action/`, `contrib/abicheck-clang-plugin/` (`REQUIRED_AGENT_INSTRUCTION_DIRS`) |
| `script-inventory` | WARN | Every `scripts/*.py` is named in `scripts/CLAUDE.md`'s inventory table — an unlisted script is invisible to that discovery path |
| `generated-file-ownership` | ERROR | A known-generated file (`GENERATED_FILE_MARKERS`, plus every `docs/examples/case*.md`) still carries its "this is generated, don't hand-edit" marker comment |
| `test-ratio` | WARN | At least 20% test-to-source file ratio; test files are discovered recursively under `tests/` (not just top-level) |
| `future-annotations` | WARN | `from __future__ import annotations` per this file's convention |
| `changekind-partition` | ERROR | Every `ChangeKind` is in exactly one of `BREAKING_KINDS` / `API_BREAK_KINDS` / `COMPATIBLE_KINDS` / `RISK_KINDS` |
| `changekind-detector` | WARN | Every `ChangeKind` is produced somewhere (not orphaned) |
| `changekind-docs` | WARN | Every `ChangeKind` is mentioned in `docs/` |
| `doc-count-sync` | ERROR on drift, WARN if anchor moved | Headline counts in docs (ChangeKind count, example-catalog size) match their source of truth (`len(ChangeKind)`, `ground_truth.json`) — this file (`AGENTS.md`) is included in the generic sweep, same as `README.md`/`CLAUDE.md` |
| `cli-contract` | ERROR | No front-end `cli*.py` module calls Tier-1 `checker.compare` directly — it must route through the Tier-2 service (`service.run_compare`/`compare_snapshots`); ADR-037 D10.1 |
| `import-cycle-growth` | ERROR | No *unapproved* strongly-connected-component growth within `abicheck/` — not literally "no import cycles": a large, deliberately-baselined CLI-registration SCC already exists and is allowed (`IMPORT_CYCLE_ALLOWLIST`). The invariant is that no *new* module joins it and no *new* separate SCC forms; extending the allowlist to unblock a fresh cycle needs an ADR or explicit architectural sign-off, not a routine edit (CLAUDE.md "M1-3") |
| `mypy-baseline` | ERROR if drifted up | mypy error count ≤ documented baseline |
| `examples-ground-truth` | ERROR | Every `examples/case*/` has a `README.md` and an entry in `ground_truth.json` |
| `examples-readme-sync` | ERROR | `examples/README.md` headline count, verdict distribution, and case-index rows match `ground_truth.json` (catches missing/stale catalog rows) |
| `mkdocs-nav-coverage` | WARN | Every `docs/**/*.md` is in `mkdocs.yml` nav or linked from another doc |
| `adr-index-nav-sync` | ERROR | Every `docs/development/adr/*.md` is linked from `adr/index.md`, and the ADR index page itself (not each individual ADR — relaxed, since that overloaded top-level nav with 50+ flat entries for no reader benefit) is listed in `mkdocs.yml`'s nav, so every ADR stays reachable from published navigation (this is what originally caught ADR-041 going missing from nav despite being accepted). Also requires every ADR to carry a Status metadata line/heading, and an ADR whose status leads with "Superseded" to link to its replacement |
| `banned-imports` | ERROR | No `print(...)` outside CLI/reporter modules; no `subprocess(..., shell=True)` |
| `license-header` | WARN | Every `abicheck/**/*.py` carries the Apache-2.0 header / SPDX identifier |
| `test-assertion-density` | WARN | Every `test_*` function asserts something (directly or via a same-file helper) — flags zero-assertion smoke tests so coverage isn't "filled" without verification |

Run locally: `python scripts/check_ai_readiness.py`. Errors fail; warnings print and pass.

## Test-quality gates (beyond line coverage)

Line coverage measures *reach*, not whether a test actually checks the result.
Several mechanisms guard test quality so coverage can't be "filled" without verifying behaviour:

- **FP-rate gate** — `scripts/check_fp_rate.py` (mirrored in `tests/test_fp_rate_gate.py`).
  A labelled corpus of `(old, new)` snapshot pairs run under public-surface scoping:
  internal-noise cases must stay non-breaking (no false positives), real-break cases
  must stay breaking (no false negatives). Both baselines are 0; grow the corpus only
  with cases the correct implementation already passes. Cases carry a scoping *axis*
  tag (`CASE_CATEGORY`); `--markdown`/`--json` emit a per-axis FP/FN breakdown for trend
  tracking.
- **Per-tier accuracy gate** — `scripts/check_tier_accuracy.py` (mirrored in
  `tests/test_tier_accuracy_gate.py`). Complements the FP-rate gate by measuring *what
  each evidence level buys*: one labelled change per case is projected down to what each
  tier observes (L0 symbols → L1 debug → L2 headers → L3 build) and run through `compare`;
  verdicts collapse to a 3-band ordinal (non-breaking/risk/breaking). It records, per
  tier, over-calls (false positives) vs under-calls (false negatives) — encoding the
  principle that **adding a layer reduces both** (L1 sees layout but over-calls internal
  churn; L2 scoping removes it; L0/L1 under-call breaks only headers/build see). Gates on
  top-tier correctness + under-call monotonicity (more evidence never hides a break an
  earlier tier caught — authority rule). CI posts the matrix to the step summary. User
  docs: `docs/concepts/evidence-and-detectability.md` § "What each layer buys".
- **Mutation testing** — `scripts/check_mutation_score.py` + `.github/workflows/mutation.yml`.
  `mutmut` mutates the detector core (`diff_*`, `checker_policy`); a *surviving* mutant
  is a covered-but-unverified line. Runs weekly / on the `mutation` PR label, gating on a
  survivor baseline (`SURVIVOR_BASELINE`) once the first run establishes it.
- **Metamorphic property tests** — `tests/test_detector_properties.py` (`slow`).
  Hypothesis-generated snapshot pairs checked against invariants that hold for *any*
  input (idempotence, determinism, direction-symmetry of touched symbols, emitted-kind
  partition, additive monotonicity) — generalization guards, not example-shaped tests.
- **Silent-skip guard** — `tests/conftest.py`. A marker lane can export
  `ABICHECK_MIN_EXECUTED=<n>`; the session fails unless at least `<n>` tests actually ran,
  so a missing external tool can't turn a lane green with zero work done. Wired into the
  `abicc`, `libabigail`, and `integration` CI lanes.

## Line-coverage floor

The `pr` profile's `unit-pr` step (`scripts/verify.py`) enforces a **95%**
line+branch coverage floor (`--cov-fail-under=95`) — the `fast` profile does
not, since it's the everyday inner loop and deliberately skips coverage
instrumentation. This floor applies **only on the canonical Linux/Python-3.13
unit-test lane** in `.github/workflows/ci.yml` — that's where the full unit
suite runs under coverage.
The other Linux Pythons (3.12/3.14) run the same suite *without* coverage (they would
only re-check the identical floor, and coverage instrumentation adds ~60% wall time).
macOS/Windows skip the Linux-only ELF/DWARF parsing tests, which structurally lowers
their coverage (~93% on macOS), so those lanes run the same tests without the
fail-under gate (macOS still emits a coverage report). Coverage uses the
`sys.monitoring` backend (`COVERAGE_CORE=sysmon`, Python 3.12+) to keep the
instrumentation cheap. If the macOS lane ever fails on coverage, the fix is to keep the
gate Linux-scoped — **do not lower the global 95% floor** to make another platform pass.

## Files that are large — edit carefully

**Don't trust hard-coded line counts — they drift.** The AI-readiness gate is the
source of truth: it WARNs on any file >1500 lines and ERRORs >2000 (hard cap, no
allowlist). To see today's large files, run:

```bash
python scripts/check_ai_readiness.py 2>&1 | grep "exceeds soft limit"
```

As of this writing the WARN set (>1500 lines) is `cli.py`, `dumper.py`, and
`buildsource/crosscheck.py` — the main CLI, binary-metadata extraction, and the
cross-check engine. Treat that command output (not this sentence) as current.

When editing any large file, read the specific section you need rather than the
whole file. Several big commands have already been split into sibling
`cli_<name>.py` / `diff_*` modules (see the module map above); prefer extending a
split-out module over growing the parent toward the cap.

### Adding a new top-level command

Pick the right home:

- **Small command (one function, no significant helpers)** — add to `cli.py` directly with `@main.command(...)`.
- **Larger command or command group** — add as a sibling `abicheck/cli_<name>.py` module:
  1. Top of module: `from .cli import main` (and any shared `_helpers`).
  2. Decorate with `@main.command("foo")` or `@main.group("foo")` as usual.
  3. At the bottom of `cli.py`, add `cli_<name>` to the side-effect `from . import (...)` block — that runs after `main` and helpers are defined, registering the new command.
  4. If the new module uses `@click` decorators, add `abicheck.cli_<name>` to the `disallow_untyped_decorators = false` override in `pyproject.toml` (alongside the existing entries).
  5. If `scripts/check_ai_readiness.py` flags a cycle, this is `IMPORT_CYCLE_ALLOWLIST`'s known CLI-registration cluster — see "What NOT to do" below before extending it.
  6. **Shared utility flags go through a decorator, not an inline copy.** `-v/--verbose` is `@verbose_option`, `--format`/`-o/--output` are `output_options(...)`, language is `lang_option(...)` (all in `cli_options.py`). Every visible option must carry `help=` text and a shared concept must use one canonical primary spelling — both are enforced by `tests/test_cli_contract.py` (`test_no_option_has_empty_help`, `test_shared_concept_canonical_spelling`).
  7. **Moving helpers out of a module that re-exports them?** If you relocate a helper that an existing module re-exports "for API stability / tests" (e.g. the `cli_buildsource` block), preserve the old import path with a lazy module-level `__getattr__` shim that resolves via `importlib.import_module` — a static `from .new_module import …` re-export would re-introduce the import cycle the split was meant to avoid (see the shim at the tail of `cli_buildsource.py`).

## Exit codes

- `compare` command (legacy, without `--severity-*` flags): 0 = compatible, 2 = source break, 4 = ABI break
- `compare` command (severity-aware, with any `--severity-*` flag): 0 = no error-level findings, 1 = error in addition/quality only, 2 = error in potential_breaking, 4 = error in abi_breaking
- `compat` command: 0 = compatible, 1 = BREAKING, 2 = API_BREAK (source-level), 3-11 = errors (see `compat/cli.py:_classify_compat_error_exit_code`)
- `64` = usage error (bad flags/inputs; `cli._EXIT_USAGE_ERROR`) — applies across commands
- Full per-command matrix: `docs/reference/exit-codes.md`

## Known gaps — acknowledged remaining work

- **Depth contract, CLI vs. API/MCP** (CLAUDE.md "M1-6"): PR #601 (open) adds
  a hard-fail `DumpDepthNotSatisfiedError` when an explicit `dump --depth`
  isn't actually reached — but only at the CLI entry point (`cli.py` /
  `cli_dump_helpers.py`). `action/run.sh` already propagates that (or any
  other) nonzero `dump` exit code as an Action failure unconditionally — no
  fix needed there. The gap is `abicheck/service.py`'s `ScanRequest`/
  `run_scan_subprocess` and `abicheck/mcp_server.py`'s MCP tools (see
  `_validate_public_depth`'s docstring): neither enforces the same
  requested-vs-achieved check, so a Python-API caller or an MCP-driven agent
  passing an explicit `depth=` can silently get a shallower-tier result. Once
  PR #601 merges, extend the same check to those two call paths.

- **Action pinning is deliberately partial, not a full sweep.** Third-party
  GitHub Actions in `.github/workflows/agentready.yml`, `ci.yml` (the
  `id-token: write` jobs), `pages.yml`, `publish.yml`, and `security.yml` are
  pinned to a full commit SHA (with a `# <tag>` comment) rather than a
  mutable tag/branch — those five carry `security-events:write`,
  `pull-requests:write`, `contents:write`, or `id-token:write` (OIDC/PyPI
  Trusted Publishing), so a re-pointed tag there is a real supply-chain risk.
  Other workflows (`test-action.yml`, `eval-suite.yml`, `performance.yml`,
  `realworld-validation.yml`, `dependency-review.yml`, and any future ones)
  still use tags — deliberately deferred, since they only run with
  `contents: read` and don't touch secrets/publishing/security-event write
  access, so the blast radius of a compromised tag there is far smaller.
  Extend the same pinning to a workflow only when it gains elevated
  permissions, not preemptively.
- **CODEOWNERS risk tiers currently all resolve to one person.** The file is
  structured by risk tier (CRITICAL/HIGH/STANDARD) so a second maintainer
  can be slotted into CRITICAL/HIGH without restructuring, but there is
  only one maintainer today — don't read the tiering as "these are reviewed
  by different people," it isn't, yet.
- **Deferred entirely, not attempted this pass** (heavier structural
  changes, each needing its own scoped design rather than a drive-by
  addition):
  - *Devcontainer image* — a maintained `.devcontainer/` needs a decision on
    which system tools (castxml, libabigail, abi-compliance-checker,
    compilers) ship baked-in vs. installed on first use, and upkeep as those
    pins drift; `pixi` (see CONTRIBUTING.md) already solves the "one command
    gets you a working dev environment" problem this would target, without
    the image-maintenance burden.
  - *Trend-reporting database* — persisting `scripts/check_tier_accuracy.py`
    /`check_fp_rate.py`/mutation-score history across runs (rather than each
    CI run only gating against a static baseline) needs a storage decision
    (artifact-based vs. external DB) and a retention/access policy before
    it's worth building.
  - *Full behavioral baseline* — `agent-evals/` (this pass, M1-5) is a real
    but minimal harness with one task; a "full behavioral baseline" implies
    a broad task suite plus a scoring/leaderboard story, which should grow
    from real usage of the one-task harness rather than being speculatively
    built out now.

## What NOT to do

- Don't hand-edit `CHANGELOG.md`'s `## [Unreleased]` section directly — add a `changelog.d/` fragment instead (see Conventions above); CI enforces this
- Don't modify `examples/` test cases without understanding the ground truth they encode
- Don't add dependencies without strong justification (this is a lightweight tool)
- Don't skip test markers — if a test needs `castxml`, mark it `@pytest.mark.integration`
- Don't "fix" the mypy errors listed above by adding `# type: ignore` broadly
- Don't modify binary test fixtures without regenerating expected outputs
- Don't change public API signatures without checking for breaking changes
- Don't add platform-specific code without considering cross-platform compatibility
- Don't extend `IMPORT_CYCLE_ALLOWLIST` in `scripts/check_ai_readiness.py` to make a new cycle pass, and never as a routine step to unblock CI. The existing large CLI/service entry documents an accepted, by-design registration pattern (Click sibling commands registering back on `cli.main`) — a *new* member outside that documented pattern is very likely a real dependency-direction problem, not another instance of it. Prefer a function-local import or moving the shared logic to a leaf module both sides can depend on. If the coupling really is intentional, extending the allowlist needs an ADR (or explicit maintainer sign-off) recorded in the PR, the same bar as any other architectural exception — not a comment justifying it inline and moving on.
- Don't hand-duplicate a command, invariant, or count from this file into an adapter (`CLAUDE.md`, `.github/copilot-instructions.md`, `.cursor/rules/`) — point the adapter back here instead (see the table at the top of this file).
