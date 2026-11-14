### Fixed

- The DWARF lane now sees pure virtual members and abstract classes. GCC
  writes `DW_VIRTUALITY_virtual` for a pure virtual, so a member is also read
  as pure when its slot in the library's emitted vtable holds
  `__cxa_pure_virtual`; clang's `pure_virtual` mark is read directly. A
  record's `is_abstract` is derived from its members (unknown, never "not
  abstract", where nothing proves it), so `type_became_abstract`,
  `type_lost_abstract` and `func_virtual_became_pure` are reported from a
  DWARF-only comparison on both compilers.
- A class that keeps only a declaration in the new DWARF (no key function,
  so no vtable or definition is emitted) is no longer reported as
  `type_removed` while its member functions are still defined.
- `EXTRACTOR_GENERATION` is now 71.
