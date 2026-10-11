### Changed

- **Cheaper location pass, snapshot-cache write switch and trace phases** —
  clang location materialization walks siblings without per-node stack
  traffic; new `ABICHECK_SNAPSHOT_CACHE_WRITE=0` reads the snapshot cache
  without populating it, and `ABICHECK_MEMORY_TRACE` now marks the cache's
  `dump.cache.lookup`/`dump.cache.store` phases so warm runs can be attributed.
