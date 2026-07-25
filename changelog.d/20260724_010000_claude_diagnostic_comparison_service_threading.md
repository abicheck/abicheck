<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`diagnostic_comparison` threaded through the Tier-2 service layer**
  (ADR-050 D2 rollout-risk follow-up): `checker.compare()`'s
  comparability-contract escape hatch was previously only reachable by
  calling the Tier-1 core directly — every real front-end (CLI, MCP, `scan`,
  `appcompat`) routes through `service.compare_snapshots()`/
  `run_compare_request()` instead (ADR-037 D1/D10.1), which had no way to
  pass it through. Added `diagnostic_comparison: bool = False` to
  `api_types.CompareRequest`, `service.compare_snapshots()`,
  `service.run_compare_request()`, and the legacy `service.run_compare()`
  shim (appended as the new last positional parameter, same rule already
  applied to `debuginfod_url`, so no existing positional caller's bindings
  shift). This lands ahead of `dumper.py` wiring `AbiSnapshot.contract` on
  real dumps (separate, upcoming change) specifically so a programmatic
  escape hatch exists the moment the comparability gate goes live for real
  comparisons, even before any CLI flag for it is built.
