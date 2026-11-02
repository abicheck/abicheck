### Fixed

- **A snapshot's graph section is now byte-reproducible across processes** —
  `surface_graph` node and edge rows are written sorted by node id and edge
  relation key instead of in-memory insertion order, so dumping the same
  library in two processes (with `SOURCE_DATE_EPOCH` set) gives identical
  bytes and digests. Decoding is unchanged and accepts any row order; no
  schema version change.
