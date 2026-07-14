# ADR-019: Testing Strategy and Parity Validation

**Date:** 2026-03-18
**Status:** Accepted — implemented. Amendment (2026-07-14): the specific
numbers in this ADR (change-type count, coverage threshold, parity test
count, example-case count, CI matrix details) were a point-in-time snapshot
and have drifted. The core principles (four-tier architecture, conditional
parity gating, examples as dual-purpose tests) still hold; for current
numbers see `CLAUDE.md` (coverage-floor policy), `docs/development/testing.md`
(maintained testing strategy page, explicitly kept up to date), and
`.github/workflows/ci.yml` (CI topology) rather than this document.
**Decision maker:** Nikolay Petrov

---

## Context

abicheck's correctness depends on accurately classifying a large and
growing number of change types (see ADR-011 and `len(list(ChangeKind))` in
`checker_policy.py` for the current count) across three binary formats.
False negatives (missed breaks) can cause
production outages. False positives (spurious breaks) erode user trust and
block CI pipelines.

Two reference tools exist (ABICC, libabigail) against which results can be
validated. However, both are unmaintained and have known limitations.
abicheck intentionally diverges from their classifications in some cases
(see ADR-011).

### Requirements

- Fast feedback loop for contributors (unit tests without external tools)
- Comprehensive coverage of real-world ABI break scenarios
- Parity validation against reference tools (ABICC, libabigail)
- Cross-platform CI (Linux, Windows, macOS)
- Reasonable CI runtime (not blocking PRs for 30+ minutes)

---

## Decision

### Four-tier test architecture

| Tier | Marker | Dependencies | Runtime | Trigger |
|------|--------|-------------|---------|---------|
| **1: Lint + Types** | — | ruff, mypy | ~30s | Every push/PR |
| **2: Unit tests** | default | Python only | ~60s | Every push/PR |
| **3: Integration** | `@pytest.mark.integration` | castxml, gcc, cmake | ~5min | Every push/PR |
| **4: Parity** | `@pytest.mark.libabigail` / `@pytest.mark.abicc` | abidiff / ABICC + gcc | ~10min | Conditional |

### Tier 1: Lint and types

- **ruff** — linting and formatting (rules: E, F, W, I, UP; ignore E501)
- **mypy** — strict mode with targeted overrides for untyped external
  libraries (pyelftools, Click, FastMCP)
- **mkdocs build --strict** — documentation build validation
- Single matrix entry on ubuntu-latest (exact Python version drifts with
  each toolchain bump — see `.github/workflows/ci.yml` `lint-and-types` job
  for the current pin)

### Tier 2: Unit tests

- Test all core logic without external tools: checker, policy, model,
  serialization, reporter, suppression, CLI parsing
- A coverage floor is enforced on one canonical Linux/Python lane via
  `--cov-fail-under=<N>`; the exact threshold moves over time and is
  documented in `CLAUDE.md` ("Line-coverage floor" section) and
  `docs/development/testing.md`, not here
- Matrix: ubuntu (multiple Python versions), windows (one pinned version),
  macos (one pinned version) — see `.github/workflows/ci.yml` `unit-tests`
  job for the current matrix
- Codecov upload (canonical Linux/Python lane only)

The coverage floor applies only to the canonical Linux/Python lane, not the
full matrix aggregate — the other Linux Pythons run the same suite without
coverage instrumentation (they would only re-check the identical floor), and
macOS/Windows skip the Linux-only ELF/DWARF tests, which structurally lowers
their coverage, so those lanes run without the fail-under gate. Platform-
specific code (elf_metadata, pe_metadata, macho_metadata) is structurally
harder to cover because each module only runs on its native platform in CI.
See `CLAUDE.md` for the durable statement of this policy — it is
intentionally not duplicated with a hardcoded number here.

### Tier 3: Integration tests

- Full pipeline tests: castxml → AST parsing → DWARF extraction → comparison
- System dependencies: castxml, gcc/g++, cmake
- Matrix: ubuntu, windows, macOS
- 30-minute timeout (some tests compile C/C++ examples)
- Separate coverage report (`coverage-integration.xml`)

### Tier 4: Parity tests

- **ABICC parity** (`test_abicc_parity.py` and sibling `test_abicc_*.py` /
  `test_*_parity.py` modules): compile example cases, run both abicheck and
  ABICC, compare verdicts
- **libabigail parity** (`test_abidiff_parity.py` and sibling
  `test_abidiff_*.py` modules): compile example cases, run both abicheck and
  abidiff, compare verdicts
