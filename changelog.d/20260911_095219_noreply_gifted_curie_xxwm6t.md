<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **A `compare --no-baseline` audit that failed its own evidence contract
  no longer reads as a clean pass.** `abicheck.buildsource.check_report`'s
  no-baseline classification unconditionally treated the shape as free of
  operational error; a run whose pinned depth/source-method could not be
  satisfied (`run_outcome.operational: evidence_contract_error`, exit 7)
  is now still recognized as an operational failure, so
  `gate-mode: advisory`/`deferred` can no longer turn it into a quiet
  exit 0.
