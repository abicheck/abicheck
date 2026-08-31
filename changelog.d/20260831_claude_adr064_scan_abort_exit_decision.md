### Added

- **`scan`'s programmatic API now explains a budget-overflow/evidence-contract
  abort, not just its bare exit code** (ADR-064 stage 1b). `service_scan.
  run_scan`/`run_scan_set`'s member scans used to return a `ScanResult` with
  an empty `report` when `run_scan_core` raised `_BudgetOverflow`/
  `_EvidenceContractError` — the same explanatory `exit` block
  `scan_engine.py`'s own `NOT_COMPARABLE` outcome already carried was simply
  never computed for these two abort exceptions. `ScanResult.report["exit"]`
  now carries a real `ExitDecision` for both
  (`abicheck.policy.exit_decision_precedence.scan_abort_result_fields`),
  giving a report reader the same `code`/`reasons`/per-axis contributions
  breakdown for these two outcomes as every other scan/compare exit path.
  No exit code or verdict string changes for any existing caller.

### Notes

- Scoped to the programmatic `ScanResult` API
  (`abicheck/service_scan.py`'s `run_scan`/`_run_scan_one_member`); the
  native `scan` CLI's own abort handling (`cli_scan.py`, which calls
  `run_scan_core` directly and never builds a report at these two points)
  is unchanged — per ADR-064's own status notes, giving the CLI a persisted
  report at these abort points is a separate, still-open design decision
  about what a machine-readable `scan` invocation should emit on abort, not
  a mechanical wiring step like this one.
