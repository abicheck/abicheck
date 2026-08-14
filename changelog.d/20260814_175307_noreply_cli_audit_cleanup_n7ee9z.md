### Removed

- **`scan --allow-build-query` is removed (CLI audit PR 5/5).** Unlike
  `dump`, `scan` never reaches the ADR-032 external-extractor-manifest
  `QUERY_BUILD_SYSTEM` gate `--dump-manifest` uses, so on `scan` the flag
  only ever suppressed one advisory note about auto-enabling a build query
  from a trusted `--config`. `dump`'s own `--allow-build-query` (a real
  opt-in gating `QUERY_BUILD_SYSTEM`) is unaffected.
