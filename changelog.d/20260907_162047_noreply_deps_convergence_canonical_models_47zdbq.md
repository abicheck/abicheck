### Changed

- **`deps` converges onto the canonical exit-decision/report models
  (ADR-068 D6)** — `deps compare`/`deps tree` now compute their exit codes
  through the shared `policy.exit_decision.resolve_exit_decision` fold (a
  new `ExitReason.LOADABILITY` axis for `deps`'s own loadability signal,
  reusing the existing `NOT_COMPARABLE` reason for ADR-050 D2's
  profile/scope-mismatch case) instead of `cli_stack.py`'s previous
  hand-rolled `sys.exit(1)`/`sys.exit(4)`/`sys.exit(5)` chain, and their
  JSON report is now a `report.stack.compute_stack_report_document` →
  `ReportDocument` projection. No `deps` flag, exit code, or JSON shape
  changes — this is internal convergence only.
