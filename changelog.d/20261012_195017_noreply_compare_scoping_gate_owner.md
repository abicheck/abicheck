### Changed

- **The `--used-by` / `--required-symbol(s)` scoped gate is owned by the workflow layer** — the scoping passes and their scoped exit code moved from the CLI helper module into `abicheck/workflows/consumer_scope_gate.py` (ADR-061). The CLI still decides which pass applies and maps consumer-read errors to exit 1. No output, exit code, or finding changes.
