### Documentation

- Corrected `diff_types_vtable.py`'s module docstring and the ADR-063 plan
  doc, which both misstated `vptr_offset_bits` as sharing the
  `TYPE_VTABLE_CHANGED` cluster's residual "not yet FactStatus-gated" gap.
  That cluster never reads `vptr_offset_bits`/`vptr_offset_bits_fact` at all
  (only `vtable_fact`) — `vptr_offset_bits` is fully gated via
  `diff_layout._check_vptr_introduced`'s own direct-status pre-check. Only
  `vtable` remains open through this cluster.
