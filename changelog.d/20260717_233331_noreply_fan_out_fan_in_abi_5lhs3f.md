### Added

- **`abicheck.assessment` module for multi-target CI aggregation** — a new
  `Assessment`/`AssessmentResult` model for repositories that fan out ABI
  checks across several independently-built targets (e.g. `linux-x86_64`,
  `windows-x86_64`). Each target reports a terminal `TargetState`
  (`analyzed`, `build_failed`, `artifact_missing`, `baseline_missing`,
  `analysis_failed`, `cancelled`, `timed_out`) against a declared
  `AssessmentManifest`; the aggregator unions findings only from
  successfully-`analyzed` targets and reports every other target as
  unavailable rather than fabricating an empty-ABI comparison for it.
  Exposes separate `findings_verdict` and `coverage_verdict()` so a build
  infrastructure failure on one target is never presented as an ABI
  regression, plus `compare_target_sets` to report an added/removed target
  as a support-set change rather than symbol removals.
