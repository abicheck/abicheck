### Added

- **`abicheck.schemas.current(name)` — a read-only schema-version registry
  (ADR-055 D3)** — returns the current version abicheck emits for a
  persisted artifact (`"snapshot"`, `"compare"`, `"scan"`, `"aggregate"`,
  `"build-output"`, or `"run-plan"`), backed by the existing per-artifact
  constants (`serialization.SCHEMA_VERSION`,
  `schemas.REPORT_SCHEMA_VERSION`/`SCAN_SCHEMA_VERSION`,
  `aggregate.AGGREGATE_SCHEMA_VERSION`,
  `buildsource.build_output.BUILD_OUTPUT_SCHEMA`,
  `buildsource.run_plan.RUN_PLAN_SCHEMA`). A lookup facade only — it does
  not change any of those constants' current values or bump policy, and
  adds no compatibility metadata or cross-version lookup. Closes the class
  of bug that let `docs/use/python-api.md` claim snapshots carried
  `schema_version 8` long after the real value reached 17: a docs generator
  (or an external integrator) can now pull every current version number
  from one place instead of a human hand-copying one.
