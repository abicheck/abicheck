### Fixed

- **`scan --artifact-set` now discovers `.abicheck.yml`'s `bundle:` block
  cwd-upward, not just from `--build-config`/`--sources`.** CLI cleanup
  phase two's PR J removed `--bundle-system-providers` as a CLI flag in
  favor of `.abicheck.yml`, but the artifact-set audit path only attempted
  config discovery relative to an explicit `--build-config` or `--sources`
  tree, never the cwd-upward walk `compare`'s own directory/package fan-out
  uses — so a project's `.abicheck.yml` sitting in the working directory
  with neither flag given was silently never consulted, with no remaining
  way to suppress custom external providers on that invocation shape.

### Added

- **`docs/_meta/topics.yaml`'s `config-keys` and `bundle-analysis` topics
  now list `abicheck/buildsource/build_config.py` (and
  `build_config_schema.py` for `config-keys`) as fact sources**, covering
  the new public `bundle:` config namespace.
