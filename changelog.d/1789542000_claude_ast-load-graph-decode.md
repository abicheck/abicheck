### Performance

- **Faster clang AST parsing, stored-graph decoding, and header-graph build.**
  The cold, warm (`load_cached_ast`), streamed and projection-sidecar parses
  of a clang AST now pause Python's cyclic garbage collector while
  `json.loads` builds the tree (`storage/acyclic_json.py`). A decoded JSON
  document has no reference cycles to collect, so each collection during the
  parse was wasted work. On a 246 MiB AST, the parse went from 1.2–1.8 s to
  0.85 s. Decoding a stored graph table (`decode_graph_table`) now builds
  nodes and edges directly and decodes each interned fact row once, instead
  of first building a per-entity legacy dict for every node and edge. Edges
  are also no longer resolved twice. Under the profiler, this took 3.6 s
  down to 1.9 s on a 24k-node, 56k-edge graph. The header-only graph now
  classifies each header path once and adds each header node once. The clang
  walker now builds a `_Decl` only for nodes it actually keeps, and looks up
  the anonymous-ordinal state only for scopes that have an anonymous child.
  Findings, verdicts, and graph nodes, edges and facts are unchanged. The
  stored graph's node order changes once, to AST document order (see Fixed).

### Fixed

- **Two cold dumps of the same headers now store the same header graph.**
  The header graph seeded its type nodes by iterating a `set` of qualified
  names, and built its unseeded `DECL_REFERENCES_DECL` sources from another
  `set`. Node order, and with it the stored graph's string and fact tables,
  therefore followed the process's `PYTHONHASHSEED`, so every cold dump of
  unchanged headers wrote a different snapshot. Both now follow AST document
  order. The header-graph projection sidecar schema is bumped to `/2`, so
  sidecars written in the old order are re-derived rather than reused.
