<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
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
