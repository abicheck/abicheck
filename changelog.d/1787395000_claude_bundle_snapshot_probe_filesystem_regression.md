### Fixed

- **`abicheck.bundle.build_bundle_snapshot`**: fixed a real-caller
  regression introduced by the same PR's earlier
  `build_bundle_snapshot_from_metadata` filesystem-independence fix.
  Making the metadata-only function unconditionally resolve with
  `probe_filesystem=False` also silently disabled filesystem alias
  probing (symlink targets, hard-link aliases) for `build_bundle_snapshot`
  itself — the real-path wrapper every live comparison
  (`compare_product_directories`, `compare-release`, ...) uses — since it
  delegates to the same function. `build_bundle_snapshot_from_metadata`
  now accepts a `probe_filesystem` parameter (default `False`, preserving
  the metadata-only fix), and `build_bundle_snapshot` passes `True`
  explicitly to restore its own pre-existing live-filesystem behavior.
