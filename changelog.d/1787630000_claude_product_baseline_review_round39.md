### Fixed

- **`abicheck.product_baseline._discover_library_map`**: a library-shaped
  self-referential symlink loop (`loop.so -> loop.so`) was silently
  discovered as though it were a real library once its identity lookup
  failed -- comparing a product containing nothing but a loop against an
  empty product previously produced a spurious `bundle_library_removed`
  finding instead of the invalid product being rejected outright. A loop
  is now detected via the same `stat(ELOOP)` check the packing path
  already uses and raises `SnapshotError`, both from
  `compare_product_directories()` and from `unpack_product_baseline()`'s
  own extracted-inventory cross-check (both call this shared discovery
  function).
- **`abicheck.product_baseline.unpack_product_baseline`**: the
  extracted-library-inventory cross-check compares by filesystem identity
  (`dev`, `ino`) so a legitimate dev-symlink alias (never given its own
  manifest entry) doesn't false-positive. That comparison is too loose
  for a *hardlink* alias, which -- unlike a symlink -- does get its own
  `LibraryEntry` when honestly packed: an archive omitting just the
  hardlink alias's entry (while keeping the first-archived copy's) still
  shares `(dev, ino)` with the still-declared library, so the identity
  check silently accepted it as "already declared" even though the
  alias's own path was never independently verified. A non-symlink
  library file is now checked by its declared path string instead of
  identity; a symlink alias keeps the existing identity-based check.
