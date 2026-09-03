### Changed

- **Post-parse identity is now two explicit tiers instead of a bare string**
  (ADR-063 Phase 2). `abicheck/model/identity_tiers.py` introduces
  `StableEntityId` — an `EntityId` that passed
  `entity_id_is_cross_snapshot_stable`, and therefore safe to compare across
  releases or against a stored suppression rule — and `SnapshotLocalIdentity`,
  the fallback tier for a declaration whose identity carries a parse-order
  ordinal or was never resolved at all. The two never compare equal, so a
  snapshot-local ordinal can no longer silently answer a cross-release
  question. No globally-stable ordinal scheme is introduced; the two prior
  attempts at one stay reverted.
- **Opaque-type suppression matches on identity before spelling.**
  `diff_filtering.py`'s opaque-type index moved to its `compare/` owner as
  `compare/opaque_types.OpaqueTypeIndex` and now carries both tiers. A finding
  whose `EntityId` matches an opaque declaration's is suppressed regardless of
  how either side rendered the name, closing a false negative where a
  qualified `Change.symbol` never matched a bare `RecordType.name`. The
  spelling tier is retained as a fallback, so no previously-suppressed finding
  starts being reported.
- **`type_reachability.py`'s closure-walk record keys are typed.** The
  stdlib-reference scan's reached/walked/pending/provenance state is now
  `SnapshotLocalIdentity` rather than bare `str`, naming that domain as
  valid-within-one-snapshot at the type level. Behavior is unchanged; the
  module's spelling-matching machinery is deliberately untouched.
