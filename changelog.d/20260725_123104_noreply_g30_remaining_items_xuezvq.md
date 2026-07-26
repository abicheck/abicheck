### Fixed

- **`check-project.yml` now reports candidate-resolution and required
  build-output-download failures as typed operational errors** instead of
  silently ending the matrix job with no report for `abicheck aggregate` to
  see (G30 P1.4, [#628](https://github.com/abicheck/abicheck/issues/628)).
  The "Download build-output artifact" step no longer swallows a required
  download failure via `continue-on-error`, and a new "Synthesize pre-check
  operational-error report" step reuses
  `actions/check-target/report_envelope.py --mode operational-error`
  directly to write a full report envelope for `aggregate` to fan in,
  matching how a real `resolve-baseline` failure is already reported.

