### Changed

- Internal (ADR-061): moved six root header-AST helpers into
  `abicheck.extract.headers` and deleted the old paths, with no re-export:
  `dumper_clang_attributes`, `dumper_clang_expr`, `dumper_clang_qualifiers`
  and `dumper_clang_streaming` -> `extract.headers.clang.{attributes,expr,qualifiers,streaming}`;
  `dumper_castxml_typedefs` and `castxml_policy` ->
  `extract.headers.castxml.{typedefs,policy}`. No behaviour change.
