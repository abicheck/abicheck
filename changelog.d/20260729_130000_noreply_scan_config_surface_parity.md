### Changed

- **`scan --against` now accepts `--policy`/`--policy-file`/`--suppress`/
  `--scope-public-headers`** — the same config surface `compare` already
  has (ADR-049 Phase 5 §6.4). Previously a baseline comparison was always
  classified with a hardcoded `policy="strict_abi"`, no suppression, and
  `scope_to_public_surface=True`; a `scan --against` invocation with none
  of the new flags behaves exactly as before. `ScanRequest`
  (`abicheck.service_scan`) gained matching `suppression`/`policy`/
  `policy_file`/`scope_to_public_surface` fields for the Python API,
  threaded through `run_scan_core`/`_run_baseline_compare` alongside the
  existing `_verdict_exit_code` helper `compare` already used, replacing
  `scan`'s own hand-rolled duplicate of the same verdict→exit-code mapping.
