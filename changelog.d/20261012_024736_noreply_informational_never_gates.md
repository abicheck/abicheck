### Fixed

- **An informational, no-material-change finding no longer fails the build
  under `--severity-preset strict`** — `declaration_identity_unchanged`,
  `declaration_coordinates_shifted`, `public_surface_grew` and
  `public_surface_shrank` record that nothing a consumer can act on changed
  (a reconciliation proving a declaration identical, or a roll-up of findings
  reported on their own), but they were counted as `quality_issues`. So
  `strict`, or any `quality_issues: error`, exited `1` on them. They now land
  in a new, fixed `informational` severity category. It has no configurable
  level and never contributes to the exit code. The kinds are declared by
  `ChangeKindMeta.is_informational` and exposed as `INFORMATIONAL_KINDS`. The
  findings stay in every report: `summary.informational`,
  `severity.categories.informational`, an "Informational (no material change)"
  Markdown section, and the release `libraries[].informational` count. **JSON
  consumers:** `summary.quality_issues` (and the release per-library
  `quality_issues`) no longer counts these findings, a meaning change, so
  `report_schema_version` is now 6.0 and `release_schema_version` is now 2.0.
  The pre-6.0 value is `quality_issues + informational`. See ADR-078.
