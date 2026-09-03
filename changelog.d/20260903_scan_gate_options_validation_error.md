### Fixed

- **`run_scan` raised a bare `ValueError`/`PolicyError` for an invalid `ScanRequest.severity_preset`/`exit_code_scheme`**, instead of the `ValidationError` every other malformed `ScanRequest` field raises. A Tier-2 caller guarding `run_scan` with `except ValidationError` would miss these two cases and see the raw resolver exception instead. Fixed by translating both at the `resolve_scan_gate_options` call site, mirroring the existing `_resolve_scan_contract_config` translation.
