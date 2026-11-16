### Added

- **Mach-O `.dSYM` bundle DWARF reader (ADR-021 step 5)** — a thin 64-bit little-endian Mach-O library's `<lib>.dSYM` DWARF can be read through the pyelftools path, after its UUID is checked against the library's. A missing, fat, 32-bit, malformed or UUID-mismatched bundle is absent evidence, never an empty DWARF surface. Not yet attached to `dump`: the Mach-O dump path does not use the bundle until the follow-up re-lands the wiring, so macOS `dwarf` evidence is still reported as unavailable. Not supported: fat (universal) libraries, which have no slice selection, and 32-bit Mach-O.

<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
