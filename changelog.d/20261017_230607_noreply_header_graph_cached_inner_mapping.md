### Fixed

- **A cached AST whose root `inner` is not a list no longer reads as an empty, clean translation unit** —
  the streamed projection (taken on a warm run for any document at or above the
  streaming threshold) skipped a root `inner` that was a mapping, `null` or a
  scalar as though the key were absent, and returned an empty projection with no
  error. Every AST-derived header pass then read as collected. The streaming scanner
  now refuses such a root with the same `MalformedClangAstError`-equivalent
  containment the whole-tree path already applies (`_after_colon_bracket` in
  `buildsource/header_graph_ast_stream.py`), so the run degrades to the ordinary
  parse, which records the call graph as degraded. A valid empty `inner` and a
  translation unit with no `inner` key at all are unchanged.
