### Fixed

- **DWARF standard-layout trait** — a binary-plus-DWARF comparison now derives
  `is_standard_layout` from the record's own debug facts (member access
  uniformity, virtual functions and bases, reference members, base-class and
  first-member placement), following the same rules as the header path.
  `STANDARD_LAYOUT_LOST` is reported from DWARF instead of only the
  `FIELD_ACCESS_CHANGED` substitute. Where DWARF does not settle the trait it
  stays unknown, never a clean `True`. The extractor lives in
  `abicheck/extract/dwarf_standard_layout.py`.
- **Clang calling-convention names** — `DW_AT_calling_convention` codes clang
  writes for `ms_abi`, `vectorcall` and `regcall` on x86-64 are named
  `LLVM_Win64`, `LLVM_vectorcall` and `LLVM_X86RegCall`; the previous
  `0xD0` `LLVM_vectorcall` entry was not what clang writes.
