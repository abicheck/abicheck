### Removed

- `dump --build-query` and `dump --build-compile-db` are removed (CLI cleanup
  phase two, PR 3C / PR F). Both were CLI equivalents of the `.abicheck.yml`
  `build.query` / `build.compile_db` fields; put the values in a config file and
  pass it with `--config`:

  ```yaml
  build:
    query: cmake -S . -B build -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
    compile_db: build/compile_commands.json
  ```

  The old spellings are a hard usage error (`No such option`, exit 64) — there
  is no hidden alias. `--build-info`, `--build-target` and `--compile-db-filter`
  are unaffected: they are genuine per-run inputs, not project build settings.

### Changed

- An explicit `--config` is now the **only** way to authorize executing a
  `build.query`. The trust gate previously read `build_config is not None or
  build_query is not None`, so a bare `--build-query` on the command line was a
  second, independent way to mark an arbitrary command trusted to run; with the
  flag gone the gate has one term and there is no command-line-only route to
  execution. An auto-discovered `.abicheck.yml` is still never trusted to
  execute its query, and `dump --dry-run` still reports the exact argv, cwd,
  resulting compile-DB path, and why the query will or will not run.
