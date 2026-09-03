<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **`compare-release`'s per-library effective-config digest now carries real
  pack identity.** Under `--pack`, each library's own report previously
  stayed at the baseline tier of `effective_config_digest` even though a
  resolved `CompatibilityEvaluationConfig` existed for the release — so two
  runs under different pack *revisions* that happened to project the same
  current policy/severity assignments produced the identical per-library
  digest. `_run_compare_pair` now stamps each library's `DiffResult` with
  the release's already-resolved config, the same way single-pair `compare`
  already does, closing the gap `effective_config_digest`'s own module
  docstring documented.
