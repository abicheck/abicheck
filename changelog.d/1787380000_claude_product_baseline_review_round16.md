### Fixed

- **`abicheck.bundle.build_bundle_snapshot_from_metadata`**: resolution is
  now independent of ambient filesystem state. `_compute_resolution_graph`
  gained a `probe_filesystem` parameter, and the metadata-only entry point
  now always calls it with `probe_filesystem=False` — previously, a
  caller-supplied `paths` argument (given only for display purposes, e.g.
  a library's own `.name`/`.parent`) could make the resolution graph
  silently depend on what those paths happened to resolve to on the real
  filesystem (a symlink or hard-link alias present in the current working
  directory), even though this function's whole contract is resolving
  purely from already-parsed `ElfMetadata`.
- **`abicheck.product_baseline.pack_product_baseline`**: header-root
  containment validation now runs before the output directory's scaffold
  directories are created. Previously, a header root that happened to
  match the freshly-created (and still-empty) output scaffold directory
  could pass validation, only to analyze an empty directory instead of
  being rejected outright.
