### Fixed

- **`--compile-db-filter` silently matched nothing (and fell back to every
  compile unit) against a redacted compile database.** `CompileDbAdapter`
  (ADR-032 D7) redacts both a compile unit's `source` and `directory` to the
  identical `~/...` placeholder before either reaches `build_context.
  source_matches_filter()`. The redacted `source` (e.g. `~/proj/a.cpp`) is
  not `Path.is_absolute()`, so the matcher treated it as an ordinary
  relative filename and joined it onto the redacted `directory` again,
  producing `~/proj/~/proj/a.cpp` — which the filter glob never matches,
  so the ambiguity-resolving fallback silently restored every unit instead
  of narrowing to the one named by `--compile-db-filter`. `source_matches_
  filter()` now checks `Path.is_relative_to()` before joining, so a
  redacted source already anchored under its redacted directory is used
  as-is (Codex review on #814).
