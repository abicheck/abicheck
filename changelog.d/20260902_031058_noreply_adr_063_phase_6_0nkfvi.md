### Added

- **`AbiSnapshot.semantic_ir` is now populated on a real ELF `dump()`/`compare()`** —
  ADR-063 Phase 6's second slice. `extract/semantic_normalizer.py`'s
  `normalize_header_ast` projects each header-AST backend's already-parsed
  records, enums, and typedefs (both `castxml` and `clang` already carry a
  canonical `entity_id` per declaration since Phase 2) into a real
  `SemanticIR`, wired through the shared single-TU/manifest assembly path so
  both a legacy single-header dump and a `--dump-manifest` dump populate it
  — including through `--ast-frontend hybrid`, whose cross-backend
  reconciliation (landed in this phase's first slice) now runs against real
  data for the first time. Functions, variables, constants, and the
  PE/Mach-O/BTF/CTF/PDB backends are not covered by this slice.
