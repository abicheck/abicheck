### Fixed

- **castxml and the clang header backend now record the same record layout** —
  a castxml snapshot stored each base class's offset in bytes where DWARF and
  the clang backend store bits, so comparing a castxml dump with a clang one
  reported `base_class_offset_changed` for every base at a nonzero offset; it
  now stores bits, and an older castxml baseline is converted on load (extractor
  generation 38). The clang backend's built-in record-layout pass now also sizes
  empty classes and records an empty base's offset, gives the members of an
  anonymous struct/union their offsets, lays out C headers, and no longer puts a
  virtual base in `base_offsets`, so without debug info a clang dump reports
  `type_size_changed`/`type_field_offset_changed` for those records as a castxml
  dump does. Re-dump a generation-37 clang or hybrid baseline.
