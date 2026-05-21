# CLAUDE.md — `abicheck/compat/` (ABICC drop-in layer)

ABICC = `abi-compliance-checker`. This package mimics its CLI and output
shapes so abicheck can substitute into existing ABICC-based pipelines.

## Files

- `cli.py` (large — ~1500 lines, allowed) — Click commands that mirror
  ABICC: `compat check`, `compat dump`, top-level `abicheck compat <args>`
  auto-forwarding.
- `abicc_dump_import.py` — reads ABICC XML dumps and translates them into
  `AbiSnapshot` objects.
- `descriptor.py` — parses ABICC descriptor XML (`-d1 <file>`).
- `xml_report.py` — emits ABICC-compatible XML reports.
- `__init__.py` — public surface for compat consumers.

## Exit-code contract

`compat check` exit codes must stay aligned with the root `CLAUDE.md`
"Exit codes" table:

| Code | Meaning |
|------|---------|
| 0 | compatible |
| 1 | BREAKING |
| 2 | API_BREAK (source-level) |
| 3–11 | errors (see `_classify_compat_error_exit_code` in `cli.py`) |

Changing these requires a CHANGELOG note and downstream coordination —
many CI pipelines rely on the numeric meaning.

## Parity tests

- `tests/test_abicc_parity.py`
- `tests/test_xml_parity.py`
- `tests/test_abicc_compat_flags.py`
- `tests/test_abicc_dump_*.py`

Run with `-m abicc` (CI gates this on `heavy-parity-gate`).
