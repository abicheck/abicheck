### Fixed

- The export surface fact (`binary_exported_fact`) no longer claims a dynamic
  export the observed `exports` join denies. A `.symtab`-only symbol, a
  bare-name hit for a declaration whose mangled spelling is not exported, and
  DWARF's demangled-name fallback were each recorded as a plain confirmed
  export; every producer (castxml, clang, DWARF) now classifies through the one
  shared `model.export_index.match_export` primitive, and only a dynamic-table
  match of the declaration's linker spelling is `PRESENT(True)`. The weaker
  tiers stay truthy (no finding changes) but are `PARTIAL` with an
  `export-match:<tier>` diagnostic, read back by
  `model.surface_facts.binary_export_match`. No snapshot schema change.
