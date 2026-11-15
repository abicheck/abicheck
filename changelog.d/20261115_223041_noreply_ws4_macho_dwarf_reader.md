### Added

- **Mach-O libraries read DWARF from their adjacent `.dSYM` bundle (ADR-021 step 5)** — a macOS library with a `<lib>.dSYM` beside it (or a framework's `<Name>.framework.dSYM`) now gets DWARF evidence automatically, and an explicit `--debug-root` bundle is read the same way. A bundle that is missing, fat, malformed, or built from a different binary (UUID mismatch) is absent evidence, never an empty DWARF surface. The `dwarf` cells of the macOS abiforge corpus are judged again.

<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
