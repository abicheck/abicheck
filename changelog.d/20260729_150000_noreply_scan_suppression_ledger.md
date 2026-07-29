### Added

- **`scan --against`'s JSON summary now exposes a suppression audit trail**
  (`diff.suppressed_count`/`diff.suppressed`) — `compare`'s own JSON report
  already surfaces which findings a `--suppress` rule silenced
  (`DiffResult.suppressed_changes`); `scan --against` honored suppression
  rules (ADR-049 Phase 5) but never showed which finding got suppressed.
  Capped independently of the existing gating-findings truncation so a
  large suppression file can't crowd out real breaking/risk findings from
  the always-on summary.
