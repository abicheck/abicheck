### Added

- **`delta-storage: cache` on the main Action** — `delta-baseline: main` can
  now read the default-branch snapshot `actions/main-snapshot` saved with
  `storage: cache`, so a pull-request job needs neither `actions: read` nor
  `delta-snapshot-workflow` (fork pull requests of a private repository). It
  runs `actions/fetch-main-snapshot`'s own cache path: an exact-key restore,
  the same entry checks and the same refusals (`untrusted_source` for a pull
  request into another branch and for `merge_group`). Every unavailable entry
  is a named outcome that `delta-on-missing-base: build` and `fail` act on;
  the release gate still alone decides the step's status. `artifact` stays
  the default. See `docs/reference/main-snapshot.md`.
