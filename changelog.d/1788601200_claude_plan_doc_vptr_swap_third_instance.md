### Documentation

- Fixed two more instances of the same `vtable`/`vptr_offset_bits`
  gating-status swap in the ADR-063 plan doc (the phase-status table row
  and the "5B's second PR" narrative section), both still naming
  `vptr_offset_bits` as sharing the `TYPE_VTABLE_CHANGED` cluster's open
  gap. Only `vtable` remains open through that cluster.
