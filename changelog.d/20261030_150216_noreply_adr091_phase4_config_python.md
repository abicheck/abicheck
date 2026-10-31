<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Changed

- **Config key `acknowledgment:` is now `acknowledgments:`** (ADR-091 R3), matching the Action input and the CLI flag. The `source: {method: ...}` key moves into the `sources:` block as `sources.method` (ADR-091 R13). Migration: `docs/start/upgrading-to-0.7.md`.

### Removed

- **The old config keys `acknowledgment:` and `source.method` are gone, with no transition period** (ADR-091 D7, maintainer decision 2026-10-30). A `.abicheck.yml` that still uses one fails with the strict schema's unknown-key error and exit 64.
