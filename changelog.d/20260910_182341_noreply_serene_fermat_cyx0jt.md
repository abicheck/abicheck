### Added

- **`compare --no-baseline` audit-gate exit axis** — a new orthogonal exit
  code (`3`), opt-in via `--severity-preset` (previously a usage error under
  `--no-baseline`), lets a CI job gate on a candidate-side audit finding
  classified `BREAKING`/`API_BREAK` without an audit ever emitting the
  compatibility family's `2`/`4` codes (ADR-068 D2). Reproduces legacy
  `scan`'s audit-mode gating exactly, closing the last-named blocker on
  `scan`'s eventual retirement. See `policy/audit_gate_exit.py` and
  ADR-068's 2026-09-10 amendment.
