### Fixed

- A `hybrid` header-backend dump through `compare`/`dump` (the default,
  dependency-scoped run) no longer lets the outer scope's parse-time
  dependency skip reach the two backend legs. The clang leg used to skip
  dependency declarations the castxml leg kept, so the merge reconciled two
  legs that disagreed, and each leg was stamped `dependency_scope="full"`
  over a filtered surface. A full-surface dump now always parses with neither
  the streaming pruner nor the parse-time skip, whatever encloses it
  (`workflows.run_dump_scope.extraction_scope`), and the CLI's hybrid path
  shares `dumper_hybrid.run_hybrid_dump` with `dumper.dump` instead of a
  hand-copied recursion.
