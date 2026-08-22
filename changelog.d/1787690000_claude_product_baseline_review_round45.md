### Fixed

- **`abicheck.package.TarExtractor._safe_extract_zst_tar`**: the round-44
  streaming rewrite of the external `zstd` CLI fallback (for the
  decompression-bomb size bound) replaced the previous
  `subprocess.run(..., timeout=120)` call with a plain blocking
  `proc.stdout.read()` with no deadline at all -- a stalled or hung
  `zstd` process could block extraction indefinitely. The read is now
  offloaded to a daemon thread, with the main loop polling a queue
  against the same overall 120s deadline the old call enforced.
- **`abicheck.product_baseline.compare_product_directories`**: two
  genuinely distinct libraries sharing a bare filename in different
  directories (e.g. `plugins/a/plugin.so` and `plugins/b/plugin.so`)
  were silently collapsed to one entry when building the bare-name-keyed
  bundle maps `compare_bundle()` uses for its own dependency-edge
  analysis, with no error or warning -- a real intra-bundle dependency
  on, or ABI drift in, the discarded library was invisible to
  `compare_bundle()`. Standalone add/remove detection already handles
  this shape correctly via a separate, collision-free path, so the
  collision is not rejected outright (several existing tests pin the
  duplicate-basename shape as intentionally supported); a `UserWarning`
  is now raised instead, naming both colliding paths, so the gap in
  bundle-level graph analysis is at least visible.
