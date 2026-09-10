### Added

- **The directory/package release fan-out's per-library findings cap is now overridable, and truncation is now attributable.**
  The hard-coded 10-finding cap on each library's `findings`/`findings_view`
  lists in a directory/package `compare` (release) JSON summary is now
  configurable via `compare --max-findings-per-library N` (or, globally,
  `ABICHECK_MAX_RELEASE_FINDINGS_PER_LIBRARY`) instead of requiring
  `--output-dir` as a workaround to see more than 10 findings per library.
  When a library's list is truncated, the entry now also reports
  `findings_truncated_kinds`/`findings_view_truncated_kinds` — a
  `ChangeKind -> count` breakdown of what was cut — mirroring `scan
  --against`'s identical `--max-findings`/`findings_truncated_kinds` fix.
- **`project history`'s pairwise entries now report finding evolution.**
  `apply_finding_evolution` (ADR-068 Phase 1's `introduced`/`resolved`/
  `persistent`/`not_evaluated` correspondence primitive) previously had no
  caller outside tests. `workflows.history.build_longitudinal_history` now
  applies it once per adjacent pair against the previous pair's own
  comparison in the chain, so `project history --format json`'s
  `pairwise[]` entries carry `evolution_counts` and `resolved` (findings
  that dropped out since the previous pair) — a pre-existing finding no
  longer reads as identical to a newly introduced one across a longitudinal
  history run.
