<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`abicheck compare --format sarif`/`--format junit` now render an
  ADR-050 D2 comparability-gate hard failure as a real, spec-conformant
  document of their own, instead of writing nothing.** `sarif.py` gains
  `to_sarif_not_comparable`: a SARIF 2.1.0 run with
  `invocations[0].executionSuccessful: false`, `exitCode: 16`, and the
  reason in a `toolExecutionNotification` (the spec's own mechanism for a
  tool-level problem, not a synthetic finding-shaped `result` for something
  that isn't a finding). `junit_report.py` gains
  `to_junit_xml_not_comparable`: a `<testsuite errors="1">` with one errored
  `<testcase>`, mirroring the existing "library whose comparison crashed"
  shape. Both are wired into `cli_compare_helpers._report_not_comparable`.
  `--format markdown`/`html`/`review` are unchanged — those human-facing
  formats already surface the same clear stderr message, and neither has an
  equivalent "run failed" document convention worth fabricating.

### Fixed

- **`compare-release --format junit`'s per-library JUnit report silently
  omitted a `"not_comparable"` library entirely** — `_format_release_junit`
  only forwarded `verdict == "ERROR"` entries to `error_libs`, so a library
  that hit `PROFILE_MISMATCH`/`SCOPE_MISMATCH` (its own dedicated verdict
  string, not folded into `"ERROR"`) contributed zero testsuites, leaving a
  CI dashboard reading only the JUnit file blind to exactly the failure that
  made the release exit non-zero. Now included as an errored testsuite,
  using its `reason` message.
