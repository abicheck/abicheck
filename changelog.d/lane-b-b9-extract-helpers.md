### Changed

- Internal (ADR-061): moved six more root extract helpers to their layer and
  deleted the old paths, with no re-export: `dumper_ast_config` ->
  `extract.headers.ast_config`, `dumper_ast_config_cpp20_chains` ->
  `extract.headers.ast_config_cpp20_chains`, `dumper_castxml` ->
  `extract.headers.castxml.dumper`, `dumper_debug` -> `extract.debug_dump`,
  `dumper_elf_symbols` -> `extract.elf_symbol_classify`, and
  `dumper_layout_backfill` -> `extract.dwarf_layout_backfill`. The
  `cache-ast-dumps` Action now hashes the moved `ast_config` file for its
  cache key. No behaviour change.
