### Fixed

- **A `CompareRequest` naming an unknown `policy` with no `policy_file_path`
  override was only rejected after both sides had already been extracted.**
  The earlier fix normalized the policy name through `stated_policy_base`
  for the case where a file overrides it, but left the no-file case
  unchecked — `CompareRequest(policy="not_a_policy")` alone still only
  failed later, inside `builtin_policy_identity` during gate-receipt
  installation, after extraction had already run.
  `CompareRequest.validation_errors()` now rejects a `policy` name outside
  the built-in policy set when no `policy_file_path` is given, mirroring
  `stated_policy_base`'s own "a file overrides the name" rule.
