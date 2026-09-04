### Added

- **ADR-063 Phase 3 (D5), slice 11: `checker.compare()`/`service.
  compare_snapshots()` wiring for the resolved public-surface id pair.**
  `checker.compare()` gains `old_public_entity_ids`/`new_public_entity_ids`
  (`frozenset[EntityId] | None`, default `None` on both) and forwards them,
  unresolved, to its own `_apply_surface_metrics`/`_apply_pattern_verdicts_step`
  steps — `compare()` itself never calls `PublicSurfaceQuery.resolve()`
  (`policy -> compare` stays a one-way edge; only `model`/`compare`-layer
  code lives inside `checker.compare()`). `service.compare_snapshots()` --
  the Tier-2 chokepoint every front end routes through (ADR-037 D1/D10.1)
  -- resolves each side's own set via `PublicSurfaceQuery().resolve()` and
  forwards the pair into `compare()`; `service_compare_pipeline.
  classify_compare_pair` gets this for free since it already calls
  `compare_snapshots()` rather than `checker.compare()` directly.

### Fixed

- **`compare/surface_graph.py`'s legacy-entity-id fallback no longer calls
  `entity_id_for_type`/`entity_id_for_typedef`.** Those resolver
  constructors may only be called by a header-AST producer -- the only
  place a real, typed `ScopePath` exists to build one from
  (`tests/test_entity_id_carrier.py::TestResolverIsOnlyCalledByAProducer`
  enforces this repo-wide) -- so a post-parse module recomputing one from a
  bare qualified-name string was exactly the anti-pattern that invariant
  exists to catch. The fallback now synthesizes a plain string node id
  instead of a synthesized `EntityId`.
- Regenerated the G20 example-catalog snapshot fixtures
  (`examples/case143`-`151`, `case181`) for the `schema_version` 28→29 bump
  (ADR-063 Phase 3 slices 3-4); updated three tests that hardcoded the
  prior literal `28` (`test_baseline_pinning.py`, `test_entity_id_carrier.py`,
  `test_serialization_roundtrip.py`) to compare against the live
  `SCHEMA_VERSION` constant (or `>=` where the test's own intent is a floor,
  not the exact current version) instead of a frozen literal; added
  `surface_graph_codec` to `tests/unit/storage/adr062_scope.py`'s
  `NON_ADR062_MODULES`, alongside its `entity_id_codec` sibling.
