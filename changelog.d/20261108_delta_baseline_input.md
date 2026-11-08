### Added

- **`delta-baseline: main` on the main Action** — the PR comment shows only
  the pull request's own changes, measured against the default-branch
  snapshot of its exact base commit (`actions/main-snapshot` /
  `actions/fetch-main-snapshot`), while the step's status still comes from the
  normal comparison against the release baseline. The comment says what it
  was measured against and what the gate decided; an unavailable snapshot is
  named, never read as "no changes". `delta-on-missing-base: fail` fails the
  job in a separate step. See `docs/reference/main-snapshot.md`.
