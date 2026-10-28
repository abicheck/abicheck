<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Typed API severity provenance** — a `CompareRequest` with `severity_abi_breaking`, `severity_potential_breaking`, `severity_quality_issues` or `severity_addition` now records that level in its gate receipt as the stated `api_request` value, not `built_in_default`. The exit code was already correct; only the receipt disagreed, and it now matches the `severity.<category>` config route (`project_config`).
