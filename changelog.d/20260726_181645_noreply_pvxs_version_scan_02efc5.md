### Fixed

- **The `include_sequence` owned-header carve-out accepted a duplicated
  newly-owned header pair as safe growth.** `_include_sequence_is_additive_owned_growth`
  converted a slot's decoded owned-pair list straight to a `set` via
  `{tuple(p) for p in pairs}` — which silently collapses a genuine
  duplicate away before it can be detected. Since `_slot_token_for_ancestor`'s
  real construction always emits a deduplicated pair list, a duplicated
  pair (e.g. a newly-appended `("c.h", "c.h")` listed twice) is never
  genuine evidence, but the set conversion made it indistinguishable from
  a single legitimate addition, so the carve-out still authorized the
  waiver. Both the old and new decoded pair lists are now checked for
  duplicates before the set conversion, closing the gap on either side.
