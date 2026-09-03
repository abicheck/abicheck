### Fixed

- **`VIRTUAL_METHOD_ADDED` no longer fabricates a finding from an
  incomplete `bases`/`virtual_bases` capture** (ADR-063 Phase 5B).
  `diff_cxx_rules.virtual_method_addition`'s transitive-base walk (used to
  tell a genuinely new virtual slot apart from a compatible override of an
  inherited one) now tracks whether every visited record's
  `bases_fact`/`virtual_bases_fact` reached a confirmed `PRESENT` status,
  the same "decline rather than fabricate" discipline
  `abicheck.compare.base_class_diff.diff_bases` already applies to its own
  `TYPE_BASE_CHANGED`/`BASE_CLASS_VIRTUAL_CHANGED` findings. Previously, a
  base whose evidence never arrived (`NOT_COLLECTED`/`FAILED`, not a
  confirmed-empty list) silently read as "no bases", which could make a
  real override look like a brand-new vtable slot and emit a spurious
  `VIRTUAL_METHOD_ADDED`. Behavior is unchanged whenever both sides'
  evidence is complete (every real producer already states `bases`/
  `virtual_bases` explicitly, including empty, for every `RecordType` it
  emits).
