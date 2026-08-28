### Changed

- **CastXML, direct-clang, and the DWARF backend now construct
  `RecordType`/`Param`'s `Fact[...]` fields explicitly at parse time**,
  instead of leaving it to the `bridge_legacy_and_fact` omission bridge to
  infer (ADR-063 Phase 0, second slice). `bases_fact`/`virtual_bases_fact`/
  `vtable_fact`/`vptr_offset_bits_fact` are stated via a new shared helper,
  `model.entities.record_layout_facts()` — a representational change only,
  since all three producers already resolved these fields themselves and
  the omission bridge already derived the identical `Fact.present(...)`.
  The one real behavior change: `Param.is_va_list_fact` is now
  `Fact.unsupported()` on CastXML and DWARF (neither can ever determine
  va_list-ness, on any run) rather than the omission bridge's weaker
  `NOT_COLLECTED`; direct-clang's `is_va_list_fact` is `Fact.partial(...)`,
  since that backend genuinely evaluates the check per parameter but only
  covers x86-64 System V. `vptr_offset_bits_fact` is `Fact.partial(...)`
  on CastXML/direct-clang too, matching the same heuristic-derived-value
  caveat its legacy sibling field's capability-matrix row already states.
  No detector reads these `Fact[...]` fields yet — this remains
  representation only, per Phase 0's own "vertical slice, not flag day"
  discipline. The DWARF-backfill and castxml/clang-hybrid-merge paths
  (`dumper_layout_backfill.py`, `dumper_hybrid.py`) now also preserve
  whichever side's own `vptr_offset_bits_fact` status backs the value that
  survives the merge, rather than letting the generic merge/backfill
  helper's default derivation silently promote a `PARTIAL` status back to
  `PRESENT`. The whole-snapshot disk cache version is bumped (19 → 20)
  accordingly.
