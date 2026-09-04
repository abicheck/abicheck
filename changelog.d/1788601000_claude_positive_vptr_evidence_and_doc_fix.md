### Fixed

- `SECONDARY_VTABLE_GROUP_CHANGED` (`diff_vtable_layout._is_polymorphic`)
  now treats a confirmed, non-`None` `vptr_offset_bits` as unconditional
  positive proof of polymorphism, even when `vtable_fact` itself wasn't
  collected — a real recorded vptr offset can only exist if the class
  genuinely owns a vptr, unlike a confirmed `None` (which stays ambiguous:
  it also covers "polymorphic only via a virtual base").

### Documentation

- Fixed a second, earlier instance of the same `vtable`/`vptr_offset_bits`
  gating-status swap in the ADR-063 plan doc's case-(a) inventory summary.
