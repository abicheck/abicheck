<!--
A new changelog fragment. See changelog.d/README.md for the workflow.
-->

### Added

- **`Change.impact_assessment` — one producer constructs `ImpactAssessment`
  directly** (ADR-052 D2 follow-up, Slice 8, scoped implementation):
  `internal_leak.py`'s two leak-finding builders
  (`INTERNAL_TYPE_LEAKS_VIA_PUBLIC_API`/`INTERNAL_SYMBOL_REQUIRED_BY_PUBLIC_API`)
  now attach a producer-built `ImpactAssessment` to the new, additive
  `Change.impact_assessment` field. `impact.engine.assess_change` reuses
  its evidence fields (reachability/proof-path/confidence/evidence-category/
  correlated-change-kind) instead of re-deriving them, while always
  recomputing `decision`/`root_cause_id` fresh — those depend on
  suppression/pattern-modulation state that can change after construction.
  Verified safe by a pipeline-ordering audit: `post_processing.
  MarkReachability` is the only step that mutates a `Change`'s
  reachability/evidence fields, and it runs before these findings even
  exist. The other four producer modules D2's original decision named
  (`post_processing.MarkReachability` especially) remain unmigrated — see
  ADR-052's "Slice 8" section for the full scoping rationale.

### Fixed

- **Reachability-walk precision could stick to the first-discovered route**
  (`internal_leak._consumer_compiled_reachability`, CodeRabbit review): a
  plain BFS marked a node "seen" permanently on first discovery, so a node
  reached first via a virtual/function-pointer call stayed labeled
  `overapprox` even when a later, equal-or-longer *exact* route to the same
  node existed elsewhere in the graph — an edge-ordering artifact, not a
  real precision limit. Rewritten as a 0-1 BFS (exact edges relax to the
  front of the queue, degraded edges to the back), so an exact route always
  wins over a degraded one to the same node, regardless of discovery order
  or path length — matching the existing exact-beats-overapprox proof-path
  preference (ADR-046 D6).
- **`_build_alternative_path`'s `is_direct` counted node *and* edge steps**
  (`impact/engine.py`, CodeRabbit review): `structured_proof_path`'s shape
  is `node, edge, node, ...`, so a single-hop (direct) alternative path
  already has 3 raw entries — the old `len(raw_steps) <= 1` check therefore
  marked every direct alternative as transitive. Now counts edge-type steps
  only.
- **`SourceGraphSummary.from_dict` crashed on an explicit
  `"entity_resolver": null`** (CodeRabbit review): `dict(None)` raised
  before `EntityResolver.from_dict`'s own defensive `.get()` parsing could
  run, so a hand-edited pack with a null (not absent) `entity_resolver` key
  failed to load.
- **`resolve_entities()` could serve a stale canonical id after `add_node`
  merged stronger identity evidence into an already-resolved node**
  (CodeRabbit review): `EntityResolver.resolve` is idempotent per node id
  by design, so calling `resolve_entities()` a second time after a node
  gained a USR (or other stronger identity signal) via a merged
  registration kept returning the canonical id computed from the node's
  earlier, weaker attrs. `resolve_entities()` now starts from a fresh
  `EntityResolver` on every call.
- **`graph_impact._node_is_public` read only `attrs`, not `resolved` first**
  (CodeRabbit review): inconsistent with `_edge_is_overapprox`'s
  `resolved or attrs` read order in the same module — a node whose
  visibility was contributed by a fact and only materialized in `resolved`
  (not yet mirrored into `attrs`) could be misclassified non-public. Now
  matches the same read order.
