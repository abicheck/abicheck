<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--report-mode root-cause` SARIF properties** (ADR-052, G29 Phase 3
  slice 5): `--format sarif` keeps its normal one-result-per-finding shape
  (so every existing SARIF/code-scanning consumer keeps working unchanged)
  but each result now gets `properties.rootCauseId`/`properties.rootCause`
  when combined with `--report-mode root-cause` — group results by
  `rootCauseId` yourself if you want the same buckets JSON/markdown show.
  Shares the exact grouping decision (`_root_cause_key_and_display`) the
  JSON and markdown renderers already use, so all three formats can never
  disagree about which findings correlate. `--format junit` still renders
  `root-cause` mode as `full` (its `<testcase>` model already groups by
  symbol, not by finding). See `docs/user-guide/output-formats.md`.
