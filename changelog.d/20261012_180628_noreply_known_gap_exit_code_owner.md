<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **One owner for the evidence-contract exit precedence** — the exit-7 axis
  that a failed pinned-evidence contract (`--depth`) takes over the ordinary
  gate/coverage/assurance fold is now decided by one function,
  `resolve_evidence_over_ordinary`, for scalar `compare`, `scan`'s resolver
  and the directory/package release. No exit code changes for any input.
  The one difference is the explainability fields of a scalar `compare`
  whose `DiffResult` carries `evidence_contract_error`: its `exit` block now
  keeps the ordinary gate/coverage/assurance contributions, as the release
  path already did. The release's removed-library ladder stays separate.
