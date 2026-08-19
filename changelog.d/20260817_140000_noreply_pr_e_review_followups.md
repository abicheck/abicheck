### Fixed

- **The CLI no longer writes its own `$GITHUB_STEP_SUMMARY` job summary at
  all.** An earlier revision of this PR's flag removal made that write
  unconditional whenever running in CI (as a replacement for the deleted
  `--annotate` flag's side effect) — a real regression when running
  through the composite GitHub Action: the subprocess already inherits
  `GITHUB_ACTIONS=true`/`GITHUB_STEP_SUMMARY` from the Action's own job, so
  the unconditional write double-wrote against `action/run.sh`'s own,
  richer, `add-job-summary`-gated job summary (or wrote one even when a
  caller explicitly set `add-job-summary: false`).
  `abicheck.annotations_step_summary.emit_github_step_summary` stays
  available as a public primitive for a caller invoking the CLI directly
  outside the composite Action.
- **The persisted `annotations` array now includes a missing
  `--used-by`/`--required-symbol` contract member** (a label the new
  library lacks entirely, `DiffResult.scoped_missing_labels` — distinct
  from a *present* scope-synthesized finding, already covered) —
  previously a comparison whose sole gating finding was a missing label
  could exit non-zero with nothing in either the stderr annotation stream
  or the persisted `annotations` array to explain why. Classified the same
  way every other consumer of `scoped_missing_labels` already does
  (`sarif.py`, `junit_report.py`, `reporter_markdown.py`): unconditionally
  a hard block under the legacy scheme, or gated on
  `severity.missing_contract_exit_code` under a severity scheme.
- **The composite Action's `annotate`/`annotate-additions` inputs now
  explain, rather than silently do nothing, when the primary format isn't
  json and the caller's own `extra-args` `--write` targets a non-json
  format** (markdown/junit/sarif/html/review) — unlike a `--write
  json=PATH`, there is genuinely no JSON report anywhere in that
  configuration for the renderer to discover.
