### Added

- **`scan` and the GitHub Action now support `--build-target`/`build-target`
  Bazel root-target scoping (P0.2), matching `dump`'s existing flag.**
  `dump --build-target //:math` has scoped L3 evidence collection to an
  explicit root target and its transitive deps since an earlier change, but
  `scan`'s own `embed_build_source` call never threaded `build_targets`
  through at all — a `scan --build-target //:math --against` a
  `dump`-produced, target-scoped baseline silently ran an UNSCOPED
  workspace-wide query instead, capturing unrelated fixture/test targets
  and diverging from the baseline's own evidence (a real Bazel validation
  lab hit exactly this after adopting `dump --build-target`). Fixed by
  adding the identical `--build-target` CLI flag to `scan` (single-binary
  and `--artifact-set`), a matching `ScanRequest.build_targets` field for
  the typed Python API (`run_scan`/`run_scan_set`), and a new `build-target`
  GitHub Action input forwarded identically to both `dump` and `scan` mode.
