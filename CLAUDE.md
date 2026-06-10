# CLAUDE.md — Instructions for AI agents working on abicheck

## What is abicheck?

ABI compatibility checker for C/C++ shared libraries. Pure Python (3.10+).
Detects 215 ABI/API change types across ELF, PE/COFF, and Mach-O binaries,
categorized into `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, and `RISK_KINDS` (see `ChangeKind`).
Drop-in replacement for abi-compliance-checker (ABICC).

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
- `abicheck/cli.py` — Click CLI (large file, ~2600 lines; be careful with edits)
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
   - `dwarf_snapshot.py` — DWARF-specific snapshot logic
   - `snapshot_cache.py` — caching layer
3. **Diffing** — compare two snapshots
   - `diff_symbols.py` — function/variable/parameter changes
   - `diff_types.py` — struct/enum/union/typedef changes
   - `diff_platform.py` — ELF/PE/Mach-O specific changes
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

## Key types

- `AbiSnapshot` (`model.py`) — serializable snapshot of a library's ABI surface
- `DiffResult` (`checker_types.py`) — single detected change with kind, severity, details
- `ChangeKind` (`checker_policy.py`) — enum of 215 change types; categorized into `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, `RISK_KINDS`
- `Verdict` (`checker.py`) — overall comparison result (compatible/source_break/breaking)
- `LibraryMetadata` (`checker.py`) — parsed library info

## Adding a new ChangeKind

1. Add to `ChangeKind` enum in `checker_policy.py`
2. Place in exactly one of `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, or `RISK_KINDS` (import-time assertion enforces completeness)
3. Implement detection in the appropriate diff module
4. Add unit test

## Conventions

- **Commits**: Conventional Commits (`feat:`, `fix:`, `test:`, `docs:`, `refactor:`)
- **Branches**: `feat/<name>` or `fix/<name>`
- **Python**: 3.10+ syntax, type annotations, `from __future__ import annotations`
- **No line length limit** (ruff E501 ignored)
- **Tests**: use `assert` freely; parametrize when possible

## Known mypy issues

CI runs `mypy abicheck/` as a required gate. The baseline is currently **0 errors** — the previously-documented 26 errors were all `unused-ignore` / `no-any-return` / `misc` warnings on third-party calls (pyelftools, click). They are suppressed in `pyproject.toml` via per-module `disable_error_code` overrides, which keeps the file portable across mypy releases without churning the underlying `# type: ignore` comments.

**Your responsibility**: run `mypy abicheck/` after your changes and ensure it stays clean. If a new third-party suppression is needed, extend the existing `disable_error_code` override for that module rather than scattering ad-hoc `# type: ignore` comments. If you legitimately reduce a real error to zero, leave `MYPY_ERROR_BASELINE = 0` in `scripts/check_ai_readiness.py` — it now warns on drift in either direction.

## AI-readiness gate

`scripts/check_ai_readiness.py` runs in CI as a fast structural gate. It checks:

| Check | Severity | What it enforces |
|-------|----------|------------------|
| `file-size` | ERROR > 2000 lines, WARN > 1500 | Source files stay legible (no allowlist) |
| `claude-md-coverage` | ERROR | `CLAUDE.md` exists in each major sub-tree |
| `test-ratio` | WARN | At least 20% test-to-source file ratio |
| `future-annotations` | WARN | `from __future__ import annotations` per CLAUDE.md convention |
| `changekind-partition` | ERROR | Every `ChangeKind` is in exactly one of `BREAKING_KINDS` / `API_BREAK_KINDS` / `COMPATIBLE_KINDS` / `RISK_KINDS` |
| `changekind-detector` | WARN | Every `ChangeKind` is produced somewhere (not orphaned) |
| `changekind-docs` | WARN | Every `ChangeKind` is mentioned in `docs/` |
| `doc-count-sync` | ERROR on drift, WARN if anchor moved | Headline counts in docs (ChangeKind count, example-catalog size) match their source of truth (`len(ChangeKind)`, `ground_truth.json`) |
| `import-cycles` | ERROR | No import cycles within `abicheck/` |
| `mypy-baseline` | ERROR if drifted up | mypy error count ≤ documented baseline |
| `examples-ground-truth` | ERROR | Every `examples/case*/` has a `README.md` and an entry in `ground_truth.json` |
| `examples-readme-sync` | ERROR | `examples/README.md` headline count, verdict distribution, and case-index rows match `ground_truth.json` (catches missing/stale catalog rows) |
| `mkdocs-nav-coverage` | WARN | Every `docs/**/*.md` is in `mkdocs.yml` nav or linked from another doc |
| `banned-imports` | ERROR | No `print(...)` outside CLI/reporter modules; no `subprocess(..., shell=True)` |
| `license-header` | WARN | Every `abicheck/**/*.py` carries the Apache-2.0 header / SPDX identifier |
| `test-assertion-density` | WARN | Every `test_*` function asserts something (directly or via a same-file helper) — flags zero-assertion smoke tests so coverage isn't "filled" without verification |

