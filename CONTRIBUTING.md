# Contributing to abicheck

Thank you for your interest in contributing!

## Requirements

- Python >= 3.10
- `git`
- Linux for full test suite: `castxml` + `g++` or `clang++` (ELF/DWARF/header tests)
- Windows/macOS: unit tests and PE/Mach-O tests run without extra system dependencies

## Setup

### Option A: pixi (recommended)

[pixi](https://pixi.sh) manages both the Python dev tools *and* the
conda-forge system tools (`castxml`, a C/C++ compiler, `libabigail`,
`abi-compliance-checker`) from the single `[tool.pixi.*]` section in
`pyproject.toml` — no separate `conda create`/`apt install` step needed.

```bash
curl -fsSL https://pixi.sh/install.sh | sh   # or: conda install -c conda-forge pixi

git clone https://github.com/abicheck/abicheck.git
cd abicheck
pixi install          # base dev environment (lint/type/unit-test tools)
pixi run test          # fast unit-test lane
pixi run check           # lint + format-check + typecheck + test
```

`abicheck` itself is installed editable (`pip install -e .` equivalent) into
every pixi environment, so `pixi run abicheck --help` works too. Additional
environments layer on the system tools for the heavier marker lanes:

| Environment | `pixi run -e <env> <task>` | Adds |
|-------------|------------------------------|------|
| `default` | `test`, `test-cov`, `lint`, `fmt`, `fmt-check`, `typecheck`, `check` | (base — no system tools) |
| `integration` | `test-integration` | `castxml`, C/C++ compiler, `cmake` (linux-64/osx-64/osx-arm64 only — no MSVC via conda-forge; see `integration` marker below) |
| `parity` | `test-libabigail`, `test-abicc` | `libabigail` (`abidiff`) + `abi-compliance-checker` (linux-64 only, conda-forge doesn't ship these elsewhere) |
| `docs` | `docs-build`, `docs-serve` | `mkdocs` + plugins |

Note: the `integration`/`parity` environments pull `castxml`/`libabigail`/
`abi-compliance-checker` from conda-forge at whatever version is current,
which can drift from the pinned versions CI installs via `apt`/`brew`/
`choco`. A handful of parity tests are sensitive to exact tool versions
(struct-layout/calling-convention edge cases); a local pixi-driven
`integration`/`parity` failure that doesn't reproduce in CI is usually that,
not a real regression — check the CI logs for the authoritative verdict.

### Option B: conda-forge

```bash
# Create a development environment with all dependencies
conda create -n abicheck-dev python=3.10 castxml -c conda-forge
conda activate abicheck-dev

git clone https://github.com/abicheck/abicheck.git
cd abicheck
pip install -e ".[dev]"
```

### Option C: pip + system castxml

```bash
# Install castxml separately (Ubuntu/Debian)
sudo apt install castxml g++

git clone https://github.com/abicheck/abicheck.git
cd abicheck
pip install -e ".[dev]"
```

## Testing

abicheck uses a layered testing strategy with `pytest`.

### Quick tests (default CI gate)

Fast unit and component tests — no external tools required:

```bash
pytest tests/ -v --tb=short \
  -m "not integration and not libabigail and not abicc and not slow and not golden" \
  --cov=abicheck --cov-report=term-missing
```

### Integration tests

Requires `castxml` and `gcc`/`g++`:

```bash
pytest tests/ -v -m "integration"
```

### Full suite (all external tools)

Requires `castxml`, `abidiff`, and `abi-compliance-checker`:

```bash
pytest tests/ --cov=abicheck --cov-report=term-missing
```

### Test markers

| Marker | Requirements | What it covers |
|--------|-------------|----------------|
| (default) | Python only | Core logic, report serialization, suppression rules, CLI |
| `integration` | castxml, gcc/g++ | Real toolchain interactions, ELF/DWARF parsing |
| `libabigail` | abidiff, gcc/g++ | libabigail parity tests |
| `abicc` | abi-compliance-checker, gcc/g++ | ABICC compatibility parity tests |
| `msvc` | MSVC `cl.exe` (Windows) | MSVC + PDB end-to-end lane |
| `slow` | varies | Performance and large-input tests (excluded from fast CI gate) |
| `golden` | golden snapshot files | Output-format snapshot tests (skip unless changing output format) |

### Example validation

Run the example cases against ground truth:

```bash
pytest tests/ -v -k "example" --tb=short
```

Or use the benchmark script (scans the full `examples/` catalog):

```bash
python3 scripts/benchmark_comparison.py --skip-abicc
```

## Code style

```bash
ruff check abicheck/ tests/
mypy abicheck/
```

Both must pass before submitting a PR. CI enforces both.

## PR workflow

1. Branch: `git checkout -b feat/<name>` or `fix/<name>`
2. Make changes, add tests
3. `ruff check` + `mypy` + `pytest` all green locally
4. Push and open PR — CodeRabbit will review automatically
5. Address all review comments before merge
6. CI must be fully green (all checks)

## Commit style

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add --policy-file support
fix: correct CFA register extraction for epilogue frames
docs: update README with v0.1 requirements
test: add coverage for PolicyFile.compute_verdict
```

## Adding a new ChangeKind

1. Add the kind to `ChangeKind` enum in `abicheck/checker_policy.py`
2. Place it in exactly one of `BREAKING_KINDS`, `API_BREAK_KINDS`, `COMPATIBLE_KINDS`, or `RISK_KINDS` (an import-time assertion enforces completeness)
3. Implement detection in the appropriate diff module:
   - `abicheck/diff_symbols.py` — function/variable/parameter changes
   - `abicheck/diff_types.py` — struct/enum/union/typedef/field changes
   - `abicheck/diff_platform.py` — ELF/PE/Mach-O/DWARF-specific changes
   - `abicheck/detectors.py` — individual detection rules
4. Add a unit test in `tests/`
5. Regenerate the detector-spec matrix: `python scripts/gen_detector_spec.py`
6. Mention the kind in `docs/` and, where practical, add an `examples/caseNN_*`
   fixture — the AI-readiness gate checks that every `ChangeKind` is classified,
   produced by a detector, and mentioned in the docs

CI (`scripts/check_ai_readiness.py`) enforces steps 2, 5, and 6; run it locally
before pushing.

## Questions

Open an [issue](https://github.com/abicheck/abicheck/issues) or discussion.
