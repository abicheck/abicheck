### Fixed

- **DWARF-only comparisons read three facts they used to drop.** An export
  whose DWARF entry has another name (a `.symver` implementation, an IFUNC)
  no longer reads `func_removed`/`BREAKING` while the symbol is still
  exported: the DWARF lane now records it as an export-table record, as the
  symbols-only lane does, and such a record compares no signature. Adding or
  removing `...` on a C function now reports `func_variadic_added`/
  `func_variadic_removed` instead of `NO_CHANGE`. A gcc rebuild with
  `-funsigned-char`/`-fsigned-char` that flips plain `char`'s signedness now
  reports `char_signedness_changed` (the x86/Arm/POWER/RISC-V defaults are
  used only when the producer records its switches; clang and
  `-gno-record-gcc-switches` builds record none, so their setting is unknown
  and never reported as a flip, see known-gaps). A binary-only dump of a
  library with debug info no longer drops the exports compiled without it,
  and a `-g` build compared with its stripped copy no longer reads
  `func_virtual_removed`. Extractor generation 50. Found by the generated
  edit corpus.
