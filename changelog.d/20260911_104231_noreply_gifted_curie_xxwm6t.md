### Fixed

- **`abicheck aggregate`'s `compatibility.analyzed_targets` no longer counts
  a completed-but-verdict-less `compare --no-baseline` audit** — only
  targets that actually produced a `compatibility_verdict` count toward
  this axis now, so the JSON no longer reads `{"verdict": null,
  "analyzed_targets": 1}` for an audit that made no compatibility claim at
  all (ADR-068 D2). `AGGREGATE_SCHEMA_VERSION` bumped to `1.10` for the
  additive `audit_only_profiles` field added in a prior fragment this
  session.
- **The retired-surfaces docs sweep now catches a parenthesized
  mode-list mention of the retired Action `mode: scan` input** (e.g. "in
  every mode (`compare`/`scan`/`dump`)"), which previously escaped the
  sweep entirely since it spells neither `mode: scan` nor `scan mode`; the
  one live occurrence (`docs/integration/scenarios/cross-compilation.md`)
  is also fixed.

