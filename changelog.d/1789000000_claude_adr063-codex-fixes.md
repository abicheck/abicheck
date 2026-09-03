### Fixed

- **`StableEntityId` rejects an unstable `EntityId` at construction, not just
  through its factory.** The dataclass constructor was still directly
  callable and bypassed `stable_entity_id`'s stability gate, so
  `StableEntityId(entity_id)` could wrap an `Anonymous`/`LocalToFunction`
  ordinal and place a snapshot-local identity into a stable-tier
  intersection. `__post_init__` now re-applies the same
  `entity_id_is_cross_snapshot_stable` gate, so unchecked construction is
  impossible rather than merely discouraged (Codex review on PR #1041).
- **The typedef `SemanticIR` cutover's fidelity gate now compares resolved
  underlying-type values, not just alias names.** `typedef_index_pair`
  previously accepted an `AbiSnapshot` whose `semantic_ir` named the right
  typedef identities but whose `canonical_spelling` disagreed with (or was
  absent versus) the same comparison's independently-resolved legacy alias
  map — e.g. both legacy maps saying `Alias -> int` while a stale or
  hand-built IR says `Alias -> long`. That combination silently fabricated
  or dropped a `TYPEDEF_BASE_CHANGED` finding. The gate now also requires
  each alias's IR-resolved underlying spelling to equal the legacy map's
  value, falling back to the legacy adapter otherwise (Codex review on
  PR #1041).
