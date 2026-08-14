### Removed

- **`scan --allow-build-query` is removed (CLI audit PR 5/5).** Unlike
  `dump`, `scan` never reaches the ADR-032 external-extractor-manifest
  `QUERY_BUILD_SYSTEM` gate `--dump-manifest` uses, so on `scan` the flag
  only ever suppressed one advisory note about auto-enabling a build query
  from a trusted `--config`. `dump`'s own `--allow-build-query` (a real
  opt-in gating `QUERY_BUILD_SYSTEM`) is unaffected.

### Fixed

- **`--compiler-option`/`--gcc-option` mixed in one invocation now raises a
  usage error instead of silently dropping one side's tokens.** No merge
  order between the two spellings can be recovered without the original
  argv order Click doesn't expose, so combining them is now rejected with
  a clear message instead of the earlier "new wins entirely" precedence,
  which could silently drop legitimate `--gcc-option` tokens mid-migration.
