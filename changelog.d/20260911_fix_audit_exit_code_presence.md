### Fixed

- The audit lane now requires the `exit_code` field to be *present* in the
  rendered JSON, not merely non-conflicting. `audit_report.schema.json` lists
  it in `required`, but `payload.get("exit_code") not in (0, None)` read an
  absent field as `None` and passed — so a renderer dropping a required field
  would have left every audit fixture green.
- `docs/use/output-formats.md` no longer claims globally that only two
  report-schema markers exist; `aggregate` has its own
  `aggregate_schema_version`, documented on the same page.
