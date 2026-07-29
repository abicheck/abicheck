### Fixed

- **`scan --against` now resolves `scope.public`/`scope.public_symbols`/
  `suppression.strict` from the project's `.abicheck.yml`**, the same way
  `compare` does (CLI flag > config > built-in default, ADR-037 D4), reusing
  `compare`'s own `resolve_compare_config`/`discover_project_config`.
  Previously `scan --against` read `--scope-public-headers`/
  `--public-symbol`/`--strict-suppressions` from the CLI only, so a shared
  project config's `suppression.strict: true` (already honored by
  `compare`) silently had no effect on `scan --against` (Codex review,
  PR #657).
