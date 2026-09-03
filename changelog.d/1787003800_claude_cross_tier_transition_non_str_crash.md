### Fixed

- **The cross-tier AST/DWARF dedup no longer crashes on a non-string
  `Change.old_value`/`new_value`.** `cross_tier_transition`'s byte-value
  (`STRUCT_SIZE_CHANGED`/`STRUCT_ALIGNMENT_CHANGED`/
  `STRUCT_FIELD_OFFSET_CHANGED`) and type-spelling
  (`STRUCT_FIELD_TYPE_CHANGED`/`TYPE_FIELD_TYPE_CHANGED`) special cases both
  assumed a plain `str | None` value and bypassed the generic
  `hashable_value` safety net entirely — a list-valued slot raised an
  unhandled `TypeError`/`AttributeError` inside `_bits_str_from_bytes_str`/
  `_normalize_type_spelling`, aborting the whole comparison, for exactly the
  five kinds `_dedup_cross_kind` actually indexes. A slot that isn't the
  plain-string shape these conversions expect now falls back to the same
  always-safe `hashable_value` path the other kinds already use.
