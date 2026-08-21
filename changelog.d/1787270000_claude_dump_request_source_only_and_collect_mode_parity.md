### Fixed

- **`dump`'s CLI and its typed `DumpRequest` API now resolve one build-source
  collect mode** (CLI cleanup phase two, PR 3A blocker 5). `--build-info` with
  no `--depth` resolved to `source-target` through the `dump` CLI but `build`
  through `run_dump_request`, so the same invocation attempted L4 source-ABI
  replay in one front end and stopped at L3 in the other. The CLI's default is
  canonical — it is the older, documented behaviour, and changing it would
  silently stop a `dump --build-info <pack>` at L3 that reaches L4 today — so
  the typed path now resolves through
  `service_compare_evidence.dump_collect_mode_for`, a mirror of
  `cli_dump_helpers.resolve_dump_depth`'s own rule. `compare` is untouched:
  `collect_mode_for` keeps its own input-inference rule, which is correct for
  that front end.

### Changed

- **`InputSpec.path` is now `Path | None`**, so a source-only dump
  (`abicheck dump --sources ./tree` with no `SO_PATH`) is expressible as a
  typed `DumpRequest`. A pure widening — every existing caller passing a
  concrete path is unaffected — with the "when may this be `None`" rule
  enforced per request type in `validation_errors()`: never for
  `CompareRequest` (a comparison always has two artifacts), and for
  `DumpRequest` only alongside real `sources`/`build_info`.
  `resolve_dump_request()` resolves that shape (which is what `dump --dry-run`
  needs); `execute_dump_request()` raises a specific `ValidationError` for it,
  since producing a binary-less snapshot is still
  `cli_buildsource.dump_source_only`'s own pipeline.
