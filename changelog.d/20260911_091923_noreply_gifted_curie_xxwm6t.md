<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`check-target`'s report envelope no longer misclassifies a
  `compare --no-baseline` audit as an operational failure.** A no-baseline
  audit report's `verdict` field is always `null` (ADR-068 D2 — an audit
  reports no compatibility verdict at all), which previously fell through
  `abicheck.buildsource.check_report`'s verdict classification to the
  generic `scan_guard_triggered` operational-error branch, failing every
  `check-target` single-build audit unconditionally, even a clean,
  zero-finding one under `gate-mode: advisory`/`deferred`. The envelope
  also no longer stamps the compare report's `report_schema_version` onto
  an audit document, which carries its own `audit_report_schema_version`.
