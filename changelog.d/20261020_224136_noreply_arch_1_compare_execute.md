<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Changed

- **The typed compare pipeline has one owner, `workflows/compare/`** — the
  2-phase compare (`resolve_compare_request` in `resolve.py`,
  `classify_compare_pair` / `run_compare_request` / the `run_compare` keyword
  shim in `execute.py`) moved out of the flat `service_compare_pipeline.py`,
  which is deleted. Python imports of `abicheck.service_compare_pipeline` are
  gone; `abicheck.service`'s `run_compare` and `run_compare_request` keep
  their names and signatures.
  Behaviour, exit codes and output are unchanged.
