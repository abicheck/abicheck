### Fixed

- **`abicheck.product_baseline.pack_product_baseline`**: an in-tree output
  parent's scaffold directory is now removed on every failure path after
  it's created, not just the empty-source rejection. Previously, a
  rejection reached later — a real source entry colliding with the
  reserved manifest member name, an invalid `zstd_level`, or any failure
  during archive writing — left the scaffold directory behind on disk. A
  retry after fixing the underlying problem then treated that leftover
  directory as pre-existing, real content instead of reproducing an
  equivalent check.
