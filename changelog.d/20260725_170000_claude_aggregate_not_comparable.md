<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`aggregate` (the multi-target CI fan-in gate) silently folded an
  ADR-050 D2 not-comparable per-target report into the same "unavailable"
  bucket a target whose build never produced a report at all gets** — a
  coverage gap that's advisory under `--on-missing-required warn`, an
  optional target, or (having no coverage axis at all) `discovered_only`
  mode, none of which are appropriate for "we have definitive evidence this
  comparison couldn't be trusted." `_load_report_file` now special-cases a
  real `verdict: null` + structured `reason` (schema 2.17) the same way it
  already special-cases a compare-release operational-error report: a
  synthetic blocking `BREAKING`/exit-4 `GateInfo`
  (`blocking_categories=("not_comparable",)`) with the actual mismatch
  reason preserved on `TargetReport.reason` and surfaced in
  `render_text()`, so it folds into `exit_code()` unconditionally —
  required or optional, `warn` or `fail`, `discovered_only` or not.
