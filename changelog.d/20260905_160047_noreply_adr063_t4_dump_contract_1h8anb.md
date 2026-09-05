### Changed

- **`dump --dry-run` now previews its resolved execution options.** ADR-063
  Track T4 ("Dump request contract") folds `execute_dump_request`'s nine
  out-of-band execution kwargs (`build_config`, `allow_build_query`, the
  legacy `-p`/`--compile-db` auto-match's derived flags, `seed_collect_mode`,
  `source_frontend_from_folded_context`, ...) onto
  `ResolvedDumpRequest.execution_options` itself, instead of only ever being
  assembled fresh at `execute_dump_request`'s own call boundary. A new
  "Execution options" section on `dump --dry-run`'s report shows what a real
  run would pass. `frontends.cli.dump_execute.execute_dump_cli_run` no
  longer takes these nine values as separate parameters — it reads them off
  the resolved request it is handed.
