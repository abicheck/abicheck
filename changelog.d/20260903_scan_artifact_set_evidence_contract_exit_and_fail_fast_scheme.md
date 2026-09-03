### Fixed

- **`scan --artifact-set`'s composite Action verdict for a member's evidence-
  contract abort was silently reclassified as generic `ERROR`.** The prior
  redesign that moved `EVIDENCE_CONTRACT_ERROR` onto its own dedicated exit
  code (7, for a single `ARTIFACT`) deleted the JSON-report-based check
  `action/run.sh` used to disambiguate this axis from a real CLI error at
  exit 1 — but `--artifact-set` still floors *its own* exit code at 1 for
  exactly this axis (a member's abort is caught inside
  `service_scan._aggregate_scan_set_verdict`, which never reaches
  `cli_scan.py`'s single-binary exit-7 catch site at all). `action/run.sh`'s
  exit-1 dispatch now checks the JSON report's `compat_verdict` for
  `EVIDENCE_CONTRACT_ERROR` again, ahead of the generic CLI-error check, the
  same way every other exit-1 disambiguation in that dispatch already works.
- **A typed `CompareRequest` with an invalid `exit_code_scheme` was only
  rejected after both sides had already been extracted.** The prior fix
  validated the scheme inside `resolve_release_gate_options`, but
  `classify_compare_pair` only calls that resolver *after*
  `resolve_compare_request` has already run — potentially slow,
  project-controlled extraction. `CompareRequest.validation_errors()` now
  rejects an unknown `exit_code_scheme` itself, so every front end that
  calls `validate()`/`validation_errors()` (the native `compare` CLI
  included) fails fast with a `ValidationError` before any extraction runs,
  the same way its other cross-field checks (`contract_mode`,
  `policy_file_path`, `depth`, ...) already do.
