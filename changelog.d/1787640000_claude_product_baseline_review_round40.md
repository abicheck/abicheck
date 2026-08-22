### Fixed

- **`abicheck.product_baseline.unpack_product_baseline`**: the round-39
  hardlink-alias inventory check compared each extracted library against
  `_discover_library_map()`'s own *deduplicated* map -- but that map
  keeps only the first-sorted path per filesystem identity, so an
  undeclared hardlink alias sorting *after* a declared library's own
  symlink alias (both sharing the same `(dev, ino)`) was invisible to
  the check entirely: the symlink alone survived dedup, was checked and
  passed by identity, and the undeclared hardlink was never examined.
  A new `_discover_library_map`-sibling walk,
  `_iter_discovered_libraries()`, yields every raw discovered path
  (not deduplicated), which the inventory check now iterates directly
  so no path -- regardless of what else shares its identity -- goes
  unchecked.
