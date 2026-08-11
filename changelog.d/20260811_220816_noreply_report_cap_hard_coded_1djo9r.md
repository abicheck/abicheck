### Added

- **`scan --against`'s report cap is now overridable, and truncation is now attributable.**
  The hard-coded 20-finding cap on the `--against` summary's `findings`/`suppressed`
  lists is now configurable via `scan --max-findings N` / `ScanRequest.max_findings`
  (or, globally, `ABICHECK_MAX_BASELINE_FINDINGS`) instead of requiring a monkeypatch
  to raise. When either list is truncated, the summary now also reports
  `findings_truncated_kinds`/`suppressed_truncated_kinds` — a `ChangeKind -> count`
  breakdown of what was cut — so the shape of a truncated diff is visible without
  rerunning at a higher cap. Bumps `SCAN_SCHEMA_VERSION` to `1.10`.

