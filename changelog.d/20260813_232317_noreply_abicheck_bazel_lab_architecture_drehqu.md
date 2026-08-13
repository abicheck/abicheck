<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **Backend-independent `canonical_finding_id` for cross-backend suppressions** —
  every `compare`/`scan --against` finding now also carries a
  `canonical_finding_id` (report_schema_version 2.35, scan_schema_version
  1.15), a stable identity that survives an `--ast-frontend castxml` vs.
  `--ast-frontend clang` switch on the same underlying change, unlike the
  existing `finding_id` (which folds in `source_location`/`description`,
  fields the two header backends aren't guaranteed to spell identically). A
  new `finding_id:` suppression selector matches on this value directly, so
  a rule minted from one backend's report reliably suppresses the
  equivalent finding reported by the other.
