### Fixed

- **A memoized function's cache counters no longer go negative after a clear** — clearing a
  memoized function stored in `functools.lru_cache` also zeroed that cache's own counters,
  which `cache_stats()` reads as deltas from a baseline, so a stats reset followed by a clear
  reported negative hits and misses. A clear now drops entries and keeps the counted history,
  as the dict-backed memo always did, so `cache_stats()` and the call-count budgets read the
  same through either store.
