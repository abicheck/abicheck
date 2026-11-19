<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **Packed records are reported from the header layout** — a struct that becomes packed (`__attribute__((packed))` or `#pragma pack`) now emits `STRUCT_PACKING_CHANGED` on stripped binary plus headers, not only from DWARF. Packing is read as a declared alignment below the largest natural member alignment, on LP64 x86_64 ELF; a member of unknown alignment yields no finding rather than a guess.
