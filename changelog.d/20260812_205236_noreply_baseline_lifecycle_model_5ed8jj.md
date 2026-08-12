### Added

- **`baseline_generation`: a scanner-compatibility identity for baselines,
  separate from the abicheck package version.** `actions/baseline`'s new
  `baseline-generation` input records a caller-assigned integer in the
  produced `manifest.json`, for the subset of abicheck upgrades that
  actually invalidate an existing baseline (a fixed or newly-extracted
  fact, a changed normalization/hash recipe, a schema bump) — most upgrades
  (report format, policy/severity, a new detector over already-collected
  facts) don't need one, and tying rebaselining to the package version
  alone conflated the two. `actions/resolve-baseline`'s new
  `expected-baseline-generation` input (also threaded through
  `actions/check-target`, `check-single.yml`, and `check-project.yml`)
  requires the resolved baseline-set to carry exactly that generation,
  failing closed with a new `stale_generation` outcome otherwise — a
  baseline can pass every other check (`snapshot_schema`, `profile`,
  `project_ref`, digests) while still having been produced by an
  incompatible scanner epoch. A generation change is also its own
  `refresh-required` reason from `actions/baseline` when a
  `previous-manifest` is given. See "Scanner upgrades and baseline
  generations" in `docs/use/baseline-management.md`.
