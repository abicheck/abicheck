### Changed

- **The type-spelling pattern registry moved to its own module.**
  `abicheck/compare/spelling_pattern_registry.py` now owns compiled-pattern
  accounting and the cache-held references over a compiled pattern — who
  pays for a pattern's bytes, and how long the caches keep it reachable —
  leaving `spelling_match_cache` to decide only what to keep. It does not own
  the `re.Pattern`'s whole lifetime: a caller may hold its own reference, and
  releasing the last registry handle only drops the registry's charge and its
  own reference, not the object. The registry's
  lock is innermost in the module family's lock order: a cache takes it while
  holding its own, and nothing there calls back.
