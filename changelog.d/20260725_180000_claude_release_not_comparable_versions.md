<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`compare` release fan-out's per-library `not_comparable` JSON artifact
  (`--output-dir`) omitted `old_version`/`new_version`**, unlike every other
  per-library report shape (`to_json(result)`'s normal path, and the native
  `compare`'s own not-comparable report). A consumer reading
  `--output-dir`'s per-library files couldn't identify which release pair a
  `verdict: null` report belonged to. The special-case document built in
  `cli_compare_release._compare_one_library`'s `ProfileMismatchError`/
  `ScopeMismatchError` handler now includes both fields, matching the
  schema every other report shape already carries.
