### Added

- **ADR-063 Phase 3 (D5), slices 3-4: `AbiSnapshot.surface_graph` (schema
  v29).** A new, unconditional field carrying the one evidence graph the
  upcoming public-surface builder and the existing L5 source-graph builder
  will both write into — never gated on `--sources`/`--build-info`
  evidence, unlike `build_source`. Persisted through its own `to_dict()`
  encoding (`storage/surface_graph_codec.py`, mirroring
  `storage/entity_id_codec.py`'s shape), not `dataclasses.asdict()`'s
  naive recursion: when `build_source.source_graph` is the identical
  object, it is written once and the alias is restored on load; a legacy
  document (no top-level `surface_graph` key) is never aliased forward,
  since its nested L5 graph predates the public-surface builder and lacks
  the edges a query would need.
