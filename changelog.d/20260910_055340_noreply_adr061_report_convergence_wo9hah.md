### Fixed

- **`ReportEnvelope` no longer shares mutable `Change` objects with the
  caller** — `report.build.build_report_envelope`'s snapshot now also
  copies every individual `Change` (`_snapshot_change`), not only the lists
  that contain them. `Change` is an ordinary mutable dataclass (pattern
  modulation legitimately reassigns `effective_verdict` on one during
  `compare()`), so a caller reassigning a field on a `Change` it still holds
  a reference to — after the envelope was already built — could previously
  desynchronize a format that classifies straight from `envelope.result`
  (SARIF's rule/level derivation, Markdown's per-row verdict) from the
  document/findings the envelope already froze.
