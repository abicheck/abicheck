### Fixed

- **CLI cleanup phase two, PR 3A follow-up (Codex review, real reproduction)**:
  `service_dump_pipeline.DumpResult`'s newly-added `effective_includes`/
  `effective_compile_context` fields are now defaulted (`()`/`None`) instead
  of required — `DumpResult` is exported, documented Tier-2 API surface, and
  the previous change would have `TypeError`'d any external caller still
  constructing the prior three-field shape. Also documents, on both
  `DumpResult` and `service_input_resolution.SideResolution`, that these
  fields are safe for identity/comparison but not yet safe for a caller to
  re-read a file under them after the call returns — when the fold ran a
  trusted, zero-config inferred build-system query, that temporary directory
  is already deleted by the time the result is returned.
