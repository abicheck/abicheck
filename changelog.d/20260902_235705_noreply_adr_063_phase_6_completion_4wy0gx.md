### Fixed

- **DWARF `is_const` now treats `restrict` as transparent when walking a
  variable's leading cv-qualifier run** — `int * const restrict` nests
  `DW_TAG_restrict_type` outside `DW_TAG_const_type`, the same shape of
  bug already fixed for `volatile`.
- **A headerless DWARF dump now populates `semantic_ir` for the types it
  preserves even when it falls back to ELF-export-only functions and
  variables** — previously `_build_symbol_only_snapshot` left
  `semantic_ir=None` even when the DWARF walk found real record types
  with valid `entity_id`s.
