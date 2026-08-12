<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ELF symbol binding (linkage) is now on the model and matchable by
  suppression rules.** `Function.elf_binding`/`Variable.elf_binding`
  (populated from `.dynsym` alongside the existing `elf_visibility`) records
  a symbol's ELF linkage (`global`/`weak`/`local`/`unique`/`other`).
  `FUNC_REMOVED`/`FUNC_REMOVED_ELF_ONLY`/`VAR_REMOVED`/
  `FUNC_DELETED_ELF_FALLBACK` findings now stamp this onto
  `Change.symbol_binding`, and `Suppression` gains a `binding` selector so a
  rule can narrow a removal to the common `WEAK` COMDAT case (e.g. an
  in-class-defined/`inline` member) versus a `GLOBAL`/strong export's
  removal — previously both produced an identical, unnarrowable finding.
  **`binding` is provider-side (library-build) evidence only, not proof a
  removal is safe** — a `WEAK` symbol does not guarantee every consumer
  already carries its own copy (an `extern template`-declared type is the
  documented counterexample; see `docs/use/suppressions.md` and
  `Suppression.binding`'s docstring for the full caveat). Purely additive:
  no existing verdict, finding, or exit code changes as a result of this
  field's presence alone.
