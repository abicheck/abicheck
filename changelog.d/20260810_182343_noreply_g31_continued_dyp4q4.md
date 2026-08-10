<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Fixed

- **A persisted, pre-schema-v21 direct-clang snapshot no longer produces a
  false `VPTR_INTRODUCED`/`TYPE_VTABLE_CHANGED`/`LAYOUT_UNVERIFIABLE` when
  compared against a fresh dump of the same, unchanged headers.** The
  direct-clang backend's `RecordType.vtable`/`vptr_offset_bits` were
  unconditionally empty/`None` before this vtable-reconstruction feature
  landed — real but WRONG data for an already-polymorphic class, not merely
  absent, indistinguishable from a genuine non-polymorphic class by value
  alone. Schema bumped to v21; a new `AbiSnapshot.clang_vtable_facts_reliable`
  marker (following the existing `header_cv_facts_reliable`/
  `clang_deprecation_facts_reliable`/`clang_field_initializer_facts_reliable`
  legacy-fact pattern) is `False` only for a snapshot rehydrated from a
  persisted pre-v21, clang-producer schema, and the affected detectors
  decline entirely when either side is unreliable.
- **The direct-clang backend's inferred-virtuality recovery no longer widens
  an unrelated `extern "C"` free function's `is_virtual` purely from a bare
  fallback-name collision.** An uninstantiated template method carries no
  `mangledName` at all, so the vtable reconstruction falls back to its bare
  `name` (e.g. `"f"`) as the slot's identity — a free `extern "C"` function
  sharing that same bare name mangles to the identical string by C-linkage
  design. Restricted the recovered-virtuality widening to actual
  member-function declaration kinds, since only a class member can be
  virtual in C++ at all.
