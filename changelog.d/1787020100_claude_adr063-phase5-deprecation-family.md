### Added

- **ADR-063 Phase 5's fact/capability registry: the whole `deprecated`
  family plus `EnumType.is_scoped` converted to `Fact[T]`** (schema v39).
  `Function.deprecated`, `Variable.deprecated`, `RecordType.deprecated`,
  `EnumType.deprecated` and `EnumType.is_scoped` now carry `Fact[...]`
  siblings, joining `TypeField.deprecated` from the previous batch — the
  six fields `AbiSnapshot.clang_deprecation_facts_reliable` guards, all
  case (a): `None` means "not deprecated" as much as "not captured", and a
  pre-v19 clang snapshot reported a blanket `None`/`False` for every
  declaration, which only the snapshot-level flag can tell apart from a
  real answer. A legacy document whose flag says so now loads those facts
  as `NOT_COLLECTED` instead of confirming a placeholder.

### Fixed

- **Three merge paths silently reverted their own `deprecated` write.**
  `dumper_hybrid._merge_variable`, `tu_merge`'s more-public-of and
  variable merges, and `tu_merge_provenance`'s deprecation re-application
  each used a bare `dataclasses.replace()`, which hands `__post_init__`
  the stale `Fact[...]` sibling alongside the new value — and that bridge
  resolves in the sibling's favour. All four now use
  `replace_with_fact_sync()`. `_blank_provenance` blanks each blanked
  field's `Fact[...]` sibling from the field list itself rather than
  naming `source_header` alone, so a later conversion of any blanked
  field cannot reintroduce the same defect.

### Changed

- **`model/fact_registry_entries.py` is now a pure assembly point.** Its
  `FactDefinition` entries live in three owner-family sibling modules
  (`_types`, `_symbols`, `_platform`), mirroring `model/change_catalog/`'s
  own division — the single list crossed ADR-061's 800-line per-module
  ceiling as Phase 5's field-by-field conversion filled it. Content
  unchanged by the split.
