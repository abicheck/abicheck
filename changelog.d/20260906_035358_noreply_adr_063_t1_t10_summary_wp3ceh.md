### Changed

- **Dump request contract (ADR-063 Track T4) fully closed** — header-AST
  backend *selection* (`dumper._resolve_effective_ast_backend`, now shared
  by `dumper._header_ast_parser` and
  `service_dump_pipeline.resolve_dump_request`) is split from runtime
  *fallback policy* (`dumper._castxml_fallback_reason`), removing a
  previously-duplicated reimplementation of the frontend-context override
  rule. `service_dump_pipeline.execute_dump_request` also gained a real
  execution path for a binary-less (`--sources`/`--build-info`, no
  `SO_PATH`) `DumpRequest` — it used to raise `ValidationError`
  unconditionally for that shape even though `resolve_dump_request` already
  supported resolving it. `cli_buildsource.dump_source_only` and the `dump`
  CLI's own source-only dispatch are unchanged; see the plan doc for why.
