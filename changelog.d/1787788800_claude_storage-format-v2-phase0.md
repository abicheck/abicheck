### Added

- **Storage format v2, Phase 0 (ADR-062)** — a new `abicheck/storage/`
  package establishes the persistence primitives the project-scale format is
  built on: `FactAvailability`/`AvailabilityLedger` (a fact family is
  `present`/`partial`/`not_collected`/`unsupported`/`failed`/
  `not_applicable`, so an empty collection can no longer be silently read as
  "nothing is wrong"), `EntityId`/`OccurrenceId`/`OccurrenceSet`/
  `IdentityConflict` (every observation of an entity is preserved and
  ambiguity is recorded rather than resolved by discarding one side, the
  replacement for `AbiSnapshot.index()`'s first-wins behaviour),
  `canonical_form`/`semantic_digest` (a digest invariant under mapping key
  order, set iteration order, and pretty-printing, and deliberately *not*
  invariant under sequence order; capture metadata is excluded via one
  reserved slot at the document root rather than by key name at arbitrary
  depth), and `StorageVersions`/
  `check_reader_compatibility` (the seven version axes today's single
  `SCHEMA_VERSION` integer conflates, with the package-format and
  comparison-contract axes failing closed). These are leaf primitives: nothing produces, consumes, or
  persists them yet, so every existing snapshot, baseline set, and
  `BundleFacts` document is byte-for-byte unchanged, `SCHEMA_VERSION` stays
  at 25, and no CLI surface, report field, or exit code moves. ADR-059's
  compressed storage envelope is kept as-is and is explicitly not superseded.

### Documentation

- **ADR-062 and the storage format v2 plan** — record why the current format
  is a strong single-library interchange format but not yet the right
  project-scale storage architecture (whole-document construct/load,
  first-wins identity loss, one overloaded version integer, missing
  conflated with false, an incompletely specified canonical form, shared
  evidence duplicated per library, and multibuild modelled but not
  capturable), and the phased, adapter-based migration to a
  content-addressed `ProjectSnapshot` package. The plan states its
  relationship to G38 explicitly so that only one persisted bundle shape is
  ever built.
