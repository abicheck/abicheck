### Changed

- **Snapshots store the header graph as compact, interned tables (schema
  v49).** `AbiSnapshot.surface_graph` is now written in
  `storage/graph_table_codec.py`'s columnar encoding: every id, kind, label,
  producer and attribute key is stored once in a string table, node and
  edge columns hold indexes into it, and attribute dicts and producer facts
  are deduplicated. The fields the loader always recomputed (`indexes`,
  `resolved`, `conflicts`, `occurrences`) are no longer written. On a real
  oneDAL `libonedal_core` dump the graph section shrinks from 79 MB to under
  10 MB. A loaded graph is the same object graph as before, and pre-v49
  snapshots still load unchanged; a pre-v49 abicheck cannot read a v49
  snapshot, hence the schema bump (evidence-entity-model Phase 5b; ADR-063
  D5 amendment).
