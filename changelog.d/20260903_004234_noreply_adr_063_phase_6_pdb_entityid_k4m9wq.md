### Added

- **PDB record/enum types now get a real `entity_id` and populate
  `AbiSnapshot.semantic_ir`** — `extract/pdb_scope.py` parses CodeView's
  flat, already-`"::"`-qualified type names into typed `ScopePath`
  segments (the reverse of DWARF's/the header-AST backends' own
  tree-walk approach), disambiguating a namespace from a nested class by
  checking against the PDB's own recorded struct/class/union names. Wired
  into the existing PE header-scoping fallback path. PDB function/variable
  identity remains unimplemented (needs new DBI module-symbol-stream
  parsing).

### Changed

- **`qualified_name_segments.raw_segments` now delegates to a new shared
  leaf primitive, `model.qualified_name_split.split_top_level_scopes`** —
  no behavior change; the bracket-depth-aware `"::"`-splitting algorithm
  moved down to `model/` so `extract`-layer code (which may not import
  the `compare`-layer `qualified_name_segments` module) can reuse it
  instead of duplicating it.
