### Added

- **`.abicheck.yml` homes for `--debug-info` and the dependency walk** (ADR-091
  D4, 2026-10-31 amendment). `debug.info` takes a list of `--debug-info`
  values in the flag's own `[old=|new=]PATH` grammar. `deps.follow`,
  `deps.search_paths` and `deps.ld_library_path` back `--follow-deps`,
  `--search-path` and `--ld-library-path`. Each key also works with `--set`
  (and the Action's `set:`) and with the typed `config_overrides` of
  `CompareRequest`/`DumpRequest`. The flags stay and win over `--set`, which
  wins over the file. Relative paths resolve against the config file. `dump`
  reads only unscoped `debug.info` entries, and `--no-baseline` and stored
  BundleFacts comparisons refuse the keys by name, as they refuse the flags.
