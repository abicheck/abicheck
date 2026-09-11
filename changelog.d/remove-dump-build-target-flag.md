### Removed

- `dump --build-target` is removed (CLI cleanup, the `--build-target`
  retirement — ADR-068 Phase 6 resolved the `scan`-lifetime routing hazard
  that had deferred this since PR 3C/3F). It was the CLI equivalent of
  `.abicheck.yml`'s `build.targets` field; put the value in a config file
  and pass it with `--config`:

  ```yaml
  build:
    system: bazel
    targets:
      - //:math
  ```

  The old spelling is a hard usage error (`No such option`, exit 64) — there
  is no hidden alias. `--build-info` and `--compile-db-filter` are
  unaffected: they are genuine per-run inputs, not project build settings.
  The GitHub Action's `build-target` input is likewise retired: setting it
  on any mode now fails loudly (`::error::`, checked before the toolchain
  install) naming `.abicheck.yml`'s `build.targets` as the replacement,
  rather than being silently forwarded or ignored.

  The typed Python API is unaffected — `InputSpec.build_targets` remains a
  real field for a programmatic caller (unlike `--build-query`/
  `--build-compile-db`, which have no InputSpec-field parity because they
  were entangled with build-system execution trust, `build_targets` is
  ordinary per-side evidence-scoping data, the same shape as
  `compile_db_filter`/`public_header_dirs`) — only the CLI flag and its
  forwarding through `dump_cmd`/`cli_dump_request.py` are gone.
