### Fixed

- **`ReportEnvelope` no longer deep-copies whole `AbiSnapshot` operands** —
  `build_report_envelope` now uses a shallow, container-decoupled copy
  (mirroring `_snapshot_diff_result`'s own approach) instead of
  `copy.deepcopy`, which measurably dominated render time on large
  libraries (~1.3s per 10,000-function snapshot). The shallow copy still
  closes the two reported cases (`old.version` reassignment, `old.functions`
  list-level mutation) at a small fraction of the cost.
- **SARIF's `suppressions` array no longer re-runs policy classification
  per render** — `ReportEnvelope` now pre-resolves `result.suppressed_changes`
  into `suppressed_findings` at construction time, so a suppressed
  finding's verdict/category is a pure index lookup during rendering
  instead of falling through to `findings_for`'s per-change fallback on
  every single render.
