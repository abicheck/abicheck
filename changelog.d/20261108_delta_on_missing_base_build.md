### Added

- **`delta-on-missing-base: build` on the main Action** — when no stored
  default-branch snapshot is usable for the PR's exact base commit, the
  Action checks that commit out in a worktree, builds it with the new
  `delta-base-build-command` input (in its own step, with no token), dumps it
  with the run's new-side evidence inputs, and renders the PR comment against
  it. A failed checkout, build or dump is named in the comment
  (`base_build_failed` / `base_dump_failed`, also in the new
  `delta-comment-outcome` output) and never changes the step's status, which
  stays the release gate's. See `docs/reference/main-snapshot.md`.

### Fixed

- **`delta-baseline` on an audit-only comparison** (no `old-library` /
  `abi-baseline`) no longer labels the gate's report as "Changes in this PR";
  it reads as `unsupported_operand`, like a directory/package comparison.
