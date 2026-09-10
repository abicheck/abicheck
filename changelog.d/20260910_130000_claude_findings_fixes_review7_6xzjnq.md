### Fixed

- **A typed `CompareRequest.pack_policy_overrides`/`project_policy_overrides`
  entry targeting `Verdict.NO_CHANGE` was rejected too late.** Round 6's fix
  raised the error inside `classify_compare_pair`, which `run_compare_request`
  only reaches *after* `resolve_compare_request` has already run extraction
  (or invoked an authorized build system, for a `build.query` request)
  against a live operand -- so the invalid request could still perform
  expensive, possibly side-effecting work before eventually failing. Fixed
  by moving the check into `CompareRequest.validation_errors()`, the
  standard pre-resolution validation hook every other invalid-request shape
  here already uses (the same one that already catches a misspelled
  `severity_preset`/unknown `policy` before any extraction runs), so an
  invalid request now fails before planning and execution rather than after.
