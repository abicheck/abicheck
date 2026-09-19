### Fixed

- **A retired type-spelling vocabulary no longer pins its compiled pattern.**
  The match cache is bounded by its *result* bytes, which are negligible next
  to a compiled alternation's, so a tiny result entry kept a multi-megabyte
  pattern charged to the registry long after its vocabulary was evicted and
  could never be looked up again. Measured: forty retired members left 13 KB
  of results pinning 287 MiB of patterns, past the registry's budget —
  whereupon vocabulary eviction began discarding *live* vocabularies hunting
  for space it could never recover, collapsing the cache to a single entry and
  recompiling every member's vocabulary from scratch. Retiring a vocabulary
  now takes its unreachable match results with it.
- **`clear_caches()` no longer wipes the shared pattern registry.** The
  registry is the sole strong-reference owner of every compiled pattern and a
  token is an `id()`, so clearing it while a cache still held entries made the
  pattern collectable and its address reusable — answering a lookup for one
  vocabulary with another's matches. Both cache clears already return every
  handle they took.
