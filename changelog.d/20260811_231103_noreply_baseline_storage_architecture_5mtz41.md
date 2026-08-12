<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`publish-baseline.yml`'s release-asset upload no longer `--clobber`s a
  published `release-contract` asset.** The reusable workflow that publishes
  a profile's baseline-set to a GitHub Release now uploads plainly when no
  asset of that name exists yet, treats a re-upload with identical
  *normalized* baseline content (library + snapshot/staged-binary digests,
  excluding volatile fields like `created_at`) as a safe no-op retry, and
  hard-fails instead of silently overwriting when an existing asset's
  normalized content differs from what the current run built —
  `release-contract` is documented as immutable once published, and the
  previous unconditional `--clobber` contradicted that.
