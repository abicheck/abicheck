### Fixed

- **Four copies of duplicated logic folded back into one** — `service.run_dump`'s
  PE and Mach-O branches now share one post-dump tail (`_finish_native_snapshot`),
  `service_dump_cache` states its 22-argument `run_dump(...)` call once,
  `reporter_markdown`'s leaf-change and root-cause views share their opening
  block, and the L5 call-graph and type-graph extractors share the bounded
  `clang -ast-dump=json` run (new `buildsource/clang_ast_run.py`). The last pair
  had been kept in step by hand through comments pointing at each other across
  several rounds of deadline fixes; a fix to the bounding or the diagnostics can
  no longer land on one pass only.
