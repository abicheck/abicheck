### Fixed

- **clang header backend: typedef'd return types** — the clang backend now
  records the ABI fact of a return type spelled through a typedef or alias
  (`count_t f();`), as castxml does. clang's AST gives a function's type no
  desugared spelling, so the alias is resolved through the header's own
  typedef declarations. Before, removing such a typedef (`count_t f()` ->
  `int f()`) read `func_return_changed` (BREAKING) under
  `--ast-frontend clang`; it now reads `typedef_removed` with the return
  change proven ABI-equivalent. `EXTRACTOR_GENERATION` 55, snapshot cache
  salt v49.
