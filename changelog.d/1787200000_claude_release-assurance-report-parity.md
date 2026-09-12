### Fixed

- `compare`'s directory/package fan-out now threads `assurance.require_complete`
  into every result a reader sees, not just the process exit: the release JSON's
  `exit` block, `run_outcome`, `effective_config_fields
  ["gate.require_complete_analysis"]` and the `--output-dir` `summary.json`
  sidecar previously all resolved with the setting defaulted off, so a run that
  correctly exited `1` persisted `exit.code: 0`.
- `compare --format oneline` at release cardinality now folds the
  release-global bundle and probe-matrix findings into its counts; those belong
  to no library, so a bundle-only or matrix-only break printed
  `BREAKING: no changes (0 total)`.
- The release Markdown report now honours `--max-findings-per-library`, which
  it had been ignoring in favour of the built-in default cap.
