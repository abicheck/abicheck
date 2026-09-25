### Added

- Coverage-aware edge queries (evidence-entity-model Phase 4, invariant I4,
  "absence is typed"). `compare.edge_query.EdgeEvidence.query(edge_kind,
  subject, target=..., scope=...)` answers `present`, `proven_absent` or
  `unknown` for every public-surface graph edge kind (`declares`,
  `references`, `declares_linker_name`, and the Phase 2 `exports` and
  `debug_type_of` joins) and the L5 source-graph kinds, together with the
  per-producer coverage records the answer rests on. `proven_absent` requires
  the producer of the edge kind to have covered the queried scope, so an
  extractor that never ran, ran over part of the scope (a narrowed L5 pass,
  filtered dependency headers), or failed only ever answers `unknown`. The
  vocabulary lives in `model/edge_coverage.py`; the L5 per-pass trust table
  in `model/source_graph_coverage.py`.
