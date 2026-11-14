### Fixed

- **`type_visibility_changed` is now actually reported** — the kind was
  declared and mapped to the advanced-DWARF detector, which never produced
  it, so a class made `visibility("hidden")` while its members stayed
  exported read only as three anonymous `var_removed_elf_only` removals of
  its `_ZTI`/`_ZTS`/`_ZTV` symbols. A new export-table detector
  (`compare/type_visibility.py`) reports one `type_visibility_changed` per
  class whose typeinfo/vtable symbols are no longer exported (or narrow to
  protected) while the class's members stay exported, and stays silent for
  a removed class or one that lost its last virtual. The per-symbol
  removals are still reported.
