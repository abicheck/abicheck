### Added

- **Per-commit default-branch snapshots for PR deltas** — new
  `actions/main-snapshot` uploads one snapshot per default-branch commit as
  `abicheck-main-<sha>`, and `actions/fetch-main-snapshot` finds the one for
  a pull request's exact base commit (first parent of the merge commit,
  the merge-base in head mode, or the merge-queue base), verifying the
  uploading run. Every missing-base case is a named `outcome`
  (`base_pending`, `base_expired`, `snapshot_conflict`, ...), never a
  substitute commit. See `docs/reference/main-snapshot.md`.
