### Fixed

- **A covariant return at offset 0 is no longer a binary break** — a
  pointer or reference return retyped to a class derived from the old one
  through non-virtual bases at offset 0 returns the same address, keeps its
  mangled name and adds no vtable slot (Itanium C++ ABI 2.5.3); it now reads
  as the new `func_return_covariant_narrowed` (compatible with risk) instead
  of `func_return_changed`. A secondary base at a non-zero offset, a virtual
  base, an unknown offset or a cv change keeps the break.
- **An override added to or dropped from a derived class is no longer a
  vtable change** — it reuses its base's slot. The DWARF lane, whose vtable
  list names only the class's own virtuals, reported `type_vtable_changed`
  (a binary break) for an added override, and the header lanes did for a
  dropped one.
- **An added enum is reported** — `type_added` for an enum the new side
  declares, as for a record; the addition used to vanish from the report
  while the removal of the same enum was reported.
