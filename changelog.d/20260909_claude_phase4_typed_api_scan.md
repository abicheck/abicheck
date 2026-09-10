### Removed

- **`ScanRequest`/`ScanResult` and the `scan` typed-API entry points are
  gone** (ADR-068 Phase 4's typed-API slice; ADR-055's amendment carries the
  field-by-field ledger). `abicheck.service.run_scan`, `run_audit`,
  `run_scan_set`, `run_scan_subprocess`, `run_scan_set_subprocess`, and the
  `ScanRequest`/`ScanResult`/`ScanArtifactResult`/`ScanSetResult`/`Budget`/
  `LayerResult` types they used are removed: `CompareRequest` ->
  `CompareResult` is the one typed request/result contract. A Python caller
  that drove `run_scan` should build a `CompareRequest` and call
  `run_compare_request`; one that needs the one-sided audit should invoke the
  `abicheck scan` CLI, which is unchanged and still emits the same
  `--format json` report. `abicheck.service.estimate_scan` survives as the
  dry-run cost model but now takes an `InputSpec` plus the run-scoped level
  arguments instead of a request object.
- **`scan --artifact-set`/`--manifest` are retired** (ADR-068's second
  2026-09-09 amendment, ruling (b) — a breaking change with no deprecation
  window). The capability is not abandoned: it returns as
  `compare --no-baseline DIR` once ADR-065 S3's package component inventories
  make its per-member selection and coverage accounting reproducible. Run one
  `scan` per library meanwhile. The GitHub Action's `new-library-set` input
  now fails the step with an explicit error naming that prerequisite rather
  than silently auditing less.
- **`scan --risk-rules` and risk-driven `auto` depth selection are retired**
  (same ruling). **Omitting `--depth` on `scan` now resolves to the fixed
  `headers` rung** — the same default `compare` has always used — instead of
  being scored from the changed paths. This is a behaviour change in both
  directions and worth checking if you run `scan` without `--depth`: a
  low-risk seeded run used to resolve to `s0`/off, and an *unseeded* run used
  to fall back to the `--mode` preset's `(s5, source)`, i.e. a full source
  replay. Pin `--depth source` (or `build`) explicitly to ask for source
  evidence on every run. The risk score itself is still computed and
  reported; it just no longer selects an evidence level.
- **`scan --build-target` is retired** (same ruling); `dump --build-target` is
  unchanged. It narrowed which build target's evidence collection used when a
  compile database names several — a precision knob, not a correctness floor.

### Added

- **`CompareRequest.allow_build_query`** — the one `ScanRequest` field the
  audit above found genuinely missing from `CompareRequest`. `False` (the
  default) keeps the standing "never execute a build system as a side effect
  of resolving an input" rule, so every existing request is unchanged;
  `True` asserts the same operator consent `dump --allow-build-query`
  expresses and authorizes a trusted `.abicheck.yml`'s executable
  `build.query` field only. The request's per-side `InputSpec.build_config`
  is now read on the `compare` path alongside it.

### Changed

- **`scan_schema_version` is `1.31`** and marks exactly one shape again:
  `scan --format json`'s own CLI envelope. The typed `ScanResult`/
  `ScanSetResult` Python envelopes it also stamped through `1.30` no longer
  exist. The CLI shape itself is unchanged by the bump.
