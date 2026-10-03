### Removed

- Dead-code plan Stage F: the retired `scan` `--mode` preset resolver
  (`ScanMode`, `resolve_level`, `resolve_source_method`, `mode_preset`) and
  five helpers whose last caller went with #1477 or Stage E. "Explicit
  `--depth` to collect mode" now has one owner,
  `model.evidence_depth_levels.collect_mode_for_depth`, instead of three
  copies in `dump`, `compare`'s typed pipeline and the planner. No behavior
  change.
