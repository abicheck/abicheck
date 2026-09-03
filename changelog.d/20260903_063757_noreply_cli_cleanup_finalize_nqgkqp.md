### Added

- **Typed-API severity/exit-code-scheme parity (ADR-064, PR G2).**
  `CompareRequest`/`ScanRequest` gained `severity_preset`/`exit_code_scheme`
  fields, resolved through `abicheck.policy.release_gate_options.GateOptions`
  — the same object the directory/package release fan-out already resolves
  its own gate configuration from — so a typed `compare`/`scan` caller now
  reaches the identical severity-aware exit-code scheme `--severity-preset`/
  `--exit-code-scheme` already give the native CLI, instead of always
  classifying through the legacy verdict-based exit code. `CompareResult`
  gained `exit_decision`, the canonical `ExitDecision` for the comparison
  (same resolver the `compare` CLI's own report `exit` block uses).
