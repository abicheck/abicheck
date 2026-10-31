### Added

- **`dump` captures a whole release as a BundleFacts baseline** —
  `abicheck dump RELEASE/ -H include/ --bundle-facts-out base.json` writes the
  multi-library `BundleFacts` document a later `compare base.json NEW/` reads
  as its stored OLD side, without running a comparison first. `RELEASE` is a
  directory, a package archive or a stored `ProjectSnapshot` package. It
  writes the same document `compare OLD NEW --bundle-facts-out` writes for its
  OLD side, through the same producer; that `compare` flag stays.
