<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`impact_assessment.root_cause_id`/`root_cause_display`/
  `impact_group_id`** (ADR-052, G29 Phase 3 follow-up): every finding's
  `impact_assessment` object (JSON, SARIF `properties.impactAssessment`, and
  everywhere else `ImpactAssessment` renders) now carries the same
  root-cause grouping decision `--report-mode root-cause` computes —
  independent of `report_mode`, unlike that mode's own dedicated
  `root_causes` array. Reuses the exact grouping key
  (`reporter_markdown._root_cause_key_and_display`) every format already
  shares, so a finding's `root_cause_id` here always matches its
  `root_causes[].root_cause_id` in JSON root-cause mode or its
  `properties.rootCauseId` in SARIF root-cause mode. Deliberately absent for
  an uncorrelated singleton finding (no `caused_by_type`, and not itself
  referenced by another finding's `caused_by_type`) so a plain finding's
  `impact_assessment` doesn't balloon with a root cause naming nothing but
  itself. `impact_group_id` is currently always identical to
  `root_cause_id` — a placeholder alias until Phase 6's
  `RootCauseCorrelator` gives it independent meaning. `report_schema_version`
  2.16 → 2.17.
