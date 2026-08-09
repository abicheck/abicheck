<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **CI: restored the `unit-tests` and `ai-readiness` jobs on `main`.**
  `abicheck/cli_options.py` had drifted past the AI-readiness 2000-line hard
  cap. Split the ADR-037 D10 CLI-contract metadata
  (`FAMILY_FLAGS`/`COMPARE_FLAG_BUDGET`/`count_visible_options`) out of
  `cli_options.py` into a new leaf module, `abicheck/cli_options_contract.py`
  — re-exported from `cli_options.py` for existing callers, mirroring the
  `cli_profiles.py` split already used for the same reason. No behavior
  change.

