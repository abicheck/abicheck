### Fixed

- castxml header extraction no longer turns a missing `offset` attribute on
  an anonymous struct/union member (or a field inside one) into offset 0; the
  flattened field's `offset_bits` is now unknown (`None`), so
  `type_field_offset_changed` cannot fire on absent evidence. Found by the
  new Lean model of that rule in `formal/lean/` (negative check N1).
