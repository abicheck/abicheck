### Changed

- **`abicheck.product_baseline.pack_product_baseline`**: documented a
  known gap (investigated, not fixed) where an *undeclared*,
  pre-existing empty directory holding OUTPUT is excluded from the
  archive the same way pure output-scaffolding is -- the two are
  structurally indistinguishable from content alone, and using
  existence to tell them apart would regress the existing determinism-
  across-reruns guarantee, since OUTPUT's own directory necessarily
  survives on disk after a first successful pack. Closing this needs a
  real declaration mechanism beyond `header_roots`, not a heuristic.
