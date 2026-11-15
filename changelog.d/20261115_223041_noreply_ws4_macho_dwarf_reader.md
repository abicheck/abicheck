<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **Mach-O libraries read DWARF from their adjacent `.dSYM` bundle (ADR-021 step 5)** — a macOS library with a `<lib>.dSYM` beside it (or a framework's `<Name>.framework.dSYM`) now gets DWARF evidence automatically, through the existing pyelftools path. A bundle that is missing, fat, malformed, or built from a different binary (UUID mismatch) is absent evidence, never an empty DWARF surface. The `dwarf` cells of the macOS abiforge corpus are judged again.

<!--
### Performance

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
<!--
### Documentation

- **Short bold summary** — the rest of the sentence: what changed, for
  whom, and why it matters. Backtick identifiers like `ChangeKind` or
  `--policy-file`.

-->
