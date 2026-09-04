### Added

- **ADR-063 Phase 5's fact/capability registry: the three binary-format
  metadata blocks' own case-(b) fields converted to `Fact[T]`** (schema
  v37) — `ElfMetadata.dynamic_flags`/`has_init`/`has_fini`,
  `PeMetadata.delay_imports`, and `MachoMetadata.rpaths` now carry
  `Fact[...]` siblings, the same case-(b) "`None` already unambiguously
  means not captured" pattern already applied to the four declaration
  dataclasses and `AbiSnapshot.ast_resolved_standard`. Schema-version-
  driven rather than backend-driven, since each of the three blocks is
  parsed by exactly one backend. `dynamic_flags_fact`'s decoded value is
  reconstructed as a real `frozenset` rather than left as the bare JSON
  list, mirroring `Variable.elf_binding_fact`'s `SymbolBinding`
  reconstruction.

  Fixed three real post-construction mutation traps this batch's
  conversions would otherwise have silently reverted:
  `elf_metadata._parse_dynamic`, `pe_metadata._parse`, and
  `macho_metadata.parse_macho_metadata` each set their new field's plain
  legacy value via several statements after `__post_init__` had already
  run; a new `model.fact.sync_present_facts(obj, *field_names)` helper
  (mirroring `replace_with_fact_sync` for the "several plain-assignment
  statements, not one `dataclasses.replace()`" shape) closes all three in
  one line each.

### Changed

- **`fact_registry_completeness.py`'s decode-wiring scan now also reads
  `snapshot_platform_blocks.py`**, where `ElfMetadata`/`PeMetadata`/
  `MachoMetadata`'s own `decode_fact(...)` call sites live (they are
  single nested sub-blocks the whole-snapshot decode delegates to, not
  one of the list-of-declaration collections `fact_codec.py`/
  `serialization.py`'s existing receivers cover). `elf_from_dict`/
  `pe_from_dict`/`macho_from_dict`'s own dict-parameter name was renamed
  from `e` to `elf`/`pe`/`macho` respectively, since the completeness
  scan resolves a receiver name to an owner with no per-file scoping and
  `e` already names `EnumType`'s own receiver in `serialization.py`.
