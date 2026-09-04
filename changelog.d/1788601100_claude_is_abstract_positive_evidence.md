### Fixed

- `SECONDARY_VTABLE_GROUP_CHANGED` (`diff_vtable_layout._is_polymorphic`)
  now also treats a confirmed `is_abstract=True` as unconditional proof of
  polymorphism, even when `vtable_fact` itself wasn't collected — an
  abstract class necessarily has a pure virtual function, which is still a
  virtual function. Unlike `vptr_offset_bits_fact`, `is_abstract_fact`
  carries no known unreliable-producer history, so this check isn't gated
  on `vtable_facts_reliable` (same treatment `virtual_bases_fact` already
  gets).
