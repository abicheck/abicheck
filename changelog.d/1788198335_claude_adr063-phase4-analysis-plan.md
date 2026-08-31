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
  the same pre-flight guarantee. A root-target scope declared in
  `.abicheck.yml`'s own `build.targets:` (rather than an explicit
  `--build-target`) is covered too, via a second check at the one place
  both sources are already merged into a single value
  (`buildsource.inline._maybe_collect_bazel_build_info`).

### Fixed

- **The typed `run_scan(ScanRequest(...))`/`run_scan_set(...)` API no longer
  raises a Click-specific `click.UsageError` for the `--build-target` +
  pre-captured Bazel jsonproto mismatch above.** `scan`'s own candidate
  resolution (`scan_engine._build_new_snapshot`) now raises the
  framework-neutral `PlanningError` at that point, same as `dump`/`compare`;
  `cli_scan.py`'s CLI front end translates it to a usage error (exit 64) at
  its own boundary. A caller using the Python API directly previously saw a
  web-framework-specific exception type leak out of a pure engine call.
