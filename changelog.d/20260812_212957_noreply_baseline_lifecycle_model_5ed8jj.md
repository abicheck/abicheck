### Added

- **Generator provenance and generation-aware asset naming/cache keys for
  baseline-sets.** `manifest.json` now also records a `generator` block
  (`{"tool": "abicheck", "version", "git_sha"?, "action_ref"?}`) — purely
  informational, never compared by any resolve/freshness check.
  `actions/stage-baseline`'s `asset-name-template` accepts an opt-in
  `{generation}` placeholder, and `update-main-baseline.yml` automatically
  folds `-g<generation>` into its accepted-main cache key-prefix when
  `baseline-generation` is set, so two different scanner-compatibility
  generations never share one cache-key namespace. See "Scanner upgrades
  and baseline generations" in `docs/use/baseline-management.md`.
