<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--report-mode root-cause` markdown/text rendering** (ADR-052, G29
  Phase 3 slice 4): the root-cause grouping `--report-mode root-cause`
  added to JSON output now also renders for `--format markdown` and the
  default text output — one `### root (N findings)` heading per root
  cause instead of `full` mode's severity-bucketed sections. `--format
  sarif`/`junit` still render `root-cause` mode as `full`. The grouping
  function moved to `reporter_markdown.py` (`_group_changes_by_root_cause`,
  alongside `_finding_id`/`_root_cause_key_and_display`) so the JSON and
  markdown renderers share one grouping decision instead of risking drift
  between two implementations. `--show-impact` (Codex review) appends the
  same Impact Summary table full/leaf markdown already support. See
  `docs/user-guide/output-formats.md`.

### Fixed

- **Markdown/text root-cause mode didn't merge scoped-gate findings into
  existing groups** (ADR-052 follow-up): combined with `--used-by`/
  `--required-symbol`, a scoped-only finding or missing-contract label
  whose `caused_by_type`/symbol correlated with an existing change was
  still only listed separately under a flat "## Additional scoped-gate
  findings" appendix, under-reporting that group's `finding_count` and
  hiding the correlation (unlike the JSON/SARIF paths, which fold these
  in). `_to_markdown_root_cause` now merges `scoped_only_changes` and
  `scoped_missing_labels` into the same root-cause groups via
  `_resolve_scoped_gate_findings` (moved from `cli_compare_fold.py` to
  `reporter_markdown.py` so both sides can share it); the scoped-gate
  fold-in skips its own appendix for markdown/text root-cause mode to
  avoid double-listing.
