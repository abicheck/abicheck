<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--report-mode root-cause`** (ADR-051, G29 Phase 3 slice 3): groups
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
  suppressions** (ADR-051 follow-up): a suppression rule that suppressed a
  finding built by `DetectCppPatterns`/`DetectTemplatePatterns`/
  `DetectNamespacePatterns` (which run after the main suppression pass and
  route through their own shared helper) did not get
  `Change.suppression_rule` stamped, unlike a finding suppressed by the
  main pass — `suppression.suppressed_changes[]`'s
  `impact_assessment.decision.suppression_rule` was silently absent for
  these. Fixed by applying the same attribution in
  `post_processing._merge_findings_respecting_suppression`.

- **`--report-mode root-cause` grouping edge cases** (ADR-051 follow-up):
  two or more findings that both lack `caused_by_type` and carry an empty
  `symbol` (e.g. `SOURCE_FACT_COVERAGE_INCOMPLETE`,
  `SOURCE_BINARY_PROVENANCE_MISMATCH`) no longer collapse into one fake
  shared root cause keyed on `""` — each now keys uniquely, matching the
  contract that only `caused_by_type` correlates findings. Separately, a
  `--used-by`/`--required-symbol` scoped gate whose only failure is a
  synthetic scoped-only change or missing-contract label (folded into
  `changes[]` after `root_causes` is built) is now folded into
  `root_causes`/`root_cause_count` too, instead of only the flat
  `changes[]`. Separately, two *independent* findings that merely share a
  non-empty symbol with no `caused_by_type` correlation (e.g. a
  `func_return_changed` and a `func_params_changed` finding both on `foo`)
  no longer wrongly collapse into one root cause either — a symbol is
  only used as a grouping key when some other finding's `caused_by_type`
  actually names it; otherwise each finding keys uniquely, with the
  symbol still shown as its own singleton group's display root.
