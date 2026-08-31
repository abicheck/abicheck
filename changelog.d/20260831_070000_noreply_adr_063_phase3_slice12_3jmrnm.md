### Added

- **ADR-063 Phase 3 (D5), slice 12: shared `SourceGraphSummary` assembly.**
  `service_header_graph_attach._attach_header_graph()` -- the workflow-layer
  step that already builds a `SourceGraphSummary` via
  `buildsource.header_graph.build_header_only_graph()` and attaches it as
  `AbiSnapshot.build_source.source_graph` whenever headers are parsed -- now
  also folds `compare.surface_graph.build_public_surface_facts()`'s own
  declaration/type/header/symbol facts into that *same* graph instance and
  assigns it to the new `AbiSnapshot.surface_graph` field, rather than
  constructing two independent summary objects that happen to agree. The
  `storage/surface_graph_codec.py` encode/decode identity-dedup this slice's
  own field relies on (Phase 3 slice 3-4) is exercised for real for the
  first time here.

### Known gaps

- `compare/surface_graph.py`'s Phase 3 node ids
  (`canonical_key(occurrence_id)` / `approx::`/`typedef::` string fallbacks)
  and `buildsource/header_graph.py`'s pre-existing L5 node ids
  (`decl://<normalized identity>`/`type://<normalized identity>`) are two
  independent namespaces today. Slice 12 shares one `SourceGraphSummary`
  instance between the two builders (verified by
  `tests/test_service_header_graph_attach_surface_graph.py`), but the two
  id schemes do not currently collide/dedup onto one node for a declaration
  both builders see. Reconciling them is a real, separate, deeper migration
  -- either builder adopting the other's identity scheme -- left for a
  later phase; see `compare/surface_graph.py`'s own module docstring for
  the full account.
