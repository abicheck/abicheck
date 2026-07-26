<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`abicheck scan --against` now surfaces the ADR-050 D2 comparability
  gate instead of an unhandled traceback.** `run_scan_core`'s
  `_run_baseline_compare` call gains a dedicated
  `except (ProfileMismatchError, ScopeMismatchError)` branch (alongside its
  existing budget-overflow handling), setting `verdict: "NOT_COMPARABLE"`
  and exit code **`6`** with the mismatch reason in `diff.reason` — and
  skipping the cross-check severity promotion step, since a hard gate
  result is never something a promoted finding should be able to soften.
  `service.run_scan`/the MCP `abi_scan` tool pick this up automatically
  (they build their result from the same `ScanOutcome`, no separate fix
  needed). `SCAN_SCHEMA_VERSION` bumped to `1.3` for the new verdict value.
