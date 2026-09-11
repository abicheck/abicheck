### Fixed

- **The aggregate report JSON Schema's `audit_only_profiles` field is
  optional again** — it was mistakenly added to `profile_matrix_entry`'s
  `required` list, which would reject every valid pre-1.10 aggregate
  document; additive schema changes stay optional keys per this schema's
  own documented versioning discipline.
- **The GitHub Action's job summary no longer titles an audit-only
  `compare --no-baseline` run as an "ABI Compatibility Report"** — such a
  run has no baseline and reports no compatibility verdict at all
  (ADR-068 D2); the heading now reads "ABI Audit Report" instead,
  consistent with the `AUDIT_CLEAN`/`AUDIT_RISK` verdict text already
  printed below it.

