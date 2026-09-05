### Fixed

- **`dump`/`compare --dry-run` now honors an explicit `--config` the same
  way the real run does when checking for the pre-captured-Bazel-jsonproto
  scoping hazard.** Previously the pre-flight bazel-target-scoping check
  behind `--build-info` + `--build-target` (or a `.abicheck.yml`
  `build.targets:`) could only ever see whatever `.abicheck.yml` was
  auto-discovered under `--sources`, even when an explicit `--config` named
  a different file — letting `--dry-run`'s resolved plan silently disagree
  with what `embed_build_source` would actually do at real-execution time.
  `InputSpec.build_config` (mirroring `scan`'s own `ScanRequest.build_config`)
  closes this residual of CLI cleanup phase two's PR C for both `dump` and
  `compare`, including `compare`'s inline `--old/new-sources` embed path.
  `build.query` still only ever executes from an explicit `--config`
  (ADR-032 D5), unchanged.
