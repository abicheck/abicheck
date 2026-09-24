### Performance

- **A stored snapshot's header graph is decoded on first use, not at load.**
  `AbiSnapshot.surface_graph` (and its `build_source.source_graph` alias) now
  holds the undecoded `graph` section until something reads it, decoding it
  once under a lock (storage-format-v2 Phase 2, A2.1; evidence-entity-model
  Phase 5a). Equality, pickling, deep copies and re-saved bytes are
  unchanged, and a corrupt `graph` section raises the same error it did
  before, at the first access, instead of reading as an empty graph. A
  `compare --depth binary`/`--depth debug` of two header-graph snapshots no
  longer decodes either graph. A default `compare` still reads both graphs
  (the L5 source-graph diff is deliberately not gated; see ADR-062 D8's
  implementation note). Saving a snapshot also no longer encodes its header
  graph twice.
