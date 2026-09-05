### Changed

- **`execute_dump_request`'s nine out-of-band execution kwargs are now one
  typed `DumpExecutionOptions`** — `build_config`, `build_query`,
  `build_compile_db`, `changed_paths`, `allow_build_query`,
  `legacy_compile_db_tokens`, `legacy_compile_db_matched`,
  `seed_collect_mode`, and `source_frontend_from_folded_context` are folded
  into `abicheck.service_dump_pipeline.DumpExecutionOptions`, passed as one
  `options=` keyword instead of nine separate ones (ADR-063
  duplication-and-convergence-assessment.md Track T4). `options=None` (the
  default) is bit-for-bit equivalent to omitting all nine kwargs before this
  change, so every existing caller that used none of them is unaffected.