Run locally: `python scripts/check_ai_readiness.py`. Errors fail; warnings print and pass.

## Test-quality gates (beyond line coverage)

Line coverage measures *reach*, not whether a test actually checks the result.
Four mechanisms guard test quality so coverage can't be "filled" without verifying behaviour:

- **FP-rate gate** — `scripts/check_fp_rate.py` (mirrored in `tests/test_fp_rate_gate.py`).
  A labelled corpus of `(old, new)` snapshot pairs run under public-surface scoping:
  internal-noise cases must stay non-breaking (no false positives), real-break cases
  must stay breaking (no false negatives). Both baselines are 0; grow the corpus only
  with cases the correct implementation already passes.
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

The fast lane enforces a **95%** line+branch coverage floor (`--cov-fail-under=95`),
but **only on the Linux unit-test lane** in `.github/workflows/ci.yml` — that's where
the full unit suite runs. macOS/Windows skip the Linux-only ELF/DWARF parsing tests,
which structurally lowers their coverage (~93% on macOS), so those lanes run the same
tests without the fail-under gate (macOS still emits a coverage report). If the macOS
lane ever fails on coverage, the fix is to keep the gate Linux-scoped — **do not lower
the global 95% floor** to make another platform pass.

## Files that are large — edit carefully

- `cli.py` (~1,500 lines) — main CLI, Click commands; sub-command modules below register on it
- `cli_compare_release.py` (~950 lines) — `compare-release` command and helpers (split from `cli.py`)
- `cli_appcompat.py` (~280 lines) — `appcompat` command and helpers (split from `cli.py`)
- `cli_baseline.py` (~240 lines) — `baseline` command group (split from `cli.py`)
- `cli_stack.py` (~190 lines) — `deps` and `stack-check` commands (split from `cli.py`)
- `cli_debian_symbols.py` (~130 lines) — `debian-symbols` command group (split from `cli.py`)
- `cli_suggest.py` (~80 lines) — `suggest-suppressions` command (split from `cli.py`)
- `diff_platform.py` (~1,460 lines) — all platform-specific detection
- `diff_platform_templates.py` (~180 lines) — template inner-type detectors (split from `diff_platform.py`)
- `dumper.py` (~1,150 lines) — binary metadata extraction
- `dumper_castxml.py` (~610 lines) — castxml XML parser (split from `dumper.py`)
- `compat/cli.py` (~1,430 lines) — ABICC compat CLI
- `compat/_errors.py` (~130 lines) — ABICC compat error classification helpers (split from `compat/cli.py`)

The 2000-line hard cap is enforced for every source file (no allowlist). Files above 1500 lines emit a WARN as a refactor signal. When editing, read the specific section you need rather than the whole file.

### Adding a new top-level command

Pick the right home:

- **Small command (one function, no significant helpers)** — add to `cli.py` directly with `@main.command(...)`.
- **Larger command or command group** — add as a sibling `abicheck/cli_<name>.py` module:
  1. Top of module: `from .cli import main` (and any shared `_helpers`).
  2. Decorate with `@main.command("foo")` or `@main.group("foo")` as usual.
  3. At the bottom of `cli.py`, add `cli_<name>` to the side-effect `from . import (...)` block — that runs after `main` and helpers are defined, registering the new command.
  4. If the new module uses `@click` decorators, add `abicheck.cli_<name>` to the `disallow_untyped_decorators = false` override in `pyproject.toml` (alongside the existing entries).
  5. If `scripts/check_ai_readiness.py` flags a cycle, add `frozenset({"cli", "cli_<name>"})` to `IMPORT_CYCLE_ALLOWLIST` — this registration pattern is by design.

## Exit codes

- `compare` command (legacy, without `--severity-*` flags): 0 = compatible, 2 = source break, 4 = ABI break
- `compare` command (severity-aware, with any `--severity-*` flag): 0 = no error-level findings, 1 = error in addition/quality only, 2 = error in potential_breaking, 4 = error in abi_breaking
- `compat` command: 0 = compatible, 1 = BREAKING, 2 = API_BREAK (source-level), 3-11 = errors (see `compat/cli.py:_classify_compat_error_exit_code`)

## What NOT to do

- Don't modify `examples/` test cases without understanding the ground truth they encode
- Don't add dependencies without strong justification (this is a lightweight tool)
- Don't skip test markers — if a test needs `castxml`, mark it `@pytest.mark.integration`
- Don't "fix" the mypy errors listed above by adding `# type: ignore` broadly
- Don't modify binary test fixtures without regenerating expected outputs
- Don't change public API signatures without checking for breaking changes
- Don't add platform-specific code without considering cross-platform compatibility
