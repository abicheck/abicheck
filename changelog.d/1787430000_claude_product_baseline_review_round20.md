### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: a
  canonical-fallback pair (SONAME/dylib-version bump, e.g. `libfoo.so.1`
  -> `libfoo.so.2`) fed `compare_bundle()` a per-library `DiffResult` still
  identified by the *old* side's bare filename — `run_compare()` always
  stamps `DiffResult.library` from the old side. `compare_bundle()`'s
  cross-DSO detectors index by that identity and then exclude it from
  their own sibling/consumer scan over the *new*-side metadata; since the
  two identities differed for a version-bumped pair, the provider's own
  new binary was never excluded and could be reported as a cross-DSO
  consumer of its own type/symbol change. Fixed by restamping
  `DiffResult.library` to the new side's bare filename before handing it
  to `compare_bundle()`.
- **`abicheck.product_baseline.compare_product_directories`**: the
  standalone-removal/addition fallback projected each side's unmatched
  libraries down to a `set` of bare filenames before iterating, so two
  distinct unmatched libraries sharing a basename in different
  directories (`plugins/a/plugin.so` and `plugins/b/plugin.so`) collapsed
  to a single finding even when *both* vanished, not just one. Fixed by
  iterating the unmatched relative-path keys directly instead of a
  deduplicated name set.
