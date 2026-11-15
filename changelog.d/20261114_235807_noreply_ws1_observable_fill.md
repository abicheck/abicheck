<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Added

- **`observable_at` on the ChangeKind taxonomy (ADR-093, WS-1)** — 104 of 434
  kinds now declare the minimal evidence configurations in which they are
  physically observable (ELF/Mach-O/PE export-table facts, layout facts that
  DWARF and public headers both carry, compiler-flag facts, and declaration-only
  facts). The other 330 stay `UNCLASSIFIED` until their owning modules can
  take the change under the 800-line cap. `scripts/seed_observable_at.py`
  records each decision and its evidence.
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
