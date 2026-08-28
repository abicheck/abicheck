### Added

- **`Fact[T]`, a typed availability/value wrapper, for the model fields
  most prone to fabricated findings from absent evidence
  (`RecordType.bases`/`virtual_bases`/`vtable`/`vptr_offset_bits`,
  `Param.is_va_list`).** ADR-063 Phase 0 (see the "One Semantic Pipeline"
  plan): `abicheck.model.fact.Fact` pairs a value with a `FactStatus`
  (`PRESENT`/`PARTIAL`/`NOT_COLLECTED`/`UNSUPPORTED`/`FAILED`/
  `NOT_APPLICABLE`) so a reader of the new `*_fact` sibling fields can no
  longer observe a value without also observing whether it was actually
  collected — `None`/`[]` no longer has to double as both "confirmed
  absent" and "not collected". No detector reads these siblings yet (see
  "Not yet done" below); this introduces the representation, not
  enforcement.
  Each of the five fields above gains a `*_fact` sibling
  (`bases_fact`, `virtual_bases_fact`, `vtable_fact`,
  `vptr_offset_bits_fact`, `is_va_list_fact`); the plain field stays for
  one release for `dataclasses.asdict()`-based external-consumer
  compatibility, kept in sync with its `Fact[...]` sibling at every
  construction site rather than independently assigned. Snapshot schema
  bumped to v26 to persist the new fields; a pre-v26 snapshot backfills
  correctly from the existing `clang_vtable_facts_reliable`/
  `clang_va_list_facts_reliable` reliability flags rather than misreading
  an untrusted placeholder as a confirmed fact.

### Documentation

- **Not yet done in this pass, tracked explicitly**: no detector migrated
  to read the new `Fact[...]` fields yet (every producer still populates
  only the legacy fields, so the bridge derives each `*_fact` sibling from
  whether that legacy argument was supplied at all — an omitted field
  already yields `NOT_COLLECTED`, a supplied one yields `Fact.present(...)`
  regardless of whether the value itself is trustworthy; no producer
  constructs `Fact[...]` explicitly yet to state a real `PARTIAL`/
  `UNSUPPORTED`/`FAILED` signal, so migrating a detector now would add
  complexity with no behavior change until that lands), and no static
  AI-readiness check yet flags a detector reading the legacy field
  directly. Both are named, scoped follow-ups in the ADR-063 plan doc's
  own Phase 0 section, not silent gaps.
