### Changed

- **`scan_schema_version` bumped to `1.4`** — the previous slice added
  `diff.suppressed_count`/`diff.suppressed`/`diff.suppressed_truncated` to
  `scan`'s JSON output without recording the additive schema bump the
  documented `SCAN_SCHEMA_VERSION` contract requires, so a schema-aware
  consumer couldn't distinguish the new shape from earlier `1.3` reports
  (Codex review, PR #657).
