### Added

- **`finding_identity.resolve_change_identity` now reads `Change.entity_id`**
  (ADR-063 Phase 2, closing the true completion of (c2)), folding it in as
  a new `entity:<key>` alias qualified with the existing discriminator --
  additive only, never promoted to `primary_id`/tier, so every existing
  suppression rule and canonical finding ID is unchanged. `EntityId` gains
  a new `.key` property (`model/identity.py`) producing a flat,
  collision-safe string form for this purpose. The same `entity_id`
  old-else-new fallback now also reaches every remaining function-backed
  `Change` construction site: `diff_hidden_friends.py`,
  `diff_param_qualifiers.py`, constructor overload-ambiguity risk, and the
  auxiliary parameter/deprecation/override-specifier detectors.
