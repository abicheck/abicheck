### Fixed

- A persisted `evaluation_context` no longer names CLI flags for inputs an
  API caller stated as typed fields. `run_scan(ScanRequest(...))` recorded
  its `policy`/`scope_public` as `--policy`/`--scope-public-headers`, and the
  MCP `abi_compare` tool recorded `severity_preset`/`severity_*` as
  `--severity-preset`/`--severity-*` — command lines nobody ran, so the
  receipt could not identify the input that selected the value.
- A scan receipt also named `CompareRequest`'s field spellings
  (`scope_public`, `policy_file_path`, `suppress`) for a `ScanRequest`, which
  calls the same three inputs `scope_to_public_surface`, `policy_file`, and
  `suppression`. "The API" is not one namespace; the resolver now takes an
  `api_spellings` remap per request type.

### Added

- `compatibility_evaluation_frontend.unstatable_selectors()` reports any
  provenance hop naming an input its own selector layer cannot state: a CLI
  flag at an API tier, and — given `request_type=` — any name that is not a
  real field of that request. The cross-front-end equality gate structurally
  cannot catch either; it normalizes option spellings on purpose, since the
  same semantic input is legitimately spelled differently per front end.
  That is why the same defect was found four separate times by review before
  this check existed.
