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

### Fixed

- **SARIF root-cause grouping used an unfiltered `--show-only` preview**
  (ADR-052 follow-up): `to_sarif`'s `referenced_causes` computation read
  `scoped_only_changes` before `--show-only` filtering, so a hidden,
  filtered-out scoped-only finding's `caused_by_type` could still group two
  unrelated *visible* findings that merely shared its symbol — disagreeing
  with JSON/markdown root-cause mode, which computes `referenced_causes`
  from the filtered set only. Fixed by computing the filtered
  `scoped_only_changes` once, up front, and reusing it for both the
  `referenced_causes` set and the results loop.
