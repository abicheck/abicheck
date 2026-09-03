### Added

- **Storage format v2, Phase 1's A1.1 object model (ADR-062)** — a new
  `abicheck/storage/package.py` module carries the `ProjectSnapshot` package's
  manifest/ref/object-store *object model*: `PackageManifest`/`VariantRef`/
  `ArtifactRef`/`ObjectRef` (the D6 manifest, variant, artifact, and
  section-object records, with duplicate/undeclared-membership validation and
  D5's insertion-order-independent canonical form) and `ObjectStore` (D7's
  digest-addressed `put`/`get`/`has` protocol, plus `InMemoryObjectStore`, a
  real, process-local reference implementation, which stores both JSON-shaped
  facts and raw binary payloads — a raw extractor artifact hashes via the new
  `abicheck.storage.canonical.raw_digest`, since `semantic_digest`'s JSON
  canonicalization cannot represent one at all). This is the object model
  only — no directory-backed store, `.tar.zst` transport, or writer exists
  yet, so nothing here changes what `dump`/`compare`/`scan` read or write:
  every existing snapshot, baseline set, and `BundleFacts` document stays
  byte-for-byte unchanged, and `ObjectStore` cannot yet wrap ADR-059's
  compressed-storage envelope from this layer (`storage/` may depend only on
  `model`, per ADR-061 D1).
