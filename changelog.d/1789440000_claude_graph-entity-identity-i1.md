### Changed

- One graph node id per entity (evidence-entity-model Phase 1, invariant
  I1). Every producer that names a declaration or type in the header/L5
  source graph -- the header graph, the public-surface builder, the AST
  replay passes and the L4 source fold -- now takes its node id from
  `abicheck/model/graph_entity_identity.py`: a declaration's linker name
  (C linkage included), else `qualified#signature`, else an explicit
  `unresolved://` node; a type's qualified name. The public-surface builder's
  `declaration`/`type` nodes no longer duplicate the header graph's
  `source_decl`/`record_type` nodes (29,109 duplicate pairs on oneDAL), and
  the `declaration::`/`type::`/`typedef::` fallback ids are gone.
- Snapshot schema is now **v50** (`SourceGraphSummary.schema_version` 3,
  plus a persisted `identity_aliases` map, carried by the compact graph-table encoding). A pre-v50 snapshot still loads;
  comparing its graph against a v50 one reports the L5 layer as *not
  compared* on the coverage row (`abicheck/compare/source_graph_identity_scheme.py`) and as a warning, instead of diffing ids
  that name entities differently. Re-dump the older side to restore it.

### Fixed

- A C-linkage function's call/type-graph edges now reach its header-graph
  node (they were keyed `qualified#sha256:...` against `decl://<name>`).
- Unmangled overloads, castxml constructor/destructor placeholders, and
  same-leaf types in different namespaces (`ns::W`/`other::W`) are no longer
  collapsed onto one header-graph node.
