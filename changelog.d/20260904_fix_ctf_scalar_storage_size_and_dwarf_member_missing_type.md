### Fixed

- **CTF integer/float member size now uses the declared storage size** —
  `CTF_K_INTEGER`/`CTF_K_FLOAT` derived their byte size from the encoding
  word's own bit-width field, but per illumos `sys/ctf.h` that field is
  only the occupied bit slice within the type's real storage size
  (`size_or_type`), which can legitimately be narrower. Fixed to use
  `size_or_type` directly, mirroring the identical `BTF_KIND_INT` fix.
- **DWARF struct/union members with no `DW_AT_type` at all are now
  flagged incomplete** — a named member DIE missing `DW_AT_type` entirely
  (truncated/malformed debug info, not a legitimate type-less case)
  previously fell through with no completeness signal, unlike the
  sibling malformed-reference case. Fixed at the one call site
  (`_process_member`) that knows a type is mandatory for a real member.
