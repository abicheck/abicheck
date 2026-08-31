### Added

- **`compare`/`dump`/`scan --against` now reject an unsatisfiable request
  before any extraction runs** (ADR-063 Phase 4): `AnalysisPlanner.resolve()`
  (`abicheck.workflows.plan`) builds an immutable `AnalysisPlan` from a
  `CompareRequest`/`DumpRequest`'s own requested inputs, and raises a new
  `PlanningError` (`abicheck.errors`) when a requirement no resolved
  collector/backend combination can satisfy. This closes a previously silent
  gap: `--build-target` combined with a pre-captured Bazel `aquery`/`cquery`
  `--build-info` jsonproto scoped nothing and produced no diagnostic at all
  (every action/target in the captured graph was collected regardless of the
  requested roots); it now raises a clean usage error naming the mismatch,
  with the documented workaround (a live `bazel query`, or a jsonproto
  already scoped to the desired targets) in the message.
  `AnalysisPlanner.resolve()` runs inside `resolve_compare_request`/
  `resolve_dump_request` — the one chokepoint every `compare`/`dump` front
  end (CLI, the typed Python API, the release/bundle fan-out) already
  resolves a request through — and `scan --against`'s own candidate
  resolution reuses the identical check directly, so all three commands get
  the same pre-flight guarantee.
