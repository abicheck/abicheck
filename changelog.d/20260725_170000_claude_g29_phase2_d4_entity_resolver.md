<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`EntityResolver` — USR-based canonical entity identity for the L5 source
  graph** (ADR-046 D4, scoped implementation): `abicheck/buildsource/
  entity_resolver.py`'s `EntityResolver.resolve(node)` computes a
  USR-preferring canonical identity for a `GraphNode` — reusing
  `entity_identity.CanonicalIdentity` (ADR-048) as its resolution source —
  and records it as an alias (`aliases[v1_id] = canonical_id`), with cross-
  producer identity collisions recorded as `EntityConflict` entries instead
  of silently overwritten. `GraphNode.id` generation itself is unchanged —
  this computes a *second*, richer identity alongside the existing v1 id,
  never in place of it. `SourceGraphSummary.entity_resolver` is populated
  only when a caller explicitly calls the new, opt-in
  `resolve_entities()` method; `SOURCE_GRAPH_VERSION` bumps 1 → 2 as a
  signal (nothing branches on it) — a v1 pack with no `entity_resolver` key
  still loads and compares correctly with no forced re-collection. See
  `docs/reference/source-graph-schema.md`'s "`EntityResolver`" section and
  ADR-046's "D4 implementation" section for the full scoping rationale
  (why this stops short of the originally sketched `GraphNode.id`-generation
  rewrite).
