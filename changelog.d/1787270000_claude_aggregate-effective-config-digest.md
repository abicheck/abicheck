### Added

- **Duplication-and-convergence plan, Phase 0 item 6**: `aggregate`'s
  per-target report now carries its own `effective_config_digest` through
  into `targets[].effective_config_digest`, read straight off each
  already-persisted per-target report (`compare`/`scan`/`release` reports
  already stamp this — `aggregate` previously dropped it when rolling
  per-target reports up, including for a `scan` report's own digest, which
  is nested under `diff`/`report.diff` rather than the document root).
  Additive only: `None`/omitted for a report that carries no digest (an
  unavailable target, or one written with `include_exit_decision=False`,
  e.g. `compat check`'s own reports) — no change to any existing field.
  `AGGREGATE_SCHEMA_VERSION` bumped `1.6` -> `1.7` to publish the new field
  in `abicheck/schemas/aggregate_report.schema.json`.
