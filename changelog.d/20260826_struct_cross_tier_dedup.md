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
- **Follow-up (Codex review): a scoped whole-type symbol could be
  corrupted by a stale field-qualification guess.** `canonicalize_record_
  symbol` decided whether `Change.symbol` was field-qualified via
  `"::" in symbol` — wrong for a scoped *whole-type* symbol that itself
  contains `::` without being field-qualified at all, such as a template
  specialization over a namespaced argument (`"Wrapper<dep::Tag>"`),
  which the old guess corrupted into `"Wrapper<dep::Tag>::Tag>"`. The
  function now takes `Change.field_name` as its sole, explicit signal for
  where to split a symbol into parent + field, never a string guess.
- **Follow-up (Codex review): a global (unqualified) type/enum sharing a
  bare name with a namespaced one was silently bridged to it.**
  `record_canonical_names`/`_enum_canonical_names` both skipped an
  unqualified record/enum entirely when building the bare-name ambiguity
  table, so a genuinely global `Widget` alongside a namespaced `ns::Widget`
  never counted as a competing identity — silently registering
  `Widget -> ns::Widget` and letting the global type's own finding be
  wrongly canonicalized onto the unrelated namespaced one. Both functions
  now record an unqualified declaration as a competitor too (via a `None`
  sentinel in the per-bare-name candidate set), so this collision correctly
  disables the bridge the same way two differently-namespaced types
  sharing a bare name already did.
- **Follow-up (Codex review, fresh evidence): a DWARF-only global type/enum
  (declared in binary debug info but not in the supplied headers) had the
  identical bug from a different angle.** The fix above only scanned
  `snap.types`/`snap.enums` (the header-tier's own declarations), so a
  record/enum that DWARF sees but the header surface never exposes
  contributed no competing entry at all. Both functions now also scan
  `snap.dwarf.structs`/`snap.dwarf.enums`'s own keys directly, registering
  a namespaced DWARF-only key as an ordinary qualified candidate and a bare
  global DWARF-only key as the same competing-identity sentinel.
- **Follow-up (Codex review): a kind+symbol match alone could drop a DWARF
  finding whose own transition genuinely disagreed with the AST finding's**
  — e.g. header evidence reporting a struct's size change 64→128 while
  DWARF reports 64→96 for the same symbol. `_dedup_cross_kind` now also
  requires the two findings' `(old_value, new_value)` transitions to
  agree before collapsing them, via a new `diff_helpers.
  cross_tier_transition`: it converts a byte-based DWARF-tier value
  (`STRUCT_SIZE_CHANGED`/`STRUCT_ALIGNMENT_CHANGED`/
  `STRUCT_FIELD_OFFSET_CHANGED`) to bits before comparing against its
  always-bit-based AST-tier equivalent (a genuinely identical transition
  otherwise never matches, since the two tiers use different units), and
  compares a type-spelling transition (`STRUCT_FIELD_TYPE_CHANGED`/
  `TYPE_FIELD_TYPE_CHANGED`) via the existing `_normalize_type_name`
  DWARF↔castxml spelling bridge rather than raw string equality. A
  removal kind (no independent transition to disagree about) still dedups
  on kind+symbol alone. `_normalize_type_name` moved from
  `diff_platform.py` into `diff_helpers.py` (which `diff_platform.py`
  already imports from) so this new comparison doesn't need a
  `diff_helpers -> diff_platform` edge back into a module that already
  imports `diff_helpers` — a cycle the `import-cycle-growth` gate rejects.
