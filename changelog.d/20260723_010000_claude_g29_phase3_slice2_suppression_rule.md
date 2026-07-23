<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`impact_assessment.decision.suppression_rule` in JSON/SARIF reports**
  (ADR-050 follow-up, G29 Phase 3 slice 2): a suppressed finding's
  `suppression.suppressed_changes[]` entry now names the suppression rule
  that actually suppressed it (its `label`, falling back to `reason`).
  `suppression.SuppressionOutcome` gained a `matched_rule` field, and
  `Change` gained a matching `suppression_rule` field, set wherever a change
  moves into `DiffResult.suppressed_changes`
  (`checker._filter_suppressed_changes`/`_filter_pattern_synthetic`,
  `post_processing.ApplySuppression`).
