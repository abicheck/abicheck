### Changed

- **Release comparison runs without Click** — the directory/package release
  is now run by `abicheck.workflows.release_compare.execute_release_compare`
  (`ReleaseCompareRequest` -> `resolve_release_compare_plan` ->
  `execute_release_compare(plan, ReleaseCompareOptions(...))` ->
  `ReleaseCompareOutcome`). That function owns member iteration, each
  member's comparison through the scalar per-member primitive, the
  unreached-type and lockstep-SONAME passes, scope, surface, bundle and
  build-matrix analysis, and every exit-axis fold. The CLI command only
  parses options and renders. Report projection, `--output-dir` files and
  stderr text reach the workflow through the
  `workflows.release_compare_reporter.ReleaseCompareReporter` port.
  `cli_compare_release_pairwise.py`, `frontends/cli/release_member_errors.py`
  and `frontends/cli/release_member_restamp.py` are deleted, not re-exported:
  - the fan-out is now `workflows.release_members`;
  - failure classification is `workflows.release_member_failures`;
  - bundle analysis and `--bundle-facts-out` capture are
    `workflows.release_bundle` (they raise the typed `ReleaseOperandError`
    family);
  - `collect_matrix_result` is in `workflows.release_compare`;
  - `release_scope_warnings` is in `workflows.release_scope_notices`.

  CLI output and exit codes are unchanged.
