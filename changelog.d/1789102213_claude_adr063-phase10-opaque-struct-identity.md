### Fixed

- **`_downgrade_opaque_struct_changes` (the DWARF-oriented, asymmetric-
  existence opaque-suppression path) now matches on stable `EntityId`
  first, `RecordType.name` spelling second** (ADR-063 Phase 10), closing a
  false negative the same class of qualified-vs-bare spelling mismatch
  `_downgrade_opaque_type_changes` already closed for its own, separate
  opaque-suppression path: a bare `Change.symbol` string compared against
  a bare/namespace-baked `RecordType.name` set could miss a declaration
  both sides agree on. This third opaque-suppression tracker was found and
  migrated onto `compare/opaque_types.OpaqueTypeIndex` via a new
  `OpaqueTypeIndex.build(declarations)` classmethod, always consulted
  non-strictly (a stable-tier miss still falls back to spelling, never
  proof of non-opacity) — a real behavior change only for a snapshot pair
  whose stable identities resolve and whose rendered spellings disagree;
  every existing bare-string-only case is bit-for-bit unchanged.
