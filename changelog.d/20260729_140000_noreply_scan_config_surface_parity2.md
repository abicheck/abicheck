### Changed

- **`scan --against` gains the rest of `compare`'s policy/suppression config
  surface** — `--strict-suppressions`, `--public-symbol`/
  `--public-symbols-list` (force-public overlay), `--pattern-verdicts`, and
  `--env-matrix` (ADR-049 Phase 5 §6.4), threaded through `ScanRequest`
  (`abicheck.service_scan`) for the Python API too. Combined with the earlier
  `--policy`/`--policy-file`/`--suppress`/`--scope-public-headers` slice,
  `scan --against` now shares essentially all of `compare`'s
  verdict-classification config surface instead of silently hardcoding it.
  New field-for-field parity tests (`tests/test_scan_compare_parity.py`)
  assert `compare` and `scan --against` agree end to end (same exit code)
  on identical suppression/scope inputs over the same JSON snapshots.
