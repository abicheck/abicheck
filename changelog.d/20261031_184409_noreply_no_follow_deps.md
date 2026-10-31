### Added

- **`--no-follow-deps` on `compare` and `dump`** — `--follow-deps` is now a
  boolean pair, so a configured `deps.follow: true` can be turned off for one
  run. With neither half given, `deps.follow` decides (default off). The
  Action's `follow-deps` input now defaults to empty, which defers to the
  config the same way; an explicit `false` forwards `--no-follow-deps`.
  `--no-baseline` and stored-BundleFacts comparisons no longer refuse a
  configured `deps:` block when `--no-follow-deps` turns the walk off.
