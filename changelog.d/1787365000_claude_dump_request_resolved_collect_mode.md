### Fixed

- **A `DumpRequest` built for `compare`'s implicit inline-source dump could
  record the wrong collect mode.** `cli_compare_helpers._embed_inline_source_side`
  forwards `dump_cmd` an already-resolved collect mode (via the private
  `_resolved_collect_mode` hook) without an explicit `--depth`, so the real
  run's mode comes entirely from the pair's own resolution — but the typed
  `DumpRequest` built for the same invocation only recorded `depth`, and
  `resolve_dump_request_evidence` would silently re-derive a *different*
  mode from the absent depth. `DumpRequest` gained a `resolved_collect_mode`
  field carrying the override verbatim, threaded from `cli_dump_request.
  build_dump_request` and `dump_cmd`'s own `_resolved_collect_mode`
  parameter (Codex review on #814).
