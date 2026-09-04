### Fixed

- **A global variable's access-level change is no longer silently
  mis-derived from a variable whose own access fact was never actually
  collected.** `diff_symbols_variables.var_access_changes` now reads
  `Variable.access_fact`'s `FactStatus` directly (ADR-063 Phase 5B),
  mirroring `Param.is_va_list`'s already-established treatment — additive
  to the existing whole-snapshot `castxml_var_access_facts_reliable` check.
  A variable whose evidence is genuinely uncollected on either side now
  declines instead of reading as confirmed public (`AccessLevel.PUBLIC` is
  both this field's normal resting value and a real answer); a confirmed
  value is unaffected.
