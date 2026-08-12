<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **ELF symbol binding (linkage) is now on the model and matchable by
  suppression rules.** `Function.elf_binding`/`Variable.elf_binding`
  (populated from `.dynsym` alongside the existing `elf_visibility`) records
  a symbol's ELF linkage (`global`/`weak`/`local`/`unique`/`other`).
  `FUNC_REMOVED`/`FUNC_REMOVED_ELF_ONLY`/`VAR_REMOVED` findings now stamp
  this onto `Change.symbol_binding`, and `Suppression` gains a `binding`
  selector so a rule can distinguish a `WEAK` COMDAT demotion (an
  in-class-defined/`inline` member every consumer already carries its own
  copy of) from a `GLOBAL`/strong export's removal (which always breaks
  every consumer) — previously both produced an identical, unnarrowable
  finding. Purely additive: no existing verdict, finding, or exit code
  changes as a result of this field's presence alone.
