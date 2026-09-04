### Fixed

- **DWARF array types with no `DW_AT_type` now flag incompleteness, while
  the void-encoding convention is correctly extended to qualifiers,
  typedefs, and references** — a bare `DW_TAG_array_type` with no
  `DW_AT_type` at all (no legal `void[]` in C/C++) is genuinely malformed
  and now marks `incomplete`. A qualifier (`const`/`volatile`/`restrict`/
  `_Atomic`), a typedef, or a reference type with no `DW_AT_type` is
  DWARF's own legitimate encoding for qualified void / `typedef void X;`
  / a would-be `void&` — the same convention `DW_TAG_pointer_type`'s
  `void *` already had — and must **not** be flagged. (A real g++-compiled
  libstdc++ template instantiation and glibc's own `typedef void
  _IO_lock_t;` both reproduce this shape.)
- **An alternate entry point inside another function's FDE range now gets
  CFI facts derived from its own PC, not the enclosing function's
  whole-FDE summary** — an alternate entry placed past a CFI row
  transition (e.g. past the enclosing function's own prologue) can have a
  different CFA register or saved-register set than the FDE-wide
  dominant/union summary reports; that difference is now visible to the
  advanced diff instead of being silently masked.
