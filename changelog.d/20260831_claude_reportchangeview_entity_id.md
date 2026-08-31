### Fixed

- **`aggregate`'s cross-report finding reconciliation no longer crashes with
  `AttributeError: '_ReportChangeView' object has no attribute 'entity_id'`.**
  `resolve_change_identity` (ADR-063 Phase 2, `resolve_change_identity`
  consumes `Change.entity_id`) started reading `change.entity_id`
  unconditionally on every non-batch-shaped finding, but
  `workflows/aggregate/reconcile.py`'s `_ReportChangeView` — the read-back
  adapter `resolve_report_change_identity` builds from a report's own JSON
  — was never given a matching field. `_ReportChangeView` now carries
  `entity_id: EntityId | None = None`, permanently `None` since
  `_change_to_dict` never serializes it (same treatment already given to
  `qualified_name`): identity precision degrades gracefully instead of
  raising.

### Notes

- Introduced by the just-merged #957 (`resolve_change_identity` consumes
  `Change.entity_id`), which added the unconditional `change.entity_id` read
  without updating this call site's report-derived adapter. Verified: on
  `main` before this fix, `tests/test_aggregate.py`/
  `tests/test_aggregate_findings.py` fail with 105 `AttributeError`s; this
  fix brings the same run back to 308 passed (two new regression tests
  added in `tests/test_aggregate_migration_coverage.py`).
