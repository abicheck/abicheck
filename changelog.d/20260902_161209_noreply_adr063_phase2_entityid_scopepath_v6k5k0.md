### Added

- **DWARF- and ELF-symbol-only snapshots now populate `EntityId`/`ScopePath`** —
  ADR-063 Phase 2's identity primitive, previously landed only on the two
  header-AST backends, now also resolves for `dwarf_snapshot.py`'s DIE walk
  (functions, variables, records, enums, typedefs — via a new
  `AbiSnapshot.typedef_entity_ids` sidecar mirroring the header-AST
  backends' own) and for `dumper_elf_fallback.py`'s export-only, header-less
  fallback path. The new `abicheck/extract/dwarf_scope.py` module builds the
  typed `ScopePath` from DWARF's DIE tree the same way
  `extract.headers.clang.scope`/`extract.headers.castxml.scope` do for the
  two header-AST backends.
