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
- **`final` on a virtual member function is detected** — castxml, the clang
  backend and the native gcc plugin record the virt-specifier
  (`Function.is_final`, stored as the optional
  `abicheck.final_member_functions` extension fact); a virtual gaining it is
  the new `func_became_final` (`API_BREAK`: an overrider stops compiling,
  nothing binary changes) and losing it `func_lost_final` (risk). Extractor
  generation 68.
- **`func_became_inline` is a risk, not a source break** (ADR-081 amendment)
  — the break, when there is one, is the export disappearing, which
  `func_removed`/`func_visibility_changed` report; a key function made inline
  with its symbol still exported no longer reads `API_BREAK`.
- **The native gcc header backend declares explicit function template
  specializations** — gcc keeps them in its specialization table, which the
  plugin now walks, so such a specialization is public surface there as under
  castxml and clang.
