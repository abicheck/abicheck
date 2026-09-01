### Fixed

- **`storage/snapshot_load_normalization.backfill_missing_elf_binding` no
  longer leaves `elf_binding_fact` stale** (ADR-063 Phase 5, Codex/
  CodeRabbit review). This load-time legacy backfill recovers
  `Function`/`Variable.elf_binding` from an already-loaded snapshot's
  `elf.symbols` block when the per-declaration key predates the field,
  but only updated the legacy value — the `elf_binding_fact` sibling
  stayed at its `__post_init__`-derived `Fact.not_collected()`. A
  reserialize-then-reload round trip of the backfilled snapshot then
  read that stale `not_collected` fact as authoritative and silently
  reset the recovered `elf_binding` back to `None`, breaking
  `binding:`-selector suppression matching. Fixed by setting
  `elf_binding_fact = Fact.present(elf_sym.binding)` alongside the
  legacy assignment, mirroring the fix already applied to
  `dumper_elf_symbols._populate_elf_visibility`'s fresh-dump path.
- **Direct-clang's opaque/incomplete `RecordType` no longer misreports a
  confirmed global-scope `qualified_name` as "not collected"** (Codex/
  CodeRabbit review). The opaque branch of `_build_record`
  (`extract/headers/clang/records.py`) set `qualified_name` but never
  constructed the explicit `qualified_name_fact`, unlike the non-opaque
  branch beside it — so an opaque record's `qualified_name_fact` fell
  through the generic bridge to `NOT_COLLECTED` even when `entry.scope`
  confirmed global scope. Fixed by constructing `qualified_name_fact`
  explicitly in the opaque branch too, matching the non-opaque branch's
  own established convention.
- **`fact_registry.py`'s `RecordType.qualified_name`/`EnumType.qualified_name`
  entries now correctly declare `identity_relevant=True`** (Codex review):
  `tu_merge.merge_translation_units` keys both records and enums by
  `qualified_name or name`, so this field is part of the merge/matching
  identity, not just a display detail.
- **`fact_registry.py`'s `source_header` entries (`RecordType`, `EnumType`,
  `Variable`, `Function`) now correctly list `dwarf`/`pdb` among their
  producing backends** (Codex review): `provenance.apply_provenance()`/
  `tag_provenance()` derive `source_header` unconditionally from
  `source_location` for any declaration, and both the DWARF
  (`DW_AT_decl_file`) and PDB backends populate `source_location` too —
  the registry previously claimed only the two header-AST backends.
