### Changed

- **Free-threaded live compares, cheaper location pass, snapshot-cache switch**
  — on a free-threaded (`python3.xt`) interpreter two live binaries now
  resolve as two threads instead of two forked children (same speed, less
  memory, no snapshot pickling); clang location materialization walks
  siblings without per-node stack traffic; new
  `ABICHECK_SNAPSHOT_CACHE_WRITE=0` reads the snapshot cache without
  populating it, and `ABICHECK_MEMORY_TRACE` now marks the cache's
  `dump.cache.lookup`/`dump.cache.store` phases so warm runs can be attributed.
