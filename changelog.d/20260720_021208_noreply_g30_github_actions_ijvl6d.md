<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **Report identity envelope fields (schema 2.11 / scan schema 1.1)** — the
  `compare` and `scan` JSON reports can now carry five additive, optional
  identity fields: `check_id`, `profile_id`, `requested_depth`,
  `effective_depth`, `baseline_channel` (ADR-047 §7's report-identity
  envelope, G30 P0.3). Each is omitted from the JSON entirely (never
  emitted as `null`) unless a caller explicitly sets it on `DiffResult`/
  `ScanOutcome`; nothing in the CLI populates them yet — they exist so the
  upcoming GitHub Actions integration-model primitives (G30 P1) have a
  report-level place to record a check's identity.
