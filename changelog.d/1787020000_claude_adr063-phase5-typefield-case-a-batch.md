### Added

- **ADR-063 Phase 5's fact/capability registry: `TypeField`'s own fields
  converted to `Fact[T]` — the phase's first *case-(a)* batch** (schema
  v38). `is_const`, `is_volatile`, `is_mutable`, `default` and
  `deprecated` now carry `Fact[...]` siblings. Unlike every conversion
  since Phase 0, none of these fields' own values can answer "did any
  producer look?": a plain `False`, or a `None` meaning "this member has
  no default initializer"/"this member is not deprecated", is a
  legitimate value, so availability is carried by the snapshot-level
  `header_cv_facts_reliable`/`clang_field_initializer_facts_reliable`/
  `clang_deprecation_facts_reliable` flags instead. Each field
  accordingly defaults to a private omission sentinel, so an untouched
  field reads `NOT_COLLECTED` rather than a confirmed `present(False)`.
  The `pdb_model`/`dwarf_snapshot` record builders now state
  `Fact.unsupported()` explicitly for the facts their producer cannot
  express at all (DWARF has no `DW_AT` for `mutable`, a default member
  initializer or a deprecation; the PDB layout view carries names, types
  and offsets only).

### Changed

- **The three open-coded legacy-fact corrections ADR-063 Phase 0 left in
  `storage/fact_codec.py` are now one mechanism.**
  `apply_case_a_fact_backfill()` applies a tuple of `CaseAFactRule`s over
  a single raw-document navigator that knows how each owner's instances
  are reached (including the two nested ones, `TypeField` and `Param`);
  `apply_legacy_fact_backfill()` is a thin wrapper stating Phase 0's own
  three rules through it. A case-(a) field converted by a later batch
  adds a rule, not another hand-written loop. `decode_fact_with_legacy_
  presence()` closes the matching decode-side gap: for a case-(a) field,
  a document that omits the *legacy* key entirely means "no evidence",
  which the reader's own `.get(key, False)` default would otherwise have
  laundered into a confirmed value.

### Fixed

- **`dumper_hybrid._merge_field` reverted its own backfill for any
  `Fact[T]`-bridged field.** It used a bare `dataclasses.replace()`,
  which hands `__post_init__` the stale `Fact[...]` sibling alongside the
  new legacy value — and that bridge resolves the disagreement in the
  sibling's favour, silently discarding the merge's update. It now uses
  `replace_with_fact_sync()`, the same fix already applied to
  `_merge_record_type`.
