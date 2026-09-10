### Fixed

- **`ReportEnvelope` snapshotting no longer breaks the disposition
  ledger's conservation invariant** — `_snapshot_diff_result` now remaps
  `DiffResult.disposition_ledger`'s identity-keyed records onto the
  envelope's own copied `Change` objects. Without this, `with_gate`'s
  `id(change)` membership test could no longer find a single gating
  record among the envelope's new objects, silently reporting
  `effective_total: 0` in the disposition audit beside a real, blocking
  exit code.
