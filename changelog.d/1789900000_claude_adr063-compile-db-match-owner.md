### Changed

- ADR-063 Phase 1: the legacy `-p`/`--build-info` compile-database match
  (per-header matching with a union fallback) moved out of the CLI into
  `abicheck.workflows.artifact.compile_db_match`, and `execute_dump_request`
  now runs it itself from the new `DumpExecutionOptions.compile_db` /
  `compile_db_filter` fields. `dump --dry-run` calls the same function, so the
  preview and the real run cannot disagree about a match. The CLI-private
  helpers `cli_helpers_compare._resolve_build_context_flags`,
  `dry_run_compile_db_matched` and
  `frontends.cli.dump_build_context_preview.dry_run_build_context_preview`
  are removed, and `DumpExecutionOptions.legacy_compile_db_tokens` /
  `legacy_compile_db_matched` are replaced by `compile_db`. The dry-run line
  now reads "compile-db flags: N derived (matched|no match)".
  An unreadable or malformed compile database now fails the dump as an
  operational error (exit 1), as it did before, rather than a usage error.
