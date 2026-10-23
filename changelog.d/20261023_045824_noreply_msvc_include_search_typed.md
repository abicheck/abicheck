### Fixed

- **MSVC and clang-cl include operands reach the public-root probe** — `-I`
  and `/external:I` (separate and joined spellings), and `/I` in
  `cl`/`clang-cl` mode, are now recorded as typed include-search operands on
  `CompilerInvocation`, so a public-header root reached only through them is
  no longer missed by the clang public-root probe. Only the probe changes: the
  replayed AST-dump argv is unchanged. GNU-mode include handling is unchanged.
