<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--report-mode root-cause` markdown/text rendering** (ADR-051, G29
  Phase 3 slice 4): the root-cause grouping `--report-mode root-cause`
  added to JSON output now also renders for `--format markdown` and the
  default text output — one `### root (N findings)` heading per root
  cause instead of `full` mode's severity-bucketed sections. `--format
  sarif`/`junit` still render `root-cause` mode as `full`. The grouping
  function moved to `reporter_markdown.py` (`_group_changes_by_root_cause`,
  alongside `_finding_id`/`_root_cause_key_and_display`) so the JSON and
  markdown renderers share one grouping decision instead of risking drift
  between two implementations. See `docs/user-guide/output-formats.md`.
