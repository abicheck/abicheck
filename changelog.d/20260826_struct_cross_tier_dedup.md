### Fixed

- **A namespaced struct/class's size or alignment change could be
  reported twice — once by each evidence tier.** The L1 DWARF-tier
  detector (`diff_platform._diff_struct_layouts`) keys its findings by
  the fully-qualified type name (`"ns::Widget"`), while the L2 header/
  castxml-tier detector (`diff_types._diff_type_pair`) deliberately keys
  the same finding by the bare declaration name (`"Widget"`) — the same
  bare-vs-qualified mismatch already fixed for enum kinds, but left open
  for `STRUCT_SIZE_CHANGED`/`TYPE_SIZE_CHANGED`,
  `STRUCT_ALIGNMENT_CHANGED`/`TYPE_ALIGNMENT_CHANGED`, and the three
  field-level struct/type kind pairs. Neither
  `diff_filtering._dedup_cross_kind`'s exact `(kind, symbol)` match nor
  `_deduplicate_cross_detector`'s identity-keyed dedup could recognize
  the two tiers' findings as the same event for any namespaced type.
  `diff_helpers.record_canonical_names`/`canonicalize_record_symbol` now
  bridge the bare and qualified spellings (mirroring the existing enum
  bridge), wired into `_dedup_cross_kind`/`_deduplicate_ast_dwarf`, so a
  namespaced type's struct/type-level and field-level findings collapse
  to one, the same way an unqualified (global-namespace) type's already
  did.
- **Follow-up (Codex review): two distinct types sharing a bare name
  (`a::Widget`/`b::Widget`, both bare `Widget`) still failed to dedup.**
  The bridge above correctly declines to register a genuinely ambiguous
  bare name in its lookup table, but that also meant a perfectly
  well-identified AST-tier finding for `a::Widget` had no way to resolve
  its own bare `Widget` symbol back to `a::Widget` for comparison against
  the DWARF-tier finding's already-qualified symbol.
  `diff_types._append_type_size_and_alignment_changes` now stamps
  `Change.qualified_name` directly from the matched `RecordType` pair it
  already has in hand, and `canonicalize_record_symbol` prefers that
  per-finding hint over the (necessarily ambiguous) table lookup.
- **Follow-up (Codex review): the field-level parent-type match could drop
  a real, distinct field-level finding.** An AST-tier `TYPE_FIELD_*`
  finding's `Change.symbol` names only the parent type, never the field —
  so once the fix above widened `_dedup_cross_kind`'s parent-type match to
  reach namespaced types, a DWARF-tier `STRUCT_FIELD_*` finding for one
  field could be silently dropped merely because a *different* field of
  the same type also changed at the AST tier. The three `TYPE_FIELD_*`
  emitters (`diff_types.py`) and their `STRUCT_FIELD_*` counterparts
  (`diff_platform.py`) now stamp a new `Change.field_name`, and
  `_dedup_cross_kind`'s parent-type match requires it to agree before
  collapsing two findings; the three `TYPE_FIELD_*` emitters also now
  stamp `Change.qualified_name`, mirroring the size/alignment fix above.
