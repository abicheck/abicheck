<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **`snapshot_cache._cache_key` stopped sorting the caller-given
  `headers`/`includes` lists before hashing them.** Header/include order is
  a real, load-bearing input — the same "`-I` search-precedence order is a
  real compile difference" rule `comparability.py`'s `profile_fingerprint`
  (ADR-050 D1) already enforces for the comparability gate — so two dumps
  requesting the same headers/includes in a different order can legitimately
  resolve to a different snapshot (e.g. `-I a -I b` vs `-I b -I a` with a
  same-named header in both, or a macro one header defines before another is
  included). Sorting collapsed both orders to the same cache key, letting a
  warm whole-snapshot disk cache silently serve the wrong order's result.
  `_SNAPSHOT_CACHE_VERSION` is bumped `3` → `4` so a previously-stored,
  order-collapsed cache entry is never replayed as if it were order-aware.
  The transitively-discovered `hash_files` set (headers reached only via a
  directory input) is unaffected — it stays a sorted, unordered content
  aggregate, not a caller-ordered sequence.
