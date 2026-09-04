### Fixed

- **A named DWARF enumerator with no `DW_AT_const_value` now flags
  incompleteness** — `_process_enum`/`_process_enum_named` previously
  never received the completeness accumulator at all, so a truncated
  enumerator's fabricated `0` value (indistinguishable from a genuine
  `0`) was silently reported as a complete parse.
- **CFI/frame-register analysis no longer requires unwind sections for a
  DSO with no exported functions** — a shared library exporting only
  variables (or no symbols at all) has nothing for the calling-convention
  drift analysis to cover, so a total absence of `.eh_frame`/`.debug_frame`
  no longer downgrades such a comparison to `partial`.

### Changed

- **PR-scoped mutation testing now retains full coverage for an
  unclassified test edit, even alongside a changed detector module** —
  `scripts/mutation_scope.py`'s `selected_modules()`/
  `require_baseline_for_pr()` previously narrowed to just the changed
  module (and skipped baseline drift) whenever a test edit didn't pair
  with any `only_mutate` module's own test glob, so such an edit could
  weaken coverage for an unchanged function anywhere with nothing to
  catch it.
