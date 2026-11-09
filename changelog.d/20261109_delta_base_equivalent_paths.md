### Added

- **Path-equivalent ancestor for the PR delta** — `base-equivalent-paths` on
  `actions/fetch-main-snapshot` and `delta-base-equivalent-paths` on the main
  Action (opt-in; globs naming the paths that feed the ABI). When the PR's
  exact base commit has no default-branch snapshot (`base_not_snapshotted`,
  `snapshot_missing`, `base_expired`, `base_failed` — typical for a
  `paths:`-filtered snapshot workflow), the delta uses the snapshot of the
  base's nearest first-parent ancestor (at most 50 back) whose tree equals the
  base's under those paths. The ancestor's snapshot passes the same provenance
  checks, bound to the ancestor's SHA, with artifact and cache storage alike;
  a shallow checkout is deepened along the base. The walk stops at the first
  ancestor where a declared path differs. When nothing qualifies, the base's
  own outcome stands and the new `ancestor-walk` output names why
  (`paths_changed`, `limit_reached`, `history_ended`, `history_unavailable`,
  `lookup_failed`). The PR comment heading then reads "Changes in this PR vs
  `main@<ancestor>` (equivalent to `<base>`)", with the same wording on the
  direct and the fork-publisher path. The new `snapshot-sha` /
  `delta-snapshot-sha` outputs and the `delta_baseline` block's new
  `snapshot_sha` field (compare report schema 7.3) record which snapshot was
  used, separately from `base_sha`. The release gate still alone decides the
  exit code. See `docs/reference/main-snapshot.md`.
