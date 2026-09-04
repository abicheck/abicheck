### Fixed

- **A function parameter's `restrict` qualifier change is no longer silently
  mis-derived from a parameter whose own restrict fact was never actually
  collected.** `diff_param_qualifiers.param_restrict_changes` now reads
  `Param.is_restrict_fact`'s `FactStatus` directly (ADR-063 Phase 5B),
  mirroring the sibling `is_va_list` detector's already-established
  treatment — additive to the existing whole-snapshot
  `clang_restrict_facts_reliable` check. A parameter whose evidence is
  genuinely uncollected on either side now declines instead of reading as
  confirmed non-`restrict`; a confirmed value is unaffected.
