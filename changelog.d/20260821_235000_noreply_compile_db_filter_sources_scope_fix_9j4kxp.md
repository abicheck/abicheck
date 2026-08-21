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
- **`dump --compile-db-filter --build-info <dir>` now also catches a
  compile database in a conventional out-of-tree subdirectory** (e.g.
  `<dir>/build/compile_commands.json`), not only `<dir>/compile_commands.json`
  directly. The scope check's `--build-info` resolution previously only
  checked the immediate child, while the real header-AST fold already
  searches the same conventional build-directory hints `--sources`
  auto-discovery uses — so `--build-info` naming a project root whose
  database lives one level down resolved for the fold but not for the guard,
  reproducing the identical filtered-L2/unfiltered-L3 mismatch. Fixed on the
  CLI and the typed `DumpRequest`/`CompareRequest` API alike.
- **`dump --compile-db-filter --build-info <path>` now also catches a
  pre-captured `collect` pack directory and a Bazel `aquery`/`cquery`
  jsonproto**, not only a literal `compile_commands.json`. The L3→L2 fold
  narrows *whatever* build evidence a `--build-info` resolves to, regardless
  of its shape — but the scope guard only ever recognized a literal compile
  database, so a pack or Bazel jsonproto silently reproduced the identical
  filtered-L2/unfiltered-L3 mismatch with no error. Fixed on the CLI and the
  typed `DumpRequest`/`CompareRequest` API alike; a `--sources` tree with no
  discoverable compile database, resolved only through the zero-config
  inferred build-system query, remains a documented gap (see `AGENTS.md`).
- **`dump --compile-db-filter --sources <path>` now also catches a
  `--sources` tree that is itself a pre-captured `collect` pack (a classic
  `BuildSourcePack` or a Flow-2 `abicheck_inputs/` directory), not only an
  explicit `--build-info` pack.** L2 seeding folds such a `--sources` pack's
  own build evidence in whenever no `--build-info` is given, the identical
  way a `--build-info` pack does — but the scope guard's `--sources`
  fallback only ever looked for a literal `compile_commands.json`, which a
  pack directory doesn't carry at its root, so the mismatch reproduced with
  no error. Fixed on the CLI and the typed `DumpRequest`/`CompareRequest`
  API alike.
- **`dump --compile-db-filter --build-info <path>` combined with a
  `--sources` tree no longer raises a false-positive scope-mismatch error
  when `--build-info` itself doesn't resolve to anything.** A fix earlier in
  this same release made the scope check fall back to `--sources` whenever
  none of the `--build-info` checks matched — including when `--build-info`
  was genuinely given but unresolvable, which is wrong: the real L2 fold and
  L3 embed both surface that miss rather than silently falling back to a
  `--sources`-discovered database instead. Fixed by returning cleanly (no
  usage error) once an explicit, given `--build-info` resolves to nothing,
  matching the real resolver's own precedence.
- **`dump --compile-db-filter --build-info <path>` now also catches a
  `--build-info` naming a Flow-2 `abicheck_inputs/` pack**, not only a
  classic `BuildSourcePack`. An earlier fix in this same release recognized
  a `--build-info` pack directory, but only checked for the classic pack
  format — the L2 seed and the L3 embed both also recognize a Flow-2
  `abicheck_inputs/` directory identically, so that shape silently
  reproduced the same filtered-L2/unfiltered-L3 mismatch. Fixed on the CLI
  and the typed `DumpRequest`/`CompareRequest` API alike.
