### Performance

- **Header-parse cache keys no longer digest the whole system include tree** —
  the whole-snapshot, clang AST and castxml AST caches fold the compiler's
  system include directories by spelling, and validate an entry against a
  dependency manifest of the system headers its parse actually read (from the
  frontend's `-MD -MF` depfile, `extract/cache_dep_manifest.py`). A changed,
  deleted or newly shadowing system header still misses; a lookup now stats
  the ~150 headers a `<string>`+`<vector>` parse reads instead of ~4,400
  files, three times per dump and header-graph attach (ADR-062 D11). Existing
  cache entries are re-extracted once (`SNAPSHOT_CACHE_VERSION` 38, AST cache
  schemas bumped).
