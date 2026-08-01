### Fixed

- A persisted `evaluation_context` no longer names CLI flags for inputs an
  API caller stated as typed fields. `run_scan(ScanRequest(...))` recorded
  its `policy`/`scope_public` as `--policy`/`--scope-public-headers`, and the
  MCP `abi_compare` tool recorded `severity_preset`/`severity_*` as
  `--severity-preset`/`--severity-*` — command lines nobody ran, so the
  receipt could not identify the input that selected the value.

### Added

- `compatibility_evaluation_frontend.unstatable_selectors()` reports any
  provenance hop naming an input its own selector layer cannot state. The
  cross-front-end equality gate structurally cannot catch this — it
  normalizes option spellings on purpose, since the same semantic input is
  legitimately spelled differently per front end — which is why the same
  defect was found three separate times by review before this check existed.
