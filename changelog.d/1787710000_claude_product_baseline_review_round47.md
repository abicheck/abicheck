### Fixed

- **`abicheck.package.TarExtractor._safe_extract_zst_tar`**: bounding the
  external `zstd` CLI fallback's reader-thread queue (previous round) could
  leave the reader thread permanently blocked in `put()` after an abort
  (a timeout, or the decompression-bomb size limit) -- the consumer stops
  draining the queue the moment it raises, so a reader that has already
  produced a further chunk blocks forever, leaking a daemon thread and its
  queued chunks for the life of a long-running process. Every abort path
  now drains the queue and joins the reader thread before returning.
- **`abicheck.product_baseline.pack_product_baseline`**: a suffix-shaped
  symlink whose target is an in-tree *directory* (`plugins.so -> payload/`)
  matched the standalone-library-symlink fix's own name check and had its
  target hashed unconditionally, raising an uncaught `IsADirectoryError`
  instead of the documented `SnapshotError` contract. Packing now requires
  the target to be a regular file before treating it as library content.
