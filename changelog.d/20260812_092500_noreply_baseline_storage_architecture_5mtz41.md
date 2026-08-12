### Fixed

- **`actions/stage-baseline`'s default `.tar.zst` packaging** no longer
  requires an undeclared `zstd` executable on the runner — `tar --zstd`
  shells out to a separate `zstd` binary, and this composite Action (unlike
  `actions/baseline`) has no dependency-install step, so a minimal or
  self-hosted runner without it pre-installed would otherwise hard-fail on
  the default configuration alone. Falls back to Python's `zstandard`
  package (already an abicheck core dependency) when the `zstd` CLI isn't
  on `PATH`.
- **`test-baseline-publish-e2e.yml`**'s build-output artifact name no longer
  double-prefixes its own profile id — the fixture's "publish" job could
  never find its build output, so this live workflow always failed before
  testing either the fresh-upload or safe-retry path.
