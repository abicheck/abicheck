### Added

- **`CompareRequest.strict_suppressions` and `CompareRequest.require_justification`.** The typed Python API now honors the suppression strictness the CLI reads from `.abicheck.yml` (`suppression.strict`, `suppression.require_justification`): an expired rule or a rule with no `reason` raises `ValidationError`, with the same message the CLI prints. Both default off, so existing callers are unchanged. Both front ends load suppressions through one function, `abicheck.workflows.compare_policy.load_suppression_and_policy`.
