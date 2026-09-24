### Changed

- **A stored header graph now holds observed evidence only.** The graph
  table no longer stores anything the loader rederives -- each node's and
  edge's `attrs`/`provenance`/`confidence` (rebuilt from its facts),
  `graph_id`, and the finalize-owned `coverage` counts -- and never stores a
  projection of the snapshot's own records: facts from the public-surface
  builder (`compare/surface_graph.py`'s `declaration`/`type`/`symbol` nodes
  and `declares`/`references`/`declares_linker_name` edges, now stamped with
  the `public_surface_facts` producer) are dropped on save, and a reader
  that needs them rebuilds them from the records, getting what it had
  before the save (evidence-entity-model Phase 5c). A graph built with and
  without those projections now saves to identical bytes.
