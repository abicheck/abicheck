# Testing strategy and coverage

> Maintainer note: this strategy doc is intentionally long-lived and should be updated when CI gates, test layers, or extension priorities change.

This document explains how `abicheck` is tested, why the current test layout exists,
and where coverage expansion should go next.

## Testing strategy

`abicheck` uses a **layered testing strategy**:

1. **Fast deterministic gate (unit/component)**
   - Marker selection: `not integration and not libabigail and not abicc and not slow`
   - Purpose: protect core logic and user-facing behavior on every PR with low runtime cost.
   - Includes checker logic, report serialization, suppression rules, CLI command behavior,
     dumper orchestration via mocks, and DWARF helper edge cases.

2. **Integration confidence layer**
   - Marker selection: `integration`
   - Purpose: validate real toolchain interactions (castxml, gcc/g++, ELF/DWARF parsing)
     against realistic examples and negative scenarios.
   - Includes end-to-end cases under `examples/`, ELF integration parsing, and failure-path
     tests (bad ELF, broken headers, missing symbols).

3. **External parity layer**
   - Marker selection: `libabigail` and `abicc`
   - Purpose: compare selected behavior against `abidiff` and `abi-compliance-checker`
     to reduce semantic drift and catch interpretation mismatches in ABI diagnostics.

This split keeps PR feedback fast while retaining deeper compatibility checks in dedicated jobs.

## Coverage model and CI gate

Coverage is measured for the `abicheck` package with branch coverage enabled.
The fast CI gate enforces a threshold and publishes `coverage.xml` for inspection.

Current CI command:

```bash
pytest tests/ -v --tb=short \
  -m "not integration and not libabigail and not abicc and not slow" \
  -n auto --dist worksteal \
  --cov=abicheck --cov-report=term-missing --cov-report=xml --cov-fail-under=95
```

The 95% line+branch floor is enforced **only on the Linux unit-test lane** — macOS
and Windows skip the Linux-only ELF/DWARF tests, which structurally lowers their
coverage, so those lanes run without the fail-under gate.

### Test markers

| Marker | Needs | When to run |
|--------|-------|-------------|
| *(default)* | Python only | Always — fast, no external deps |
| `integration` | castxml + gcc/g++ | When modifying DWARF/ELF parsing |
| `libabigail` | abidiff + gcc/g++ | Parity testing |
| `abicc` | abi-compliance-checker + gcc/g++ | Parity testing |
| `msvc` | MSVC `cl.exe` (Windows) | MSVC+PDB end-to-end lane |
| `slow` | varies | Hypothesis/perf benchmarks |
| `golden` | golden files | Snapshot tests — run when changing output format |

### Why this gate shape

- Keeps gating stable and deterministic (no dependency on optional system tools).
- Provides a practical signal for regressions in high-change code paths.
- Leaves integration/parity variance to dedicated jobs where failures are easier to diagnose.

## Test components by responsibility

### 1) Core ABI diff semantics
- Files like `tests/test_checker.py`, `tests/test_negative.py`, `tests/test_detector_contracts.py`.
- Validates change classification and verdict priority (`BREAKING`, `COMPATIBLE`, `COMPATIBLE_WITH_RISK`, `API_BREAK`, etc.).

### 2) Data model/reporting/serialization
- Files like `tests/test_reporter.py`, `tests/test_sarif.py`, `tests/test_serialization.py`.
- Ensures machine and human outputs remain stable and parseable.

### 3) CLI behavior and UX contracts
- `tests/test_cli_phase1.py`, `tests/test_cli_unit.py`, `tests/test_cli_new_features.py`.
- Covers command success/failure flows, warning messages, output writing, and exit codes.

### 4) Dumper orchestration contracts
- `tests/test_dumper_phase1.py`.
- Covers no-header fallback behavior, castxml/parser wiring, and error propagation.

### 5) DWARF helper and parser edge cases
- dedicated DWARF regression tests,
  `tests/test_phase3_dwarf_helpers.py`.
- Covers nuanced DWARF forms (reference resolution, location decoding, packing/alignment,
  calling conventions, helper fallback behavior).

### 6) Real-world integration and parity
- `tests/test_abi_examples.py`, `tests/test_elf_parse_integration.py`,
  `tests/test_integration_phase2_negative.py`, `tests/test_abidiff_parity.py`,
  `tests/test_abicc_parity.py`, `tests/test_abicc_full_parity.py`,
  `tests/test_sprint7_full_parity.py`, `tests/test_sprint10_abicc_parity.py`.
- Exercises realistic binaries, toolchain outputs, and compatibility expectations.

### 7) Architecture conformance
- `tests/test_architecture_conformance.py`, `tests/test_changekind_completeness.py`,
  `tests/test_changekind_coverage.py`.
- Validates structural invariants: module boundaries, ChangeKind classification completeness,
  and policy set consistency.

## Extension roadmap (next improvements)

The main strategy now is **depth over breadth**: extend tests where defects are most
likely and costly.

1. **DWARF fixture corpus (high value)**
   - Add curated binary fixtures (or generated test inputs) for tricky DWARF producer/output
     variants (compiler versions, attribute forms, typedef nesting patterns).
   - Goal: reduce parser regressions when toolchains differ.

2. **Dumper internals: cache/process paths**
   - Expand tests around castxml cache key/path behavior and subprocess failure modes
     (timeouts, malformed XML, partial outputs).
   - Goal: increase resilience of snapshot generation in CI and local automation.

3. **CLI integration contracts**
   - Add more command-level tests for multi-output formats and failure diagnostics in
     `compare`/`compat` scenarios.
   - Goal: keep user-facing behavior stable for CI consumers.

4. **Integration matrix enrichment**
   - Add targeted C/C++ examples for edge ABI patterns not yet represented.
   - Goal: improve confidence that algorithmic changes match real binary behavior.

5. **Threshold ratchet policy**
   - Raise `--cov-fail-under` only when sustained improvements are merged and stable.
   - Keep ratchets incremental to avoid churn from unrelated PRs.

## Practical contributor workflow

1. Pick one under-tested surface (DWARF helper, dumper path, CLI branch).
2. Add at least one happy-path and one failure-path test.
3. Run lint + fast gate locally.
4. If coverage meaningfully improves and remains stable, propose a small threshold bump.

This keeps quality improvements continuous without making routine development fragile.
