### Fixed

- **A compiler switch no longer reads as a clean, like-for-like comparison** —
  when both snapshots record a known compiler family and the families differ
  (say GCC to Clang), `compare` still produces its verdict but marks the
  `layout` and `runtime` comparability dimensions unverified and adds a
  `coverage_warnings` reason, the same bounded treatment an untagged
  `dependency_scope` gets. If either side's family is unrecorded, it counts as
  unknown, not as a mismatch. The capture side was also widened. On ELF,
  `DW_AT_producer`/`DW_AT_language` are now read from the detached debug file
  the dump resolved when the stripped main image has none. On Mach-O they are
  read from a `__DWARF` segment (an object file, the dSYM passed as debug
  info, or the adjacent `<image>.dSYM`). PE has no reliable compiler-family
  signal, so its `build_mode` stays unknown.
