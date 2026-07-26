<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`plan --dump-manifest PATH` (ADR-050 D3, G32 Phase B)**: a new
  diagnostic command that parses and normalizes a `--dump-manifest`
  document and prints its `scope_fingerprint` (ADR-050 D1) — computed from
  the manifest document alone, no compiler invoked. Never prints a
  `profile_fingerprint`, which requires a real L2 extraction to exist at
  all; cheap to run in CI before committing to a full `dump --dump-manifest`
  / `compare --dump-manifest` invocation. Supports `--format text|json` and
  `-o/--output`.
