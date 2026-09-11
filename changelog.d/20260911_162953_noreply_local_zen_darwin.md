<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **SARIF and JUnit now project the deployment-floor digest from the frozen
  report document, not the mutable comparison result** — `sarif.py`'s
  `envMatrixSourceSha256` property and `junit_report.py`'s
  `_add_env_matrix_property()` were still reading `DiffResult.
  env_matrix_source_sha256` directly instead of the already-resolved
  `ReportEnvelope`/`ReportDocument` every other format (JSON, Markdown,
  HTML) already reused, so a mutation to the result between two projections
  of one envelope could make SARIF/JUnit disagree with the rest of a
  multi-format render. Both now read through a shared
  `report.envelope.env_matrix_digest_reusing_document` accessor, matching
  the existing `disposition_audit_dict_reusing_document` pattern.
