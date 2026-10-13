<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Removed

- **Report-view helpers moved to `abicheck.report.view`** — `ShowOnlyFilter`,
  `apply_show_only`, `parse_show_only_groups`, `SHOW_ONLY_GROUP_SEP`,
  `show_only_matches`, `show_only_matches_severity_label`,
  `render_show_only_cli_hint`, `suppress_dangling_correlation_notes`, and the
  root-cause and scoped-gate helpers (`root_cause_key_and_display`,
  `group_changes_by_root_cause`, `resolve_scoped_gate_findings`, `finding_id`)
  now have one owner. The `abicheck.reporter` and `abicheck.reporter_markdown`
  re-exports of them are removed; import them from `abicheck.report.view`.
  Every output format is unchanged byte for byte.
