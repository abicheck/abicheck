### Added

- **Per-dimension comparability assurance** — a `--diagnostic-comparison`
  run that bypassed a genuine contract mismatch now reports which of the
  five comparability dimensions (`symbol`, `declaration`, `layout`,
  `runtime`, `source`) the mismatch actually leaves unverified, alongside
  which stay trusted, instead of the previous all-or-nothing
  `assurance: "none"`. New `DiffResult.comparability_assurance` field,
  a new top-level `comparability_assurance` JSON report key
  (`report_schema_version` 3.1), and matching Markdown/HTML "Analysis
  Confidence" rows.
