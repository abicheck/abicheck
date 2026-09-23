### Fixed

- Error and warning messages no longer tell you to use flags that no command
  accepts. The removed L2 compile-context flags (`--ast-frontend`,
  `--frontend-context`, `--lang`, `--compiler-option`,
  `--allow-ast-frontend-fallback`) now point to `.abicheck.yml` `compile.*`
  keys or `ABICHECK_AST_FRONTEND`/`ABICHECK_ALLOW_AST_FALLBACK`. The removed
  debug flags point to `debug.*` keys, `--show-only` to `--view show=`, and
  `--format`/`--write`/`--output-dir` to `-o FORMAT=DEST`. For example, a
  clang-only host running `dump` used to be told to "run with
  --ast-frontend clang", which failed with `No such option`. A new test
  checks every flag named in a diagnostic against the options that are
  actually registered.
- `dump` now finds `.abicheck.yml` the same way `compare` does: an explicit
  `--config`, then the `--sources` tree root, then the nearest config at or
  above the current directory. Before, `dump` run from inside a project
  checkout ignored that project's config unless `--config` was given. This
  rule now lives only in `config_paths.resolve_project_config()`. A config
  found this way is still never trusted to run `build.query`.
- Removed guards that could never fire because they read options no command
  registers any more:
  - `cli_resolve._reject_compile_context_for_set_inputs`
  - `cli_options.sided_frontend_explicit` and `_shared_frontend_explicit`
  - two stored-BundleFacts `--ast-frontend` rejections

  An explicit `--config` that sets `compile.lang` for a stored/stored
  comparison is now rejected by the same check as every other `compile:` key.

### Added

- The CLI prints progress for its long phases on stderr, for example
  `abicheck: header AST parse ...`, `abicheck: L4 source replay (translation
  units): 120/800 (45s)` and `abicheck: DWARF debug info done (12.3s)`. A
  multi-minute `dump` no longer looks like it has hung. Progress lines never
  go to stdout, and counters are throttled to one line every 5 seconds. Set
  `ABICHECK_PROGRESS=0` to turn them off.
