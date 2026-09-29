### Fixed

- Several detectors paired or looked up records by bare `name`, so two
  classes sharing a leaf name in different namespaces (`ns1::Impl` /
  `ns2::Impl`) overwrote each other and one was compared against the other.
  The vtable-layout detector (`VIRTUAL_BASE_OFFSET_CHANGED`,
  `SECONDARY_VTABLE_GROUP_CHANGED`), the empty-tag rename and
  `detail::`-field-leak patterns, the type-spelling pass, build-context
  reconciliation, stdlib-embedding attribution and the stdlib-implementation
  reachability walk now use the canonical record identity (`TypeMap` /
  `lookup_matched_type`, or the new `compare.record_lookup.RecordLookup`),
  and the integer-model and `time64` typedef scans use the qualified typedef
  maps (ADR-063 sub-phase 2B).
