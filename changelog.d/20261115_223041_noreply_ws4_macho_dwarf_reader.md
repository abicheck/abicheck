### Added

- **Mach-O `.dSYM` DWARF reaches `dump`** (ADR-021 step 5) — `dump` of a thin 64-bit little-endian Mach-O library now reads DWARF from its `<lib>.dSYM` bundle, through the pyelftools path, after checking that the bundle's UUID matches the library's. An explicit `debug_info_path` bundle that is missing, mismatched or unreadable fails the dump; an adjacent bundle that is refused is reported as a non-fatal limitation, and a library with no bundle carries no DWARF, as before. Not supported: fat (universal) libraries, which have no slice selection, and 32-bit Mach-O. Verification status: covered by Linux `dump()` tests; the macOS `dwarf` abiforge cells (`pure_virtual_added-dwarf`, `primary_base_grew-dwarf`) are verified by macOS CI on this change.

<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
