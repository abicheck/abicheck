### Changed

- **The type-spelling pattern registry moved to its own module.**
  `abicheck/compare/spelling_pattern_registry.py` now owns compiled-pattern
  accounting and lifetime — who pays for a pattern and when it is freed —
  leaving `spelling_match_cache` to decide only what to keep. The registry's
  lock is innermost in the module family's lock order: a cache takes it while
  holding its own, and nothing there calls back.
