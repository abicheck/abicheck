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
  pre-captured Bazel jsonproto mismatch above.** `scan`'s own pre-flight check
  now raises the framework-neutral `PlanningError`, same as `dump`/`compare`;
  `cli_scan.py`'s CLI front end translates it to a usage error (exit 64) at
  its own boundary. A caller using the Python API directly previously saw a
  web-framework-specific exception type leak out of a pure engine call.
- **The same check now runs once, at the top of `scan_engine.run_scan_core`,
  before its S3 pattern-scan/points-of-interest work** — not only inside
  `_build_new_snapshot`, which that work already precedes. A typed
  `run_scan()`/`run_scan_subprocess()` caller has no `cli_scan.py` pre-flight
  ahead of `run_scan_core` the way the CLI does, so an unsupported request
  previously paid for that (cheap but real) work before being rejected.
  Moving the check also surfaced and fixed a real bug: the old
  `_build_new_snapshot`-only check had no `depth=binary` exemption at all, so
  a typed `ScanRequest(depth="binary", ...)` combined with `build_targets`
  and a pre-captured jsonproto was wrongly rejected even though that depth's
  `collect_mode` never consults either value (the same false-positive class
  already fixed for `dump`/`compare`'s `AnalysisPlanner` check).
- **`scan --artifact-set`'s own pre-flight check gained the identical
  `depth=binary` exemption its single-binary sibling already had.** Unlike
  the single-binary path (whose `_normalize_depth_inputs` prunes `build_info`
  to `None` at that depth before the check runs), `_run_artifact_set` checked
  the raw, unpruned inputs directly, so `scan --artifact-set --depth binary
  --build-target ... --build-info <precaptured jsonproto>` was wrongly
  rejected even though that depth never consults either value.
- **A `--build-target`/`.abicheck.yml` `build.targets:` scoping mismatch
  against a pre-captured Bazel jsonproto is no longer silently swallowed at
  `--depth headers`.** `buildsource.l2_seed`'s three L2-seed/compile-context
  helpers each wrap their own `collect_inline_pack` call in a broad
  best-effort `except Exception` (by design, for an ordinary collection
  failure) — but at `--depth headers`, `embed_build_source`'s own real check
  never even runs (that depth's `collect_mode` is `"off"`), making this
  L2-seed call the *only* place the mismatch could be detected at all, and
  the broad catch swallowed it with no diagnostic. Fixed by carving the new
  `ValidationError` out of the catch-all in all three helpers, mirroring
  their pre-existing `HeaderCompileContextAmbiguousError` carve-out for the
  same reason: a deliberate usage error must propagate, not degrade silently.
- **`run_scan_core`'s own pre-flight check no longer wrongly exempts
  `--depth headers`.** The check's exemption (added above) was keyed only on
  the resolved `collect_mode` mapping to no collection layers — true for both
  `--depth binary` and `--depth headers`, but only `--depth binary` also
  clears the header list, which is the actual reason `build_info` is never
  consulted anywhere for that depth. A headers-only scan still runs the
  L2-seed's own independent `build_info`-consuming pass, so an explicit
  `build_targets` combined with a pre-captured jsonproto at `--depth headers`
  now raises the framework-neutral `PlanningError` up front (before any
  work), instead of only being caught later — and then leaking as
  `click.ClickException` — deep inside that L2-seed call.
- **`service_scan.run_scan_set` (the typed `--artifact-set` entry point) now
  runs the same Bazel-scoping pre-flight check before any per-member work
  starts**, not only inside each member's own `run_scan_core` call. A
  mismatched request previously incurred `discover_artifact_set()`,
  `check_artifact_set_soname_collisions()`, and `artifact_set_member_exports()`
  for every member before being rejected. The check's exemption logic (the
  `depth=binary` header-clearing rule above) is now shared by both
  `run_scan_core` and `run_scan_set` through one function,
  `workflows.plan.scan_bazel_scoping_failure()`, instead of two
  independently-maintained copies.
- **`dump`/`compare`'s own `AnalysisPlanner` pre-flight check no longer
  wrongly exempts `depth="binary"` when `DumpRequest.resolved_collect_mode`
  overrides what that depth alone would resolve to.** `resolved_collect_mode`
  (a private CLI hook `compare`'s own implicit-dump path uses so a real run
  doesn't re-derive a possibly-different collect mode than the one already
  resolved for the pair) wins over `depth` at execution time
  (`resolve_dump_request_evidence`), so a request combining `depth="binary"`
  with an explicit override to a real collection mode (e.g. `"build"`) still
  consults `build_info` for real. The pre-flight check now consults the
  override first when present, falling back to the raw-depth-only rule only
  when it's unset — `compare` is unaffected, since `CompareRequest` has no
  such field.
- **`scan --artifact-set`'s own CLI pre-flight check now runs before
  discovery, not after.** `cli_scan._run_artifact_set` ran its Bazel-scoping
  check only after `_resolve_artifact_set_paths()`/`discover_artifact_set()`
  had already traversed a directory and statted/format-validated every
  explicit member — so an invalid member's own error could mask the
  request's intended usage error, and a mismatched request paid for real
  discovery work before being rejected anyway (the CLI-level sibling of the
  `run_scan_set()` fix above). Moved to the top of the function, before any
  discovery work starts.
- **`dump`/`compare`'s `AnalysisPlanner` check no longer exempts a genuine
  ``"off"`` collect mode when real headers are present.** A `"off"` collect
  mode (from raw `depth="binary"`, or an explicit `resolved_collect_mode="off"`
  override) does not by itself mean `build_info` is never consulted: the L2
  seed's own independent header-seeding pass
  (`_seeded_includes_and_compile_context`/`collect_inline_pack`) still
  consumes it whenever real headers are present, regardless of collect mode
  — mirroring the identical gap already fixed for `scan_bazel_scoping_failure`.
  `SidePlan` now carries the side's raw `headers`, and the check exempts
  `"off"` only when there is no header-seeding consumer (`depth="binary"`
  still clears headers to empty independent of any override, so that
  clearing is folded in rather than re-derived).
