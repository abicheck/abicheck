### Fixed

- **Direct-clang AST extraction no longer crashes on a malformed `inner`
  field.** `dumper_clang._evaluated_int_value`, `dumper_clang_expr._unwrap_expr`,
  and `buildsource.source_extractors.clang_nodes._unwrap_expr` each iterated
  a node's `inner` field assuming it was always a list when present; a
  malformed/adversarial AST fragment carrying a non-list `inner` (e.g. a
  bare integer or dict) raised `TypeError` instead of degrading cleanly.
  All three now guard the type before iterating, matching their existing
  handling of a missing `inner` key.
