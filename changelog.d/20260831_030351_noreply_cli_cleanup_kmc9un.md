### Fixed

- **`aggregate`'s cross-report finding reconciliation no longer crashes on any finding.** ADR-063 Phase 2's `resolve_change_identity` started reading `Change.entity_id` unconditionally, but the report-read-back adapter (`_ReportChangeView`, used by `resolve_report_change_identity`) had no such field, so `AttributeError` broke every `aggregate` finding-matrix computation. Ported here from `main` since this PR's branch predates the regression and needs the fix present once merged.
