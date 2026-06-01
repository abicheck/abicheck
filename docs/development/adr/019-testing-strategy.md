# ADR-019: Testing Strategy and Parity Validation

**Date:** 2026-03-18
**Status:** Accepted
**Decision maker:** Nikolay Petrov

---

## Context

abicheck's correctness depends on accurately classifying 85+ change types
across three binary formats. False negatives (missed breaks) can cause
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
- Single matrix entry: Python 3.13 on ubuntu-latest

### Tier 2: Unit tests

- Test all core logic without external tools: checker, policy, model,
  serialization, reporter, suppression, CLI parsing
- **Coverage threshold: 80%** enforced via `--cov-fail-under=80`
- Matrix: ubuntu (3.12, 3.13, 3.14), windows (3.13), macos (3.13)
- Codecov upload (ubuntu + 3.13 only)

The 80% threshold applies to the full test suite aggregate. Core logic
(checker, policy, model, suppression) targets 90%+ coverage. Platform-specific
code (elf_metadata, pe_metadata, macho_metadata) is structurally harder to
cover because each module only runs on its native platform in CI. The 80%
floor catches regressions without forcing artificial test-writing for
unreachable platform branches.

### Tier 3: Integration tests

- Full pipeline tests: castxml → AST parsing → DWARF extraction → comparison
- System dependencies: castxml, gcc/g++, cmake
- Matrix: ubuntu, windows, macOS
- 30-minute timeout (some tests compile C/C++ examples)
- Separate coverage report (`coverage-integration.xml`)

### Tier 4: Parity tests

- **ABICC parity** (`test_abicc_parity.py`, `test_abicc_full_parity.py`,
  `test_xml_parity.py`):
  Compile example cases, run both abicheck and ABICC, compare verdicts
- **libabigail parity** (`test_abidiff_parity.py`):
  Compile example cases, run both abicheck and abidiff, compare verdicts
- ~54 parity test functions across suites

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

121 real-world ABI/API scenario cases in `examples/` serve dual purpose:

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

- 80% coverage threshold is arbitrary — some platform-specific code paths
  are inherently hard to cover on all CI platforms
- Parity tests depend on unmaintained tools (ABICC, libabigail) that may
  have their own bugs. If these tools become unavailable (repos deleted,
  dependencies break), parity tests will be skipped with a warning —
  abicheck's own Tier 2 test suite provides the primary safety net
- Conditional gating means parity regressions can land if changes don't
  touch gated paths
- 121 example cases require C/C++ compilation, adding CI complexity

---

## References

- `.github/workflows/ci.yml` — CI pipeline definition
- `tests/` — Test directory (120+ files, 2500+ tests)
- `examples/` — 121 real-world ABI/API scenario cases
- `pyproject.toml` — pytest markers, coverage configuration
