### Fixed

- A reordered vtable compared from headers (castxml or the clang backend)
  now names each moved virtual method, not only its class. Header snapshots
  record a class's declared virtual sequence but no per-method slot index,
  so the per-method `type_vtable_changed` findings that DWARF produces were
  missing there and the report did not say which methods an old client now
  calls through the wrong slot. Each method whose position in a pure reorder
  changed now gets its own finding (`position N → M`). A method whose slot
  index either side records is still reported from that index, once.
