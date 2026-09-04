### Fixed

- **A typed `CompareRequest` with a misspelled `severity_preset` (e.g.
  `"strcit"`) was only rejected after both sides had already been
  extracted.** `CompareRequest.validation_errors()` already fail-fast
  rejects an unknown `exit_code_scheme`, but left its sibling field,
  `severity_preset`, unchecked — a bad value still reached
  `resolve_release_gate_options` only after `resolve_compare_request` had
  already run extraction. `severity_preset` is now validated the same way,
  against the same preset table `resolve_severity_config` itself resolves
  from.
- `scan --artifact-set --dry-run`'s pinned-depth-with-no-evidence blocker
  message named exit code 7 (the single-artifact scan's dedicated
  evidence-contract-error code) for what is actually the `--artifact-set`
  aggregate path, which still floors at exit 1 for this axis
  (`service_scan._aggregate_scan_set_verdict`). The message now states
  both codes correctly.
