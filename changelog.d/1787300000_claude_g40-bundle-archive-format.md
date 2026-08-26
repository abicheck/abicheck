### Added

- **`save_bundle_facts`/`load_bundle_facts` support a content-addressed
  archive format (G40).** `save_bundle_facts(facts, path, format="archive")`
  writes a zip container (`abicheck.storage.bundle_archive`) holding one
  `manifest.json` plus one zstd-compressed, sha256-content-addressed blob per
  unique per-library `AbiSnapshot` (and, when present, the bundle's
  `InstantiationManifest`) — two libraries with byte-identical snapshots
  share one blob. `load_bundle_facts(path)`'s existing `format="auto"` now
  also sniffs the zip magic and reads it transparently; every existing
  plain-JSON `BundleFacts` file keeps loading unchanged, and
  `save_bundle_facts`'s own default stays `format="json"`. See the G40
  design plan (`docs/contribute/plans/g40-content-addressed-bundle-archive.md`,
  added in PR #866 — merge that PR first if this file isn't present yet on
  the branch you're reading this from).
