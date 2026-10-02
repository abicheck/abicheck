### Changed

- The typed Python API now scores with pattern verdicts by default, matching
  the `compare` CLI, which has run them unconditionally since ADR-068:
  `CompareRequest.pattern_verdicts`, `service.run_compare` and
  `compare_snapshots` default to `True`. Before this, the same comparison could
  get a different verdict through the API than through the CLI. Pass
  `pattern_verdicts=False` to keep raw detector output. The stored-bundle
  (`--old-bundle-facts`) route gains pattern verdicts too, which it previously
  skipped even from the CLI.
