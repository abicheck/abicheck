### Fixed

- Simplified the stored-BundleFacts `compare` dispatch's `.abicheck.yml`
  read (`frontends/cli/commands/compare_bundle_facts.py`): removed a
  defensive `try`/`except` around the config re-parse that could not
  actually fire — `compare.py`'s own dispatch call site always forwards
  the resolved config path as an *explicit* `build_config`, which already
  raises a usage error for a malformed file before this code ever runs.
  Added regression tests covering the config-discovery path this touches
  and `bundle.cohorts:`'s whitespace/empty-entry normalization
  (symmetric with the existing `bundle.system_providers:` coverage).
