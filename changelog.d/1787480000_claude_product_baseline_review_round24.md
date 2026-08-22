### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: a
  canonically-paired library (SONAME-major bump, dylib-version bump,
  PE case-fold — or a discovery-dedup representative mismatch, e.g. a
  dev symlink `libfoo.so -> libfoo.so.1` present only in the old tree, so
  `_discover_library_map`'s `(dev, ino)` collapse picks a different bare
  filename on each side) could reach `compare_bundle()`'s real bundle
  snapshots under two different bundle-level identities, since
  `old_bundle_map`/`new_bundle_map` were built independently of the
  already-computed canonical pairing. `compare_bundle()`'s own snapshot
  diff then read the unchanged library as a spurious removal+addition —
  or a `BREAKING` verdict, when a surviving sibling imports it. Both
  sides now share one bundle identity (the new side's bare filename,
  matching the `DiffResult.library` convention this module already
  established) for every canonically-paired library.
