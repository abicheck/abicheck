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

- **`compare` of stored snapshots skips the whole-snapshot content digest
  when the two sides provably differ.** The digest is used only to tell the
  user that the two inputs have identical content, and two snapshots with
  different verbatim-persisted fields (for example `created_at`) cannot be
  identical. So both full re-serializations are now skipped when such a
  field differs; identical inputs still get the warning. Separately, the
  snapshot encoder no longer walks the embedded build-source pack only to
  discard the result, and it looks up each dataclass's field names once per
  class. `snapshot_to_json` went from 2.1 s to 1.1 s on the header-graph
  memory fixture. ELF parsing now decodes `.dynsym` once per parse instead
  of twice, and decodes `.gnu.version` with a single `struct` call instead
  of one pyelftools parse per entry. Parsing 41 host libraries went from
  12.2 s to 5.3 s with identical metadata. End to end on the fixture,
  `compare` of two stored snapshots went from 13.8 s to 8.6 s and a warm
  `dump` from 13.5 s to 12.4 s, with byte-identical snapshots and reports.

### Fixed

- **Two cold dumps of the same headers now store the same header graph.**
  The header graph seeded its type nodes by iterating a `set` of qualified
  names, and built its unseeded `DECL_REFERENCES_DECL` sources from another
  `set`. Node order, and with it the stored graph's string and fact tables,
  therefore followed the process's `PYTHONHASHSEED`, so every cold dump of
  unchanged headers wrote a different snapshot. Both now follow AST document
  order. The header-graph projection sidecar schema is bumped to `/2`, so
  sidecars written in the old order are re-derived rather than reused.
