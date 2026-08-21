### Fixed

- **`--compile-db-filter` no longer silently mismatches an ordinary relative
  source path that happens to share its leading segment with the compile
  unit's own `directory`** (e.g. `directory="build"`,
  `file="build/a.cpp"`). `source_matches_filter()`'s `Path.is_relative_to`
  check — added to avoid double-joining an already-anchored *redacted*
  path (ADR-032 D7) — could not distinguish that genuine case from an
  ordinary directory/file naming coincidence, since both are lexically
  identical prefix matches. Real `CompileEntry`/compilation-database
  semantics still join the coincidental case unconditionally
  (`build/build/a.cpp`, not `build/a.cpp`), so a filter naming the
  correctly-joined spelling matched nothing and silently fell back to
  selecting every compile unit. Fixed by testing both interpretations (the
  joined path and the raw, unjoined path) as candidates against the filter
  pattern instead of guessing which applies — a filter that would have
  matched under either reading now matches under both, so this can only
  widen what's selected, never silently exclude the correct translation
  unit either way.
