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

- Unrelated to this PR's own change (`architecture/modules.yaml`
  reclassification) — the bug was introduced on `main` by the just-merged
  `resolve_change_identity`-consumes-`entity_id` change and reproduces
  identically on `main` alone; ported here because CI's merge-ref build
  surfaces it against this branch too. Verified: reverting to `main`'s
  `finding_identity.py` locally reproduced all 105 failures in
  `tests/test_aggregate.py`/`tests/test_aggregate_findings.py`; this fix
  brings the same run back to 306 passed.
