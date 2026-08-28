### Changed

- **CastXML, direct-clang, and the DWARF backend now construct
  `RecordType`/`Param`'s `Fact[...]` fields explicitly at parse time**,
  instead of leaving it to the `bridge_legacy_and_fact` omission bridge to
  infer (ADR-063 Phase 0, second slice). `bases_fact`/`virtual_bases_fact`/
  `vtable_fact`/`vptr_offset_bits_fact` are stated via a new shared helper,
  `model.entities.record_layout_facts()` — a representational change only,
  since all three producers already resolved these fields themselves and
  the omission bridge already derived the identical `Fact.present(...)`.
  The one real behavior change: `Param.is_va_list_fact` is now
  `Fact.unsupported()` on CastXML and DWARF (neither can ever determine
  va_list-ness, on any run) rather than the omission bridge's weaker
  `NOT_COLLECTED`; direct-clang's `is_va_list_fact` stays
  `Fact.present(...)`, since that backend genuinely evaluates the check
  per parameter. No detector reads these `Fact[...]` fields yet — this
  remains representation only, per Phase 0's own "vertical slice, not flag
  day" discipline.
