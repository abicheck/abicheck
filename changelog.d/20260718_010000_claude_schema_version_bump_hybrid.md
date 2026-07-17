<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **Snapshot schema**: `SCHEMA_VERSION` is now `10` — `--ast-frontend hybrid`
  (G28 Phase 3) added `AbiSnapshot.ast_producer == "hybrid"` and the
  `fact_provenance` per-fact producer map without a version bump, so a
  pre-hybrid reader had no signal that a snapshot mixes castxml- and
  clang-backed declarations and could misread a producer coverage gap as a
  real change. The version mismatch now surfaces the existing
  forward-version `UserWarning` for such a reader instead of silence.
