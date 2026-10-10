### Changed

- Internal (ADR-061): moved five more root header-AST helpers into
  `abicheck.extract.headers` and deleted the old paths, with no re-export:
  `dumper_castxml_probe` -> `extract.headers.castxml.probe`,
  `clang_layout_tool` -> `extract.headers.clang.layout_tool`,
  `dumper_manifest` -> `extract.headers.manifest`, `dumper_sysinc` ->
  `extract.headers.sysinc`, `dumper_toolchain` ->
  `extract.headers.toolchain`. The probe's private castxml version parser,
  an exact copy of `castxml_policy.parse_castxml_version_output`, now
  imports that function. No behaviour change.
