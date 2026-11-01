### Added

- **Published JSON Schemas for stored release facts** — the `BundleFacts`
  document that `dump RELEASE --bundle-facts-out` writes, and the
  `manifest.json` of its zip archive form, now have published schemas
  (`bundle_facts.schema.json`, `bundle_facts_archive_manifest.schema.json`)
  alongside the other machine-readable contracts. They encode the same
  version rules the reader enforces.
