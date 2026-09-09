<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **The Action's `mode: compare` `NOT_COMPARABLE` error pointed users at a
  nonexistent JSON field** (Codex review, round 11). The message said "see
  the JSON report's `diff.reason` for what mismatched" — correct for
  `scan`'s own NOT_COMPARABLE JSON (`diff_summary["reason"]`, genuinely
  nested under `diff`), but `compare`'s comparability-gate refusal
  (`_report_not_comparable()`) raises before any `DiffResult` exists, so its
  `--format json` document carries the mismatch at **root** `reason`
  (schema 2.17), not `diff.reason` — and for every human-facing format
  (markdown/html/review, the Action's own default), that function writes no
  JSON document at all, not even into a secondary `--write` the Action
  injects. Pointing at "the JSON report" was therefore wrong on both the
  field path and, for the common default-format case, the report's very
  existence. Repointed the message at the command's own stderr output
  (already in the job log above), with the root `reason` field named only
  as the `format: json` case.
