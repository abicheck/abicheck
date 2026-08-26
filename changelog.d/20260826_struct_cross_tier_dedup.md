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
