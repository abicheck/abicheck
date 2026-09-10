### Fixed

- **`ReportEnvelope` no longer shares nested mutable state with the caller's
  `DiffResult`, and HTML's compatibility metrics no longer re-resolve
  time-sensitive verdicts on every render** — the envelope's snapshot now
  deep-copies every `list`/`tuple`/`dict` *element* of a `DiffResult` field
  (not just the outer container), so a shared structure like
  `contract_conflicts` can no longer be mutated through the caller's
  original object after the envelope was built. `report_summary.
  compatibility_metrics()` gained an `effective_verdicts` parameter so an
  ADR-061 `ReportEnvelope` caller (HTML's own compatibility-metrics
  section) reuses the envelope's already-resolved per-change verdicts
  instead of recomputing `effective_verdict_for_change` against a
  `PolicyFile.reclassify` rule's expiry date on every render, which could
  otherwise change a rendered percentage after that rule expires even
  though the envelope's document was frozen earlier.
