### Fixed

- **The direct-Clang header path no longer drops `explicit`** — Clang's
  `-ast-dump=json` never serializes the `explicit` specifier of a constructor
  or conversion function, and the extractor read that absent key as a
  confirmed `False` on both sides, so `operator int()` →
  `explicit operator int()` compared as `NO_CHANGE` (case106). The specifier
  is now decided from the declaration's own decl-specifier tokens
  (`explicit`, `explicit(true)`, `explicit(false)`, attributes, comments,
  multi-line declarations, a macro spelled as `explicit`), and is recorded as
  unknown — never `False` — for a dependent `explicit(expr)`, an unrecognized
  macro, or unreadable source, so no `ctor_explicit_*` finding is fabricated
  from missing evidence. The fact's status round-trips through stored
  snapshots.
