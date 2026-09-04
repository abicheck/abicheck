### Fixed

- **`scan --format json`'s abort report used the wrong JSON shape and would
  crash `aggregate`** (PR review finding on the abort-report fix above).
  `cli_scan._emit_scan_abort_report` reused
  `scan_abort_result_fields(...)["report"]` directly — the typed
  `service_scan.ScanResult` API's own `report` nesting
  (`{scan_schema_version, exit}`, no top-level `verdict`/`exit_code`) —
  which is a different envelope from the native CLI's real
  `ScanOutcome.to_dict()` contract. Saving a `--format json` abort report
  and feeding it to `abicheck aggregate` raised `_MalformedGate`, since
  `GateInfo.from_scan_report` requires a top-level `exit_code`. The abort
  payload now matches the real envelope: top-level
  `scan_schema_version`/`verdict`/`exit_code`, with the exit decision
  nested under `diff.exit` (the same place `NOT_COMPARABLE` and a baseline
  compare already publish theirs), so it reads back through `aggregate`
  correctly instead of raising.
