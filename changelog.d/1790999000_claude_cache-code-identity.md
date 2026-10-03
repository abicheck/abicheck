### Fixed

- **Disk caches no longer serve output from a different abicheck build.** The
  snapshot cache (`~/.cache/abi_check/snapshots`), the L3 build-evidence cache
  and the L4 per-TU source-ABI cache keyed their entries on the inputs plus a
  hand-bumped version constant, so an extraction change that came without a
  matching bump kept serving the previous code's result for byte-identical
  inputs -- a reproducible debug build, for example, got the snapshot an older
  abicheck produced. Each key now also folds a content hash of the installed
  `abicheck` package (`storage.code_identity.abicheck_code_fingerprint`), so any
  change to the package, an upgrade or a different development checkout
  alike, is a cache miss. The castxml/clang header-AST cache, which stores the
  external tool's own output, is unchanged.
