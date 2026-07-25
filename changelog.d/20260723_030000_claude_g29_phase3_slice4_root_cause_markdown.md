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

- **Contradictory "No ABI changes detected" next to a populated root-cause
  section** (ADR-052 follow-up): when a scoped-only change or
  missing-contract label was the *only* displayed finding (`result.changes`
  itself empty or fully filtered out), `_to_markdown_root_cause` still
  appended the empty-state note purely because its check only looked at
  `changes`, producing a report that listed a real root cause immediately
  followed by "No ABI changes detected." The empty-state note now also
  checks whether any root-cause entries were actually rendered.

- **MCP `abi_compare`'s embedded markdown/text report duplicated
  scoped-gate findings under `--report-mode root-cause`** (ADR-052
  follow-up, Codex review): the CLI's primary render path threads
  `report_mode` into `_fold_scoped_compat_into_text` so it skips its own
  "Additional scoped-gate findings" appendix once `_to_markdown_root_cause`
  has already placed the same findings under `## Root Causes` — but
  `mcp_server.abi_compare`'s identical fold-in call was missed and kept the
  default `"full"`, so an MCP client's `response["report"]` showed the same
  scoped-only/missing-contract finding twice. Fixed by passing the caller's
  `report_mode` through that call too.

- **Markdown/text root-cause severity table ignored scoped-gate findings**
  (ADR-052 follow-up, Codex review): `--report-mode root-cause`'s
  "## Severity Configuration" table was built from `result.changes` before
  `_resolve_scoped_gate_findings` ran, so a `--used-by`/`--required-symbol`
  run whose only breaking issue was a scoped-only change or missing-contract
  label showed every category at `Count 0`/"no exit impact" immediately
  above a `## Root Causes` section naming that same real, gate-blocking
  finding. `_build_severity_summary_md` now accepts the already-computed
  `result.scoped_severity_counts`/`scoped_blocking_categories` (the same
  numbers the JSON fold-in's `severity`/`full_severity` swap already uses)
  and overrides both the `Count` and `Exit Impact` columns with them when
  present.
