### Fixed

- **A struct/class field's const/volatile/mutable qualifier change is no
  longer silently mis-derived from a field whose own CV facts were never
  actually collected.** `diff_types_field_facts._check_field_qualifier_pair`
  now reads each of `TypeField.is_const_fact`/`is_volatile_fact`/
  `is_mutable_fact`'s `FactStatus` directly (ADR-063 Phase 5B) instead of
  comparing the bare boolean values, gated independently per qualifier —
  additive to the existing whole-snapshot `header_cv_facts_reliable` check,
  closing the same per-field gap class already closed for `Param.is_va_list`
  and the vtable/vptr_offset_bits slice. A field whose evidence is genuinely
  uncollected on either side now declines instead of reading as confirmed
  non-const/non-volatile/non-mutable; a confirmed value is unaffected.
