### Added

- **`RecordType` gains six more `Fact[T]` siblings** (ADR-063 Phase 5, D7's
  fact/capability registry): `is_abstract_fact`, `data_size_bits_fact`,
  `is_standard_layout_fact`, `is_trivially_copyable_fact`,
  `qualified_name_fact`, and `source_header_fact`, each registered in
  `abicheck.model.fact_registry.FACT_REGISTRY`. This is the registry's second
  batch of conversions (after `RecordType.is_final_fact`'s first worked
  example) and closes the same "not collected vs. confirmed absent"
  ambiguity Phase 0 closed for `bases`/`vtable`/`vptr_offset_bits`/
  `is_va_list`, for six more fields that were already tri-state at their own
  declared type. Persisted as of snapshot schema v31; a legacy snapshot
  backfills correctly on load. No detector reads these siblings yet — this
  is model/registry/serialization infrastructure only, matching the
  established Phase 5 scope. `abicheck.provenance.tag_provenance` now keeps
  `RecordType.source_header_fact` in sync when it sets `source_header` by
  post-construction attribute assignment (the field's own generic
  `__post_init__` bridge cannot see that mutation), and
  `dumper_layout_backfill.py`'s DWARF layout backfill carries the surviving
  side's own `Fact[...]` status forward for `data_size_bits`/
  `is_standard_layout`/`is_trivially_copyable`, the same discipline already
  established for `vptr_offset_bits_fact`.
