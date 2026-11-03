### Changed

- **A header-dialect difference no user chose no longer refuses the
  comparison** — the header parse takes its `-std=` from each binary's build,
  so comparing a C++17 build with a C++20 build of the same library was
  refused as `profile_mismatch` (exit 16, no verdict). When the language
  standard is the only difference, both sides derived it automatically, and
  at least one side's standard was implied by its binary, `compare` now gives
  a verdict and reports the `declaration` and `layout` comparability
  dimensions unverified, with a warning naming both standards and the inputs
  that complete the check (`--set compile.std=<std>` on both dumps,
  `--build-info` for the build-floor question). Explicit pins, a split caused
  only by the header-content heuristic, legacy snapshots and any other
  differing field still refuse (ADR-050 amendment, 2026-11-03).
