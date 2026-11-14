### Fixed

- A type or enum finding whose name matches a hidden constructor (for
  example `KSD`, whose inlined `KSD::KSD` exists in DWARF but is not
  exported) is no longer moved out of the public surface as `not-exported`.
  The surface classifier now judges every type/enum-entity kind by type
  reachability whenever its `symbol` names a known type, not only the kinds
  on its hand-kept list. 61 kinds were affected, among them
  `secondary_vtable_group_changed`, `vtable_slot_count_changed` and
  `polymorphic_type_non_virtual_dtor`. The bug showed at `-O2`, where gcc
  stops exporting the constructor.
- `EXTRACTOR_GENERATION` is now 65: DWARF-derived call triviality changed
  (copy/move constructors declared under gcc's `C4` name).
