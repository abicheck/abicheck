<!--
A new changelog fragment. See changelog.d/README.md for the workflow.

Uncomment exactly ONE '### <Category>' section below (remove its comment
wrapper) and replace the example bullet with your entry, written the way
it should read in CHANGELOG.md. Delete the other sections.
-->

### Fixed

- **Undeclared export-table findings now carry their ELF binding** — the
  `func_removed_elf_only`/`var_removed_elf_only` removals (OLD's binding) and
  the `func_added_elf_only`/`var_added_elf_only` additions (NEW's binding)
  were emitted without `symbol_binding`, so a `reclassify:` or suppression
  rule with `binding:` never matched them. A header-depth compare of a
  `-fvisibility-inlines-hidden` build therefore kept WEAK inline/template
  casualties at BREAKING instead of letting the rule demote them. A name whose
  versioned aliases disagree on binding now reads `None`, not one alias's
  value. Report schema 6.1 documents the field on these kinds (additive; no
  verdict moves except where a `binding:` rule now matches).