- The parity suite has grown past its original size and spans more files
  than the two named above (both `abicc` and `libabigail` markers now
  collect well over the tens-of-tests scale this ADR originally described)
  — run `pytest tests/ -m "libabigail or abicc" --collect-only -q` for the
  current count rather than trusting a number in this document

### Conditional gating for parity tests

Parity tests are expensive (require ABICC/libabigail installation + full
compilation of example cases). They run conditionally:

```yaml
heavy-parity-gate:
  outputs:
    run-heavy: true/false
  steps:
    - if: github.event_name != 'pull_request' → run-heavy=true
    - if: PR with changes in abicheck/**, tests/**, examples/**,
           .github/workflows/** → run-heavy=true
    - otherwise → run-heavy=false
```

This means:
- **Push to main**: Always runs parity tests
- **PR with relevant changes**: Runs parity tests
- **PR with docs-only or unrelated changes**: Skips parity tests

### Example cases as tests

Real-world ABI/API scenario cases in `examples/` (181 as of this amendment;
see `examples/ground_truth.json` for the current count — it is the
generated source of truth the AI-readiness gate checks docs against) serve
dual purpose:

1. **Documentation**: Each case has `README.md` with scenario description,
   expected break type, and detection evidence
2. **Regression tests**: `tests/validate_examples.py` compiles single-library
   examples and verifies abicheck detects the correct changes; bundle cases are
   exercised by `tests/test_bundle.py`

Example case structure:
```text
examples/case01_function_removed/
├── v1/
│   ├── lib.h
│   └── lib.c
├── v2/
│   ├── lib.h
│   └── lib.c
├── consumer.c
├── CMakeLists.txt
└── README.md
```

### Packaging validation

Separate CI job validates distribution artifacts:
- Build sdist + wheel (`python -m build`)
- Validate metadata with `twine check`
- Smoke-test wheel install
- Matrix: ubuntu + windows

### Test organization

```text
tests/
├── test_checker.py          # Core diff engine
├── test_policy.py           # Policy profiles and verdict computation
├── test_suppression.py      # Suppression rules and filtering
├── test_serialization.py    # Snapshot serialization/deserialization
├── test_reporter.py         # Markdown/JSON output
├── test_sarif.py            # SARIF output
├── test_html_report.py      # HTML output
├── test_cli.py              # CLI parsing and integration
├── test_compat_cli.py       # ABICC compat layer
├── test_elf_metadata.py     # ELF parsing
├── test_dwarf_*.py          # DWARF metadata
├── test_pe_metadata.py      # PE parsing
├── test_macho_metadata.py   # Mach-O parsing
├── test_abi_examples.py     # Example case validation
├── test_abicc_parity.py     # ABICC parity
├── test_abidiff_parity.py   # libabigail parity
├── test_xml_parity.py       # XML report parity
└── validate_examples.py     # Example case validation script
```

---

## Consequences

### Positive

- Fast unit test feedback (~60s) doesn't block contributors
- Parity tests catch regressions against reference tools
- Conditional gating keeps PR CI fast for non-code changes
- Example cases serve as both documentation and regression tests
- Cross-platform matrix catches platform-specific bugs

### Negative

- The single-lane coverage threshold is somewhat arbitrary — some
  platform-specific code paths are inherently hard to cover on all CI
  platforms (see `CLAUDE.md` for the current floor and its scoping rationale)
- Parity tests depend on unmaintained tools (ABICC, libabigail) that may
  have their own bugs. If these tools become unavailable (repos deleted,
  dependencies break), parity tests will be skipped with a warning —
  abicheck's own Tier 2 test suite provides the primary safety net
- Conditional gating means parity regressions can land if changes don't
  touch gated paths
- A large and growing example-case set requires C/C++ compilation, adding
  CI complexity (see `examples/ground_truth.json` for the current count)

---

## References

- `.github/workflows/ci.yml` — CI pipeline definition (current source of
  truth for job names, matrix, and topology)
- `tests/` — Test directory (large unit, integration, parity, and workflow suite)
- `examples/` — real-world ABI/API scenario cases; `ground_truth.json` is
  the generated source of truth for the current count
- `pyproject.toml` — pytest markers, coverage configuration
- `CLAUDE.md` (root) — current coverage-floor policy and CI-lane scoping
- `docs/development/testing.md` — maintained testing-strategy page, kept up
  to date as CI gates and test layers change (see its maintainer note)
