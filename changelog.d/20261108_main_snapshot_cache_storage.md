### Added

- **`storage: cache` for `actions/main-snapshot` and
  `actions/fetch-main-snapshot`** — the default-branch snapshot a PR delta is
  measured against can now live in the Actions cache under the exact key
  `abicheck-main-snapshot-<full sha>[-<profile>]` instead of a workflow
  artifact. Fork PRs of private repositories, whose token may lack
  `actions: read`, can read it. The producer saves only from the default
  branch; the consumer restores the exact key (never `restore-keys`), applies
  the artifact path's size limits, and checks a self-reported
  `provenance.json` (repository, ref, commit, key). Unlike the artifact path
  the producing workflow run is not verified — see
  `docs/reference/main-snapshot.md`. Outcomes reuse the existing vocabulary
  (a miss is `base_not_snapshotted`). New module
  `abicheck/frontends/action/main_snapshot_cache.py`.
