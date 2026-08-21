### Fixed

- **`dump` no longer applies the legacy compile-database auto-match *and* the
  P0.3 L3→L2 fold to the same `--build-info` database, which made its own
  snapshots non-comparable with every other resolver's.** Both mechanisms read
  the compile database `--build-info` names, and `dump` ran both, so the same
  evidence was recorded twice: a build with `-DFOO=1` produced
  `macro_ops == [["D","FOO=1"],["D","FOO=1"]]` where `compare`'s implicit
  dump, `scan`'s candidate and the typed `DumpRequest` API each record one
  entry. Worse, a build carrying an extra `-I<dep>` produced
  `include_sequence == []`: the legacy match supplied that directory as
  *explicit* context before the L2 include seed ran, so the seed correctly
  declined to seed a directory explicit context already provided, the
  directory reached the parse through `gcc_option_tokens`, and
  `gcc_option_tokens` contributes no `declared_includes` slot — the sole
  source `include_sequence` is built from. Either shape gives the written
  snapshot a `profile_fingerprint` no other path reproduces, so a
  `scan --against` that `dump` baseline refused an unchanged library as
  `NOT_COMPARABLE` (exit 6) for a real, ordinary build shape, reproduced end
  to end with a real `g++` build and a real clang header parse.

  When the fold resolves a compile context for the headers being parsed, it
  is now the sole source of compile-database-derived context and the legacy
  match's own derived flags are dropped rather than stacked on top of it.
  When the fold does not apply — no `--build-info`, or a header no compile
  unit matches — the legacy match still runs and still applies, exactly as
  before; only the overlap is removed. `--compile-db-filter` is unaffected:
  it reaches the shared fold too, so narrowing still narrows what the fold
  sees.
