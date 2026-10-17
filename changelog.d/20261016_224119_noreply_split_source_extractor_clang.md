### Changed

- **The clang source extractor is split into three sibling modules** — the
  compile-command builders move to `clang_command.py`, the `-E -dD` macro
  parser to `clang_macros.py`, and the AST node-classification predicates and
  classify context to `clang_node_predicates.py`, leaving `clang.py` with the
  extractor, the compiler probes and the AST walk. Behaviour is unchanged. The
  moved names are imported from their new modules directly; `clang.py` no
  longer re-exports them.
