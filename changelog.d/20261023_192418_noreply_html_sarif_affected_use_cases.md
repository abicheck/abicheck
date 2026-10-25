### Added

- **Per-finding use cases in HTML and SARIF** — under `compare --use-cases`, the HTML changes table names each finding's `Affected use cases` (or `none` when no use case reaches it), and every SARIF result carries `properties.affectedUseCases`. Both read the same per-finding attribution the JSON `affected_use_cases` field and the Markdown rows already use, so the three formats agree on every finding. Without `--use-cases` neither output changes, and the verdict and exit code are unaffected.

