### Fixed

- **`dump --compile-db-filter` combined with `--sources` (no `--build-info`)
  no longer silently produces a mismatched snapshot.** The scope check
  guarding `--compile-db-filter` (`--compile-db-filter scopes the L2 header
  parse only...`) only ever resolved the compile database from an explicit
  `--build-info`. A `--sources` tree whose own `compile_commands.json` is
  auto-discovered (no `--build-info` given at all) reaches the identical
  compile database through both the L2 header-AST fold *and* the L3
  build-evidence embed — but only the fold honors `--compile-db-filter` — so
  the guard never fired for that shape, and the resulting snapshot carried L3
  build evidence for translation units the filtered L2 header parse never
  saw. `dump --sources tree/ --compile-db-filter foo.cpp --depth build` (no
  `--build-info`) now correctly rejects the combination the same way it
  already did with an explicit `--build-info`. Also closes the identical gap
  on the typed `DumpRequest`/`CompareRequest` API surfaces added in this same
  release.
