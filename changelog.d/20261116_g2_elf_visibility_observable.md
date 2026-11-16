### Fixed

- **`func_visibility_changed` is observable at the binary on ELF.** ELF carries symbol visibility in `st_other` of the symbol table, so a localized header-declared function is seen from the binary alone. The declaration gains an ELF `{B}` condition; PE keeps `{B,H}`, and Mach-O keeps its `{B}`. Declarations only: no detector changes.
