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
  --cov=abicheck --cov-report=term-missing --cov-report=xml --cov-fail-under=80
```

The 80% line+branch floor is enforced **only on the Linux unit-test lane** — macOS
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

#### Generated ABI edit corpus coverage

The generated edit corpus (`tests/_edit_corpus_*.py`: semantic edit operators
× seeds × configurations, run in PR, nightly and weekly tiers) reports two
separate coverage measures. Both are computed by
`tests/_edit_corpus_coverage.py`; every corpus cell writes a record when
`ABICHECK_EDIT_CORPUS_COVERAGE_DIR` is set, and
`python -m tests._edit_corpus_coverage report DIR...` aggregates records into
a JSON report and a step summary (`integration.yml` for the PR tier,
`edit-corpus-tiers.yml` per shard and for the merged tier).

**ChangeKind coverage.** The denominator is the ChangeKinds one Linux ELF
shared library with DWARF and public headers can show. `SCOPE_BUCKETS` is that
partition as data: named buckets (by prefix or name) exclude PE/COFF, Apple,
Python/NumPy/wheel packaging, SYCL, kernel-module ABI and multi-library bundle
kinds, and include the `source`-taxonomy kinds a header alone shows (concepts,
public typedef targets, header-only functions); module buckets take the rest of
each taxonomy module (`symbols`/`types`/`platform` in, `build`/`source` and the
source graph out — L3–L5 evidence). Macro, `constexpr` and default-argument
values stay out: abicheck reads them only from L4 sources, and the corpus gives
at most headers. Every kind is in exactly one bucket
(`test_every_kind_is_in_exactly_one_scope_bucket`), and every report lists the
buckets with their counts and reasons.

- *Declared*: an operator names the kind in an expectation, under some rule.
  Computed from the operators alone.
- *Verified* (per tier): a cell expected the kind, abicheck produced it, and
  the cell reconciled to a passing class. A strict-xfail (known) cell, a
  skipped cell and an unobservable cell verify nothing. A kind abicheck
  produced that the cell did not expect is listed separately, per tier with
  its cell count, as *produced but not expected* — a possible false positive
  or a missing expectation, never coverage.

`tests/edit_corpus_coverage_ledger.json` is the ratchet:
`tests/test_edit_corpus_coverage.py` fails when a declared kind stops being
declared, when a new declared kind is not written down
(`python -m tests._edit_corpus_coverage ledger --write`), or when a floor names
a kind no cell can verify. `verified_floor[tier]` holds the kinds a tier
verified when it was raised (`report DIR --raise-floor TIER`); a run that
verifies less fails `--check-floor TIER`. A single shard is held only to the
floor kinds its own cells expected; the merged run (`--complete`) to the whole
floor.

**Language-space coverage.** `tests/_edit_corpus_space.py` models the space
the corpus samples: construct (from the operators' roles and language
features) × language × standard × config feature (`baseline`, `-O2`, hidden
visibility, `-m32`, `-fshort-enums`, `-fpack-struct`,
`_GLIBCXX_USE_CXX11_ABI=0`, `-fabi-version`, different consumer flags) × build
toolchain × consumer toolchain × evidence level. A (build, consumer) pair that
cannot work is made infeasible by a `PAIR_RULES` entry. `infeasible()`
names why a point cannot be a meaningful cell (concepts before C++20, a C++
construct in C, `-fabi-version` on clang, `std::string` under
`-fpack-struct`, a family that does not target Linux ELF); the generator
refuses the same combinations through the same module. The report gives
covered against feasible points overall and per axis value, and the top
uncovered feasible points ranked by how thinly their axis values are covered.

What these numbers do **not** mean:

- A verified ChangeKind is not proof that abicheck reports that kind for every
  edit that should produce it; one cell under one configuration verified it.
- A covered space point means a cell *ran* there, not that abicheck was right
  there (mismatches are the corpus test's own result) and not that every edit
  of that construct ran: the space has no edit axis.
- The space holds only the corpus's own axes. Architectures beyond `-m32`,
  other operating systems, link order and real-world library shapes are not in
  the denominator, so 100% would still not be "all of C/C++".
- Declared coverage is a property of the operator table; only verified
  coverage says abicheck did anything.

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
     `compare` scenarios.
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
