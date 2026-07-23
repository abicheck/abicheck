<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--report-mode root-cause`** (ADR-050, G29 Phase 3 slice 3): groups
  findings sharing a root cause (`Change.caused_by_type`) under one entry
  instead of listing every change individually — e.g. an internal helper's
  `func_removed` finding and the `internal_symbol_required_by_public_api`
  overlay finding that names it now land in the same group. JSON output
  only (`--format json`); other formats render as `full`. Adds two
  additive top-level keys, `root_causes` and `root_cause_count`
  (`report_schema_version` 2.13 → 2.14); `changes` is still emitted in
  full for backward compatibility. See `docs/user-guide/output-formats.md`.

### Fixed

- **`impact_assessment.decision.suppression_rule` missing for late-detector
  suppressions** (ADR-050 follow-up): a suppression rule that suppressed a
  finding built by `DetectCppPatterns`/`DetectTemplatePatterns`/
  `DetectNamespacePatterns` (which run after the main suppression pass and
  route through their own shared helper) did not get
  `Change.suppression_rule` stamped, unlike a finding suppressed by the
  main pass — `suppression.suppressed_changes[]`'s
  `impact_assessment.decision.suppression_rule` was silently absent for
  these. Fixed by applying the same attribution in
  `post_processing._merge_findings_respecting_suppression`.
