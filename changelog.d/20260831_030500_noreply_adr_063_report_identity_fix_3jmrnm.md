### Fixed

- **`aggregate_reports_dir` no longer raises `AttributeError` on every
  non-batch finding.** `resolve_change_identity`'s new `Change.entity_id`
  read (ADR-063 Phase 2) was unconditional, but `resolve_report_change_identity`'s
  `_ReportChangeView` adapter had no `entity_id` attribute — reports never
  serialize this carrier. Added `entity_id: EntityId | None` to the
  adapter, always `None`, matching `qualified_name`'s identical
  never-serialized treatment immediately above it.
