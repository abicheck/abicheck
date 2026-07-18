<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`--ast-frontend hybrid`** (G28 Phase 3): runs both the CastXML and clang
  L2 header-AST backends over the same headers and merges them into one
  snapshot (`dumper_hybrid.merge_snapshots`), rather than picking a single
  producer up front. Fixes the concrete cross-producer bug the phase was
  designed around: CastXML sometimes cannot recover a real mangled name for
  a constructor/destructor and synthesizes a placeholder snapshot key
  instead, which shares no identity with that entity's real Itanium-mangled
  key on the clang side — comparing a CastXML-parsed snapshot against a
  clang-parsed snapshot of unchanged source previously reported a false
  `FUNC_REMOVED`+`FUNC_ADDED` pair for every such constructor/destructor.
  The merge now reconciles a synthetic key against a real clang mangled name
  via structural equivalence (same qualified enclosing class, compatible
  cv-normalized parameter signature, same access) before the merged
  snapshot is built. CastXML remains the sole layout source (sizes,
  offsets, vtable slots, alignment are never merged); a handful of
  CastXML-only facts (`deprecated`/`is_override` on functions, `deprecated`
  on variables, `is_abstract`/`deprecated` on types, `default`/`deprecated`
  on fields, `is_scoped`/`deprecated` on enums) are backfilled from clang
  only when CastXML's own value is null — a no-op today since the clang
  backend doesn't populate any of them yet, but forward-looking scaffolding
  for once it does.
- **`AbiSnapshot.fact_provenance`**: a new per-declaration provenance map
  (see `abicheck/fact_provenance.py`) recording, for a hybrid snapshot,
  which backend's value was actually used for each of the CastXML-only
  facts above. The nine detectors previously gated on the whole-snapshot
  `_both_castxml_backed` check (field defaults, deprecation ×5, override,
  abstract records, scoped enums) now gate per-declaration instead, so they
  work correctly on a hybrid snapshot instead of uniformly disabling
  themselves the moment any declaration came from a non-castxml producer.
