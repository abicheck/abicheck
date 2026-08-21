### Fixed

- **`contract_context.with_resolved_gate()` no longer resets
  `GateConfig.require_complete_analysis`/`scope` to their defaults.**
  Reconstructing `GateConfig` only forwarded the pre-existing
  `exit_code_scheme`/`preset`/`packs`/`severity` fields, silently dropping
  the two Phase 2 item 1 additions and making the persisted contract-context
  receipt (and the digest derived from it) describe a different gate than
  the one actually resolved (Codex review, PR #817). Both fields are still
  no-ops today (nothing constructs or reads them from real input yet), so
  this closes a latent replay gap rather than changing observed behavior.
