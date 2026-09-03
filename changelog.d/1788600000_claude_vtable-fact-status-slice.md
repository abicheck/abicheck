### Fixed

- **A polymorphic base class's vtable-group membership and a first-vptr
  introduction are no longer silently mis-derived from a record whose own
  vtable evidence was never actually collected.** `diff_vtable_layout.
  _is_polymorphic` and `diff_layout._check_vptr_introduced` now read the
  matched record's `vtable_fact`/`vptr_offset_bits_fact` `FactStatus`
  directly (ADR-063 Phase 5B) instead of trusting an empty/`None` value
  alone: a record whose vtable fact is `NOT_COLLECTED`/`FAILED` (e.g. a
  persisted, pre-v21 direct-clang snapshot's blanket-empty vtable, or a
  mixed-producer dump the whole-snapshot `clang_vtable_facts_reliable`
  flag doesn't cover) now degrades to "indeterminate"/"decline" instead of
  reading as confirmed non-polymorphic. Both changes are additive — they
  can only make the affected detector decline or return indeterminate more
  often, never less; a confirmed-empty (`Fact.present([])`) record is
  unaffected.
