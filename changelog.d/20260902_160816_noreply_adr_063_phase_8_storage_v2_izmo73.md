### Changed

- **`dump`/`compare`/`scan`'s snapshot JSON is now sectioned and
  independently versioned by default** (ADR-062/ADR-063 Phase 8): every
  `-o`/`--output`/stdout write splits the document across D8's named,
  independently-versioned sections (`binary`, `declarations`, `types`,
  `layout`, `debug`, `build`, `graph`, `provenance`), each structurally
  validated on read, packaged as a single JSON document — no new file, no
  new flag, no directory. `snapshot_from_dict`/`load_snapshot` still read
  an older flat `.abi.json` a prior build wrote, unchanged. `compare` and
  `scan --against` also still accept a directory-backed `ProjectSnapshot`
  package as an input path (produced via the typed
  `project_snapshot_legacy.write_legacy_snapshot_package` API), resolved
  into the identical in-memory snapshot every other input shape resolves
  to.
