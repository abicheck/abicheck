### Added

- **Mach-O `.dSYM` bundle reader (ADR-021 step 5), not yet attached to `dump`** — `abicheck` can now read DWARF from the `<lib>.dSYM` bundle of a thin 64-bit little-endian Mach-O library through the pyelftools path, after checking that the bundle's UUID matches the library's. A missing, fat, 32-bit, malformed, or UUID-mismatched bundle is absent evidence, never an empty DWARF surface; an adjacent bundle that is refused is reported as a non-fatal limitation, while an explicit `--debug-root` bundle that is refused fails the dump. Not yet supported: fat (universal) libraries, which have no slice selection, and 32-bit Mach-O. Verification status: the reader is covered by Linux tests; the macOS `dwarf` abiforge cells (`pure_virtual_added-dwarf`, `primary_base_grew-dwarf`) still do not receive DWARF evidence through `dump`, because the Mach-O dump path does not attach the bundle's DWARF yet.

<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
