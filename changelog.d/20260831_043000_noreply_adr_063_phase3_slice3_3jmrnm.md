### Added

- **ADR-063 Phase 3 (D5), slice 5: `compare.surface_graph.build_public_surface_facts`.**
  Registers this phase's own node/edge kind vocabulary
  (`declaration`/`type`/`header`/`symbol`; `declares`/`references`/
  `exports`) and populates real graph facts from L0-L2 snapshot data alone
  — reusing `model.graph_facts`'s `GraphNode`/`GraphEdge` primitive
  directly, never a second dataclass hierarchy. A declaration/type node's
  id is `canonical_key(occurrence_id)` with an empty disambiguator,
  falling back to an approximate identity (synthesized from the flattened
  qualified-name string) when a declaration's parse-time `entity_id`
  carrier is unpopulated. `compare/` stays within its own `model`-only
  import direction — no `buildsource`/`surface.py`/`export_surface.py`
  dependency.
