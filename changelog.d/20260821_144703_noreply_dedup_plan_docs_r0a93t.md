### Fixed

- **`GateConfig.require_complete_analysis`/`scope` now round-trip through
  `resolved_config_to_dict`/`resolved_config_from_dict`.** Previously
  omitted entirely, so a JSON round-trip of a `CompatibilityEvaluationConfig`
  silently dropped both fields back to their defaults (Codex review, PR
  #817). Both fields are still no-ops today (nothing constructs or reads
  them from real input yet), so this closes a latent replay gap rather than
  changing observed behavior.
