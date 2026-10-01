### Changed

- ADR-063 Phase 0/10: the eight `AbiSnapshot.*_facts_reliable` booleans are
  replaced by one load-time record, `AbiSnapshot.stale_fact_families`, read
  through `abicheck.model.snapshot_reliability.family_reliable(snap, family)`.
  The persisted document is unchanged (the eight keys are still written, in
  the same position). Typed-API code constructing
  `AbiSnapshot(..., clang_vtable_facts_reliable=False)` should pass
  `stale_fact_families=frozenset({"clang_vtable"})` instead. The restrict,
  va_list and variable-access detectors, and the legacy
  deprecation/field-default provenance gates, now rely only on each
  declaration's own fact status, which loading a stale document already
  demotes.
