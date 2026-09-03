### Fixed

- **A typed `CompareRequest`'s `severity_preset`/`exit_code_scheme` now
  reach the ADR-049 D7 resolver behind `contract_evaluation=True`.**
  `compare_request_inputs()` previously left both fields out of the
  `ExplicitCompatibilityInputs` receipt it builds, so a `--contract` JSON
  report's persisted `gate.*` configuration/provenance/digest could disagree
  with `CompareResult.exit_decision` (which reads the two request fields
  directly) for a typed caller combining `contract_evaluation=True` with
  either field. Both fields now forward unchanged.
