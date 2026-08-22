### Added

- **`abicheck.product_baseline.compare_product_directories`: per-library
  header roots.** `header_roots`/`old_header_roots`/`new_header_roots` now
  also accept a `{library_key: [roots...]}` mapping (keyed by the same
  identity `_discover_library_map` already produces — a path relative to
  that side's own root) instead of only a flat list applied to every
  library. Scopes a library's own public-header directories to that
  library alone, for a product whose libraries don't all share one header
  space — a library absent from the mapping gets no headers for that side
  rather than falling back to another library's roots. The previous flat
  shape keeps working unchanged. See
  `docs/contribute/plans/product-baseline-per-library-header-roots.md` for
  the design rationale and what's deliberately still out of scope (a
  shared header-AST cache across libraries, a cross-library type graph,
  and a manifest-level/on-disk encoding of the association).
