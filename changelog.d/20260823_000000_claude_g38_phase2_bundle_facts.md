### Added

- **Bundle-level comparisons can now be captured to a stored baseline
  (G38 Phase 2, an amendment to ADR-023).** `compare`'s directory/package
  fan-out gained `--bundle-facts-out PATH`, which persists the OLD side's
  per-library snapshots (plus the instantiation manifest, if any) to `PATH`
  as a `BundleFacts` file, alongside the ordinary live-vs-live comparison
  the invocation already performs — additive output, no change to any
  finding or exit code. The new `abicheck.bundle_facts` module exposes
  `capture_bundle_facts()`/`compare_bundle_from_facts()` and
  `abicheck.serialization` gains `save_bundle_facts()`/`load_bundle_facts()`
  (mirroring `save_snapshot`/`load_snapshot`'s plain/gzip/zstd envelope).
  `compare_bundle_from_facts()` reconstructs a live-equivalent
  `BundleSnapshot` from the stored per-library `AbiSnapshot.elf` metadata —
  no binaries read — and delegates to the same `compare_bundle()` a live
  directory comparison uses, so the two entry points can never
  independently drift; a dedicated parity test asserts they produce
  byte-identical findings for the same underlying facts. The `compare` CLI
  *consumer* half (feeding a stored `BundleFacts` file back in as an
  old-side operand) is not yet wired — see
  [Multi-Binary Releases](docs/use/multi-binary.md#comparing-against-a-stored-bundle-baseline-g38-phase-2)
  and the G38 plan's own implementation-status note for why that's
  deliberately deferred.
