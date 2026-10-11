### Changed

- **Faster dumps and live compares** — the DWARF compile-unit walk now runs
  with a raised gen-0 garbage-collection threshold on GIL builds (measured
  13-15% off a large dump, identical output), clang location materialization
  walks siblings without per-node stack traffic, and on a free-threaded
  (`python3.xt`) interpreter two live binaries resolve as two threads instead
  of two forked children (same speed, less memory, no snapshot pickling).
  New `ABICHECK_SNAPSHOT_CACHE_WRITE=0` reads the snapshot cache without
  populating it, and `ABICHECK_MEMORY_TRACE` now marks the cache's
  `dump.cache.lookup`/`dump.cache.store` phases so warm runs can be attributed.
