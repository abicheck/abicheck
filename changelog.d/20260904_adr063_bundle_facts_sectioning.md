### Added

- **`storage.import_bundle_facts`/`storage.import_baseline_set`** — ADR-063
  Track C 8B (A1.4): a persisted `BundleFacts` document and an
  `actions/baseline`-produced baseline set can now be imported into (and
  exported back out of) the `ProjectSnapshot` package's sectioned
  representation, folding each per-library `AbiSnapshot` through the
  existing v1-v25 import adapter and attaching each container's own
  composition facts (manifest, filesystem aliases, library filenames; a
  baseline set's own `manifest.json` metadata) to a new `VariantRef.sections`
  field. No existing CLI or stored file changes shape — this is a typed
  storage-layer primitive only.
