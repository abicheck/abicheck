### Fixed

- **`abicheck.product_baseline.compare_product_directories`**: the bundle-map
  alias-normalization rekey (round 24) could overwrite an unrelated old-side
  library's slot in `old_bundle_map` when its bare filename happened to
  collide with a canonically-paired library's rekey target, silently
  evicting it from `old_snapshot` entirely. Reachable when the occupant's
  own canonical identity comes from a different signal than its filename
  (e.g. real PE content under a non-`.dll` name), since that keeps it out
  of the canonical-pairing ambiguity check that would otherwise block the
  collision. The rekey now skips whenever the target bare name is already
  occupied by a different library.
