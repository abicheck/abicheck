### Added

- **`InputSpec.compile_db_filter` (typed `dump`/`compare` API) mirrors `dump
  --compile-db-filter`** (CLI cleanup phase two, PR 3A investigation). Until
  now the typed pipeline's own L2 header-AST context
  (`_seeded_includes_and_compile_context`, the P0.3 L3→L2 fold) always
  resolved from the *whole*, unfiltered compile database, even though the
  native `dump` CLI's own `--compile-db-filter` had already been threaded
  into that same shared fold (`resolve_header_compile_context`/
  `seed_includes_and_fold_compile_context`'s `source_filter`). Setting this
  new field now narrows the fold and the ADR-039 build-context collector
  identically to the CLI, and `service_dump_pipeline.resolve_dump_request`
  mirrors the CLI's own `compile_db_filter_scope_error` refusal — a filter
  combined with a resolved collect mode that also embeds L3 build evidence
  is rejected as a usage error rather than silently collected unfiltered.
  `None` (the default) is a no-op for every existing caller. `dump_cmd`
  forwards its own `--compile-db-filter` value into the `DumpRequest` it
  builds for `--dry-run`, so a dry run now reports the identical refusal
  the real CLI already raised directly.
