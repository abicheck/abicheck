### Fixed

- **BTF/CTF resolved-type name lookups now propagate an invalid string
  offset into extraction completeness, not just direct name reads** — a
  struct/union member referencing another type (e.g. a scalar, pointer, or
  qualifier) resolved only through `_TypeResolver.name()`/`.size()`
  previously discarded that type's own out-of-bounds `name_off` and
  silently substituted a kind-default display name (e.g. `"int"`), with no
  signal reaching `extraction_partial` — invisible to every direct
  extractor's own completeness tracking, since it never reads that type's
  string directly. `_TypeResolver` now accepts the same shared
  `invalid_strings` accumulator the direct extractors already use.
- **BTF/CTF string-table reads now also reject a missing NUL terminator as
  incomplete evidence** — both formats specify every string-table entry as
  NUL-terminated, so an in-bounds offset with no terminator before the end
  of the buffer is itself a truncation/corruption signal, not a legitimate
  untruncated trailing name. `read_null_terminated_string()` now returns
  `valid=False` for this shape too, not only for an out-of-bounds offset.
- **PDB `LF_INDEX` fieldlist continuations now report failure when the
  referenced type index is missing or isn't an `LF_FIELDLIST`** —
  previously fell through to an unconditional success return, silently
  dropping the continuation's members (never added to the caller's list at
  all) while still reporting the enclosing fieldlist complete.
