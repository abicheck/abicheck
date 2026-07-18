<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- `param_defaults`'s hybrid-provenance gate skipped a function pair whenever
  EITHER side's producer was unknown (e.g. a persisted castxml baseline
  predating `ast_producer` entirely), not just when both were known and
  differed — regressing a legacy baseline compared against a genuinely
  castxml-backed function on a `--ast-frontend hybrid` snapshot into a
  silent miss. The skip now only fires when both producers are positively
  known and different.
