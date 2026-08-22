### Added

- **`abicheck.bundle.build_bundle_snapshot_from_metadata`** — the primitive
  behind `build_bundle_snapshot`, split out so a caller holding
  already-parsed `ElfMetadata` (e.g. every ELF `dump`'s own
  `AbiSnapshot.elf`) can build a real `BundleSnapshot` — cross-DSO
  `DT_NEEDED`/version-table analysis included — without any binary on disk.
  `build_bundle_snapshot(dict[str, Path])` is now a thin wrapper: parse each
  path into `ElfMetadata`, then delegate. Purely additive — every existing
  caller's behavior is unchanged. This is the primitive a future
  snapshot-first product baseline (storing N ABI snapshots instead of N raw
  binaries) would build cross-library bundle analysis on, without needing
  the old side's binaries available on disk the way `pack_product_baseline`
  currently requires.
