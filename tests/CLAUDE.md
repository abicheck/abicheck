# CLAUDE.md — `tests/`

~5400 unit tests across ~180 files. Most are fast and stdlib-only.

## Test markers

| Marker | What it needs | When to run |
|--------|---------------|-------------|
| *(default)* | Python only | always — `pytest -m "not integration and not libabigail and not abicc and not slow and not golden"` |
| `integration` | castxml + gcc/g++ | DWARF/ELF parsing changes |
| `libabigail` | abidiff + gcc/g++ | parity vs libabigail |
| `abicc` | `abi-compliance-checker` + gcc/g++ | parity vs ABICC |
| `msvc` | MSVC `cl.exe` (Windows) | MSVC+PDB end-to-end (`windows-msvc` CI lane) |
| `slow` | varies | hypothesis / property-based / perf — covered in CI on Linux/3.13 |
| `golden` | golden files in `tests/golden/` | output-format snapshots |

The default fast command excludes all external-tool markers. Use it. It
finishes in ~45 seconds.

## Conventions

- Use `assert` freely — no need for unittest-style methods.
- Prefer `pytest.mark.parametrize` over manual loops.
- Fixtures live in `conftest.py` and `tests/fixtures/`.
- Golden outputs live in `tests/golden/`; if you must regenerate, do so
  in a deliberate commit and document why.
- Mark tests that shell out (`gcc`, `castxml`, etc.) with the matching
  marker so default runs stay fast.

## Helpers

- `check_validate_results.py`, `summarize_validate_results.py` — used by
  `test_abi_examples.py` to validate example case ground truth.
- `conftest.py` — shared fixtures, including temp-dir helpers and
  binary-skip markers.

## What NOT to do

- Don't change the marker scheme — CI gates depend on it.
- Don't read or regenerate `tests/golden/*` unless the output format
  intentionally changed.
- Don't add network-dependent tests.
