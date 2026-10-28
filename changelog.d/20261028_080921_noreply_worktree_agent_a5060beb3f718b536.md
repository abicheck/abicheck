### Fixed

- **Stored records round-trip their own values.** Readers in `model/` no longer
  replace a stated empty value with a default or drop it: `OwnershipRules` and
  `ExtractionScope` read an explicit `dependency_evidence: ""` as `"full"`
  (a default standing in for evidence the document never claimed), and
  `ReleasePublicSurface` read an empty `unresolved_reason` as absent. Values
  that were shared with, or copied shallowly from, a document are now deep
  copies: `GraphFact`, `GraphNode`, `GraphEdge`, `FactConflict`,
  `ExtractionScope` (`prefilter`) and `SourceGraphSummary` (`coverage`,
  `external_graph_refs`) no longer let an edit to a returned or consumed
  dictionary reach the object. A generic sweep
  (`tests/test_storage_roundtrip_sweep.py`) now checks every storage and model
  `to_dict`/`from_dict` pair for these properties.
