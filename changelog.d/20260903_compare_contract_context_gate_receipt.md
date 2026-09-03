### Fixed

- **A `CompareRequest(contract_evaluation=True, ...)` combined with a
  non-default `severity_preset`/`exit_code_scheme` persisted a
  `contract_context` whose resolved gate configuration disagreed with the
  actual exit decision.** `classify_compare_pair` already resolved the
  request's gate to score `CompareResult.exit_decision`, but never installed
  that same gate onto `result.contract_context.evaluation_context.
  resolved_config` — so a caller reading the persisted receipt (or a replay
  built from it) would see `checker.compare`'s own built-in default
  severity/scheme rather than what the request actually asked for and was
  scored with. `classify_compare_pair` now installs the resolved gate onto
  the contract context too (via the new `workflows.compare_gate_receipt`
  module), mirroring what the native `compare` CLI's own
  `record_resolved_config` already does.
