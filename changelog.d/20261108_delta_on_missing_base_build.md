### Added

- **`delta-on-missing-base: build` on the main Action** — when no stored
  default-branch snapshot is usable for the PR's exact base commit, the
  Action, after the gate comparison has run, checks that commit out in a
  worktree, builds it with the new `delta-base-build-command` input (with no
  token or Action inputs in its environment and its `GITHUB_ENV`/
  `GITHUB_PATH`/`GITHUB_OUTPUT` writes discarded), dumps it with the run's
  new-side evidence inputs, and renders the PR comment against it. A failed checkout, build or dump is named in the comment
  (`base_build_failed` / `base_dump_failed`, also in the new
  `delta-comment-outcome` output) and never changes the step's status, which
  stays the release gate's. A built base goes through the same operand-identity
  check as a stored snapshot, and its delta report's `delta_baseline` block
  (compare report schema 7.3) records `outcome: base_built`; a gate report can
  never carry it. See `docs/reference/main-snapshot.md`.

### Fixed

- **`delta-baseline` on an audit-only comparison** (no `old-library` /
  `abi-baseline`) no longer labels the gate's report as "Changes in this PR";
  it reads as `unsupported_operand`, like a directory/package comparison.
