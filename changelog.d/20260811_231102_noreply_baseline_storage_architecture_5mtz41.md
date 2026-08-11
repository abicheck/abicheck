<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`resolve-baseline`'s new `expected-project-ref` input and `wrong_project_ref`
  outcome** — catches an `accepted-main` Actions-cache `restore-keys` prefix
  match resolving to a newer default-branch commit than the caller actually
  wanted (e.g. a PR gate that should compare against its own base SHA).
  Passing `expected-project-ref` requires the resolved baseline-set's
  `manifest.json` `project_ref` to match exactly, or the check fails closed
  with the new `wrong_project_ref` outcome instead of silently resolving
  against the wrong commit. Omitting the input (the default) skips the
  check, unchanged from before.

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
