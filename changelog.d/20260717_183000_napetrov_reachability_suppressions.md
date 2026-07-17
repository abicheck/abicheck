<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Suppression pipeline order could hide a real public-reachable break**
  (ADR-044): `ApplySuppression` ran before `DetectInternalLeaks`, so a broad
  `namespace`/`source_location` suppression rule could remove the raw
  evidence for an internal-type change before the leak detector ever saw it
  — silently hiding a genuine break through the public ABI surface with no
  trace in the report. A new `MarkReachability` pipeline step now tags every
  change with public-reachability metadata before suppression runs;
  `Suppression` gains a `reachability` field (`unreachable-only`/`any`/
  `public-only`, defaulting safely by selector shape) and
  `allow_public_break`, so a broad rule can no longer silently suppress a
  public-reachable `BREAKING`/`API_BREAK` change — a new
  `suppression_would_hide_public_break` diagnostic finding explains when a
  rule matched but was withheld. Also: `namespace` (and its canonical alias
  `entity_namespace`) now matches only a change's own `symbol`/qualified
  name, never its `caused_by_type` — a new `cause_namespace` selector covers
  that case explicitly, so a namespace rule aimed at an internal
  implementation detail can no longer accidentally suppress an unrelated
  finding on a *public* symbol merely because its documented cause lives in
  that namespace. **Breaking (suppression semantics):** an existing broad
  `namespace`/`source_location` rule that happened to match public-reachable
  churn will stop suppressing that subset of findings by default; add
  `allow_public_break: true` to an audited rule that should keep suppressing
  it.
